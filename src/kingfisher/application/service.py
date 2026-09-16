"""The application service: wired once, then asked to run things.

Construction is not per-request, and the trade is measured rather than guessed: 8.1ms
median and 9.2ms p95 to build an unrestricted agent, of which 7.2ms is
`create_deep_agent` compiling the graph. Against a turn of 1.5-1.9s that is under 1%.

  subagent      +5-6ms   each compiles its own graph; the range is the delegate
  custom tool   +0.47ms  linear to at least 50, measured in August
  middlewares   +0.03ms
  skill          0.0ms   sixteen measure the same as none
  deny rule      0.0ms   a hundred measure the same as none

Every figure is *per item added to an otherwise identical build*, which is the only
form that transfers. The costs are additive and the total stays small: 10 tools, 5
middleware, 20 deny rules and 2 subagents predicted 20.8ms and measured 21.6ms.

Re-measured 2026-09-03. Construction is CPU-bound Python and does not parallelise --
about 100 builds a second per process, worker threads slightly worse -- so the
ceiling is roughly 150 concurrent turns, or 34 if every one activates eight
subagents. Above that, a cache keyed on session *and* capabilities *and* a
fingerprint of the definitions is the thing to reach for; the fingerprint because
uploads change what a session offers between turns.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from time import monotonic, time
from typing import TYPE_CHECKING, Any

from kingfisher.application import access
from kingfisher.application import config as config_module
from kingfisher.application.disposal import Disposal
from kingfisher.application.origins import Origins
from kingfisher.application.reporting import (
    delegate_only,
    opening_events,
    withheld_by_kind,
)
from kingfisher.application.sessions import Sessions
from kingfisher.application.turn import (
    Admitted,
    Prepared,
    consume,
    out_of_steps,
    overrun,
    turn_message,
)
from kingfisher.config import Config, ConfigError
from kingfisher.domain.access import (
    AccessError,
    AccessReport,
    Held,
    SourceIds,
    reaches,
)
from kingfisher.domain.capabilities import (
    UNRESTRICTED,
    Capabilities,
    CapabilityError,
)
from kingfisher.domain.ports import SessionStore
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.domain.session import (
    Session,
)
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.activation import (
    defined_subagents,
    indistinct_delegates,
)
from kingfisher.infrastructure.harness.agent import (
    build_agent,
    builtin_tool_names,
)
from kingfisher.infrastructure.harness.backend import BackendFactory
from kingfisher.infrastructure.harness.checkpointing import (
    build_session_checkpointer,
    release_checkpointer,
)
from kingfisher.infrastructure.harness.interpreter import release_interpreter
from kingfisher.infrastructure.harness.middleware import (
    MiddlewareFactory,
    refuse_unbuildable_middleware,
)
from kingfisher.infrastructure.harness.runlog import JsonlRunLogger, log_path
from kingfisher.infrastructure.session_store import (
    TRANSCRIPT,
    LocalSessionStore,
    keep_from,
    read_transcript,
    write_transcript,
)
from kingfisher.infrastructure.wiring import store_named
from kingfisher.infrastructure.workspace.layout import ensure_layout
from kingfisher.infrastructure.workspace.permissions import protect_data
from kingfisher.infrastructure.workspace.placement import check_placeable, place_data, place_inputs
from kingfisher.infrastructure.workspace.seeding import SEED_HINT, STARTER_AGENT
from kingfisher.infrastructure.workspace.sessions import (
    LocalSessionDirs,
    LocalSessionRoot,
    claim_path,
    collect_artifacts,
)
from kingfisher.infrastructure.workspace.snapshots import (
    AGENT_SNAPSHOT,
    agent_snapshot,
    agent_started_with,
    remember_agent,
)
from kingfisher.kinds.agents.reading import read
from kingfisher.kinds.agents.spec import AgentSpec

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.domain.ports import (
        CommandRunner,
        SessionDirs,
        SessionRoot,
        ThreadStore,
    )


#: `kingfisher.origins`, and deliberately not `kingfisher`.
logger = logging.getLogger("kingfisher.origins")

#: "Nothing was supplied", distinct from `None`, which is a deliberate choice to
#: run without a checkpointer at all.
_UNSET: Any = object()


def _session_store(supplied: SessionStore | None, cfg: Config) -> SessionStore | None:
    """Which store this deployment gets: the one passed, the one named, or none."""
    if supplied is not None:
        return supplied
    if cfg.session_store_factory is not None:
        return store_named(
            cfg.session_store_factory,
            setting="KINGFISHER_SESSION_STORE_FACTORY",
            port=SessionStore,
        )
    if cfg.session_store is not None:
        return LocalSessionStore(cfg.session_store)
    return None


#: What a provider answers with when it will not accept the credentials. Matched
#: on the exception's own `status_code` rather than its class, so one branch
#: covers every adapter: `anthropic` and `openai` both raise their own
#: `AuthenticationError`, and the presentation layer may import neither.
UNAUTHORIZED = 401


def refused_credentials(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 401 as the configuration error it is, or `None` for anything else.

    Named rather than hinted at, and the variable is the whole point: on a 401 the
    YAML is right and the key it points at is not, so a message that can only say
    "authentication failed" sends the reader to the file where everything already
    looks correct. The sentence about the shell is there because `load_dotenv` runs
    with `override=False` -- an exported value beats `.env`, which is how a key three
    lines from the reader's eye is not the one that was sent.

    The endpoint is found by matching the failed request's URL rather than by asking
    for the default: a delegate may run somewhere its parent does not, and naming the
    default endpoint for a 401 raised by another one would be confidently wrong.
    """
    if getattr(exc, "status_code", None) != UNAUTHORIZED:
        return None
    url = str(getattr(getattr(getattr(exc, "response", None), "request", None), "url", ""))
    named = [
        (name, endpoint)
        for name, endpoint in cfg.models.endpoints.items()
        if url.startswith(endpoint.base_url)
    ]
    where = f"endpoint {named[0][0]!r}" if named else "the model endpoint"
    supplied = f" from {named[0][1].key_env}" if named and named[0][1].key_env else ""
    return ConfigError(
        f"{where} rejected the key{supplied} (401). A value exported in the shell "
        f"overrides the one in .env, so check the environment before the file"
    )


class Kingfisher(Sessions, Disposal):
    """A configured kingfisher. Construct once; call `run` or `stream` per request."""

    def __init__(  # noqa: PLR0913 -- the composition root; each argument is one
        # collaborator a deployment or a test substitutes, and folding them into
        # a parameter object would hide exactly what is substitutable.
        self,
        cfg: Config | None = None,
        *,
        dirs: SessionDirs | None = None,
        # A store, or a factory given a session directory, or nothing for the
        # default -- see `_checkpointer_for`. The union is the contract, so it
        # is written here rather than left for a reader to infer from a branch.
        threads: ThreadStore | Callable[[Path], Any] | None = None,
        # Where a session's files go when the machine may not keep them. `None`
        # means the session directory is the only copy, which is what every
        # deployment has had until now and stays correct wherever the host is
        # allowed to hold data.
        sessions: SessionStore | None = None,
        session_root: SessionRoot | None = None,
        runner: Callable[[Path], CommandRunner] | None = None,
        backend: BackendFactory | None = None,
        catalogue: Definitions | Mapping[str, Path] | None = None,
        grants: Capabilities | None = None,
        middlewares: Mapping[str, MiddlewareFactory] | None = None,
        graph: Any | None = None,
    ) -> None:
        self.cfg = cfg or config_module.config_from_env()
        config_module.enforce_local_only_tracing()

        # Only what sessions share. Each session's own layout is made per
        # request, because its path is not known until the request names it.
        self.workspace: Path = ensure_layout(
            self.cfg.workspace, authored=self.cfg.authored_files
        )

        # Where the reviewed definitions are read from, settled once. Omitted,
        # it is the catalogue directories `cfg` names -- one per definition kind,
        # which `DEFINITION_KINDS` is the list of -- and that is what it has
        # always been; supplied, a deployment has staged them somewhere itself
        # and this
        # is the only place that has to know. Resolved here rather than per
        # request so a deployment that fetches them pays once, and so a
        # catalogue that cannot be read fails at startup rather than serving an
        # agent that has quietly been told about nothing.
        # Read now rather than on the first turn: a definition that will not
        # parse is a wiring mistake, and this is the last moment it is cheap
        # to say so. `--list` deliberately does not do this -- see `warm`.
        self.catalogue: Definitions = resolve_definitions(self.cfg, catalogue).warm()
        # The one refusal `warm` cannot make for itself: whether a workspace tool
        # wears a built-in's name is only answerable from an assembled graph, and
        # `warm` has no `Config` to assemble one with. Left to the first request
        # that touched tools until now -- so a deployment started, said it was
        # fine, and refused the turn somebody was already waiting on.
        #
        # Only when the workspace defines tools, because nothing else can shadow.
        # That is what keeps it off every build with an empty catalogue, and the
        # cost where it lands is one compiled graph: about 10ms, once, against a
        # turn of 1.5-1.9s.
        if self.catalogue.tools.found:
            builtin_tool_names(self.cfg, self.catalogue)

        # Injected, or derived from configuration, or nothing -- the same order
        # `catalogue` follows and for the same reason: derive from `cfg`, never invent.
        self.sessions_store: SessionStore | None = _session_store(sessions, self.cfg)
        self.dirs: Any = dirs if dirs is not None else LocalSessionDirs()
        # Where a session's files are for the length of a turn. The default keeps them
        # under the workspace and leaves them there, which is what this did before there
        # was a port for it; a deployment whose tree exists only while a turn runs
        # supplies its own and gets the release for free, because the turn is what
        # closes it.
        self.session_root: SessionRoot = session_root or LocalSessionRoot(self.workspace)
        # A callable, and only a callable. A runner is built for one turn --
        # kingfisher's own Landlock fence is, because its policy is generated
        # from the session -- and a shared instance could not know which session
        # it was running for, would be one fence for every tenant where the
        # runner *is* the isolation, and would be called from several threads at
        # once because turns overlap. A deployment with one to share writes
        # `lambda session_dir: shared`: a line at the call site rather than a
        # second shape here forever. `threads` takes both and needed a second
        # attribute to remember which it was given.
        if runner is not None and not callable(runner):
            msg = (
                "runner is built per turn, so it takes a callable: pass "
                "`lambda session_dir: your_runner` if you have one to share"
            )
            raise TypeError(msg)
        self._runner = runner
        # A callable for the reasons above, and one more that is this parameter's
        # own: a backend is rooted at a session directory, so a single instance
        # shared by every session would be one filesystem for every caller. A
        # deployment separating its callers by *where their files are* -- which is
        # what replacing the backend is usually for -- would have written the leak
        # it was replacing the backend to avoid, and nothing about the call site
        # would look wrong.
        if backend is not None and not callable(backend):
            msg = (
                "backend is called per turn with the session it is for, so it takes a "
                "factory rather than a backend: pass `default_backend`, or "
                "`lambda *a, **kw: your_backend` if you really have one to share -- "
                "but a backend is rooted at a session, so sharing one is sharing a "
                "filesystem between callers"
            )
            raise TypeError(msg)
        self._backend = backend
        # Three shapes, and the difference is who owns the connection. An instance is a
        # shared store the deployment made and manages; a callable is a factory this
        # service calls per session and closes after the turn; `None` means the default,
        # which is `InMemorySaver` and holds nothing after the turn that made it.
        self.threads: Any = threads
        self._shared: Any = threads if (threads is not None and not callable(threads)) else None
        # What this deployment permits, before any request asks for anything.
        # Unrestricted by default, so a single-caller deployment is unaffected;
        # a service in front of many callers sets it, and `intersect` can only
        # subtract, so no request can widen past it.
        self.grants: Capabilities = grants if grants is not None else UNRESTRICTED
        # What a definition may name in its `middlewares:` field. Empty by
        # default, so any such line fails loudly until a deployment wires one --
        # kingfisher cannot define these, only a deployment knows what its
        # middleware is. Registering is not the same as permitting: `grants`
        # still clamps which registered names a request may reach.
        self.middlewares: Mapping[str, MiddlewareFactory] = middlewares or {}
        # Walked here rather than when a definition names one. An entry nothing
        # can build is a fact about this deployment's own code, true before any
        # request arrives and true of entries no definition names yet -- so the
        # deployment hears it where it wired the registry, not from the first
        # caller unlucky enough to reach the wrong name. `_instantiate` keeps
        # its own guard for `build_agent`, which takes a registry directly.
        refuse_unbuildable_middleware(self.middlewares)
        # Exactly one answer to what filesystem a turn runs against, said at
        # construction for the reason the catalogue is read there: it is a wiring
        # mistake, and this is the last moment it is cheap to say so.
        #
        # Two of them is somebody's wiring silently discarded -- a pre-built graph
        # already holds a backend, and `_graph_for` returns it without ever calling
        # the factory. None of them used to mean kingfisher picked one, and the
        # reason it no longer does is that the backend is the sandbox: it wraps every
        # command in `sandbox-exec` or Landlock, refuses host paths, and carries the
        # route table a read-only rule is only legal against. Inheriting that in
        # silence was never unsafe -- the default is the strict option, and still is
        # -- but it meant a deployment could wire the whole service without learning
        # there was a boundary at all.
        if graph is not None and backend is not None:
            msg = (
                "graph= and backend= are two answers to what filesystem a turn runs "
                "against, and a pre-built graph already carries one: pass the graph, "
                "or pass backend and let kingfisher build the graph"
            )
            raise ValueError(msg)
        if graph is None and backend is None:
            msg = (
                "kingfisher does not pick the filesystem its agents run on: pass "
                "backend=default_backend for the one it used to build for you, a "
                "factory of your own for something else, or a pre-built graph that "
                "already carries one"
            )
            raise ValueError(msg)
        self._graph = graph
        # There is nothing to reconcile, and that is the shape of the design rather than
        # an omission. Audiences live in the definitions, so a definition *is* the asset
        # it is about -- there is no such thing as a line naming something the workspace
        # does not offer, and a definition naming a tool that does not exist was already
        # refused by `Offering.refuse_unknown` long before any of this.
        self.access: SourceIds | None = self.cfg.access
        self.access_report: AccessReport = AccessReport()
        if self.access is not None:
            # One walk of the definitions, not three. `defined_subagents` reads a
            # directory, and asking it once per question is how this came to do it three
            # times at every startup.
            kinds = (
                ("agent", self.catalogue.agents.specs),
                ("subagent", defined_subagents(self.cfg, catalogue=self.catalogue)),
            )
            # Refusals first: a typo makes a line both undeclared and narrowing,
            # and reported as a narrowing it would explain the wrong fault.
            access.refuse_undeclared(*kinds, vocabulary=self.access)
            self.access_report = access.audit(*kinds, vocabulary=self.access)

        # Last, so the line reports what was resolved rather than what was asked for --
        # and so a wiring failure raises instead of announcing a deployment that never
        # came up.
        if logger.isEnabledFor(logging.INFO):
            logger.info("reading from: %s", self.origins.line())

    @property
    def origins(self) -> Origins:
        """Where this deployment is actually reading from."""
        return Origins.of(self.cfg, catalogue=self.catalogue, sessions=self.sessions_store)

    def held_for(self, source_ids: Held | None) -> frozenset[str] | None:
        """The caller's expanded source ids, or `None` where nothing narrows."""
        return access.held_by(self.access, source_ids)

    def _effective_grants(self, source_ids: Held | None) -> Capabilities:
        """The ceiling for one call: this deployment's, narrowed by the caller's."""
        if self.access is None:
            if source_ids is not None:
                msg = (
                    "this deployment has no access policy, so naming source ids means "
                    "nothing here -- write source_ids.yaml in the workspace, or set "
                    "KINGFISHER_SOURCE_IDS_FILE"
                )
                raise AccessError(msg)
            return self.grants
        if source_ids is None:
            msg = (
                "this deployment has an access policy, so a call must say who is "
                "calling: pass source_ids=[...] with the caller's source ids, or "
                "source_ids=UNSCOPED to run without one"
            )
            raise AccessError(msg)
        # Nothing central left to intersect with: the narrowing that source ids
        # imply is per definition, and happens in `AgentSpec.declares` where
        # the spec is known. What this still does is validate the names -- a
        # caller naming a source id this deployment does not declare is refused
        # here rather than quietly reaching nothing.
        self.held_for(source_ids)
        return self.grants

    def _graph_for(
        self,
        request: Request,
        session_dir: Path,
        capabilities: Capabilities | None = None,
        checkpointer: Any = _UNSET,
        *,
        source_ids: Held | None = None,
    ) -> Any:
        """The graph that serves one request, rooted at its session."""
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            return self._graph

        make = self._backend
        if make is None:  # pragma: no cover -- the constructor refuses the pairing
            msg = "a Kingfisher built with neither a backend nor a graph reached a turn"
            raise ValueError(msg)

        return build_agent(
            self.cfg,
            agent=self._agent_for(request, session_dir, source_ids=source_ids),
            held=self.held_for(source_ids),
            # Both called here rather than passed down, because this is where a turn
            # first has a session directory and neither can be built without one. The
            # runner goes into the factory rather than alongside it: a deployment that
            # replaced the backend owns what runs its commands, and handing the same
            # runner to `build_agent` as well would leave two answers to that.
            backend=make(
                self.cfg,
                session_dir,
                catalogue=self.catalogue,
                runner=self._runner(session_dir) if self._runner is not None else None,
            ),
            capabilities=capabilities if capabilities is not None else request.capabilities,
            session_dir=session_dir,
            run_on=request.run_on,
            middleware_registry=self.middlewares,
            checkpointer=self.threads if checkpointer is _UNSET else checkpointer,
            catalogue=self.catalogue,
        )

    def _pin_agent_in(self, session_dir: Path, name: str | None) -> None:
        """Keep the agent, in the directory this is about.

        **The directory, never an id re-derived from one.** This took an id and
        rebuilt the path as `<workspace>/sessions/<id>`, which is where the session
        is only when `session_root` is the default. Under any other one the turn
        runs elsewhere, so the pin was written where `agent_started_with` does not
        read and where `_keep` does not collect it: every turn re-resolved the agent
        from the catalogue, a deploy mid-conversation changed the prompt under a
        history that had already happened, and a request naming a different agent was
        served instead of refused. `ports.md` promises the store is handed the pinned
        agent, and that promise was false for exactly the deployments the port exists
        for.
        """
        if name is None:
            return
        documents = getattr(self.catalogue.agents, "documents", {})
        if (text := documents.get(name)) is not None:
            remember_agent(session_dir, text)

    def _agent_for(
        self, request: Request, session_dir: Path, *, source_ids: Held | None = None
    ) -> AgentSpec | None:
        """The agent this turn runs, which is the one its session opened with."""
        kept = agent_started_with(session_dir)
        if kept is None:
            spec = self.agent_named(request.agent, source_ids=source_ids)
            self._pin_agent_in(session_dir, request.agent)
            return spec

        started = read(kept, agent_snapshot(session_dir))
        if request.agent is not None and request.agent != started.name:
            msg = (
                f"this session is running {started.name!r}; it was fixed when the "
                f"session opened and cannot be changed to {request.agent!r} "
                f"mid-conversation -- start a session to run a different agent"
            )
            raise CapabilityError(msg)
        return started

    def agent_named(
        self, name: str | None, *, source_ids: Held | None = None
    ) -> AgentSpec | None:
        """The agent this request asked for, out of the catalogue."""
        offered = self.catalogue.agents.specs
        # Filtered before the listing is built, not after, so the message a caller reads
        # never names an agent they cannot open. An agent out of reach is spelled
        # exactly the way an agent that was never written is: anything else lets a
        # caller enumerate the catalogue by guessing, and sends them off to try
        # something they will only be refused for.
        if self.access is not None:
            if source_ids is None:
                msg = (
                    "this deployment has an access policy, so a call must say who "
                    "is calling: pass source_ids=[...] with the caller's source ids, or "
                    "source_ids=UNSCOPED to run without one"
                )
                raise AccessError(msg)
            if (held := self.held_for(source_ids)) is not None:
                offered = {
                    n: spec for n, spec in offered.items() if reaches(spec.source_ids, held)
                }
        listing = ", ".join(sorted(offered)) if offered else "none"
        # Two refusals, one remedy, and the remedy is different when there is nothing at
        # all. `SEED_HINT` says `--from DIR`, which needs a DIR -- and `SUGGESTION`
        # names none to a reader who installed the package, because neither directory it
        # could name exists for them. Correct, and a dead end: the next thing that
        # reader needs is the file itself.
        empty = "" if offered else f" -- try {SEED_HINT}, or write one:\n\n{STARTER_AGENT}"
        if name is None:
            msg = f"this request names no agent; this workspace offers {listing}{empty}"
            raise CapabilityError(msg)
        spec = offered.get(name)
        if spec is None:
            msg = f"no agent named {name!r}; this workspace offers {listing}{empty}"
            raise CapabilityError(msg)
        return spec

    def _prepare(
        self,
        request: Request,
        session: Session,
        *,
        source_ids: Held | None = None,
    ) -> Prepared:
        """Do everything up to the model call, and return what the loop needs.

        Filesystem work plus building the agent, measured at 15-46ms end to end --
        of which 9.2ms is the agent.
        """
        return self._open_turn(self._admit(request, session, source_ids=source_ids))

    def _checkpointer_for(self, session_dir: Path) -> tuple[Any, Any]:
        """The saver this turn runs on, and how to release it when the turn ends."""
        if not self.cfg.conversation_enabled:
            return None, None
        if self.threads is None:
            saver = build_session_checkpointer(session_dir)
            return saver, saver
        if callable(self.threads):
            saver = self.threads(session_dir)
            return saver, saver
        return self.threads, None

    def _admit(
        self,
        request: Request,
        session: Session,
        *,
        source_ids: Held | None = None,
    ) -> Admitted:
        """Everything that can refuse, before anything a refusal would strand."""
        cfg, dirs = self.cfg, self.dirs
        # Who is calling, before the session is marked, claimed or written to. Any
        # later and a refused caller's files are already in the `/data` of a session
        # that was never theirs; after the claim, and a turn running in it would
        # answer "busy" where an id nobody issued answers "no session". The grant
        # is asked for here only for its refusals, and again below for itself.
        self._effective_grants(source_ids)
        if not self._reaches_session(session.directory, self.held_for(source_ids)):
            raise self._unknown_session(session.id)
        # A turn writes inside the session, never to the session itself, so the
        # timestamp `retention.expired` reads would still say "idle" for a
        # conversation in daily use. Recorded here, at the top of a turn, rather
        # than at the end: a turn that fails still happened.
        dirs.mark_used(session.directory)
        # Before the other refusals rather than after: those read the session,
        # and a turn arriving halfway through would be reading it as it moved.
        session.claim(
            dirs, claim_path(session.directory), stale_after=cfg.claim_stale_after, now=time()
        )
        try:
            return self._admitted(request, session, cfg, source_ids=source_ids)
        except BaseException:
            session.release(dirs, claim_path(session.directory))
            raise

    def _admitted(
        self,
        request: Request,
        session: Session,
        cfg: Config,
        *,
        source_ids: Held | None = None,
    ) -> Admitted:
        """The rest of admission, once the session is claimed."""
        # Kernel-level guard; the deny rule covers only the file tools. Paths
        # it could not harden are reported below rather than raised: they used
        # to abort the run, and since this runs before anything else, one file
        # owned by another user made a session unusable for good.
        unprotected = protect_data(session.directory)

        # Before the data is placed, not after: placing it grows the session,
        # so checking afterwards would let a request that is already over
        # budget add to it and only then be refused.
        self._refuse_if_over_budget(session)

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        placement = place_data(request.data, session.directory)

        # What this deployment permits, narrowed by what the request asked for.
        allowed = self._effective_grants(source_ids).intersect(request.capabilities)
        # Named here rather than inline below, because two things want it and
        # the expression is a mouthful. `None` for a run with no policy or an
        # `UNSCOPED` one: both see the whole workspace, so there is nothing to
        # filter the report against.
        held = self.held_for(source_ids)
        # Resolved here rather than in `__init__`, because a saver is built per
        # session and there is no session until now.
        checkpointer, release = self._checkpointer_for(session.directory)
        graph = self._graph_for(
            request,
            session.directory,
            capabilities=allowed,
            checkpointer=checkpointer,
            source_ids=source_ids,
        )

        # The last thing that can refuse, and the reason this half exists. The
        # files themselves cannot be copied until a turn directory holds them,
        # but refusing them must not wait that long.
        check_placeable(request.inputs)

        return Admitted(
            request=request,
            session=session,
            graph=graph,
            unprotected=unprotected,
            placement=placement,
            release=release,
            # Tools come off the assembled graph rather than a list kept
            # somewhere: the surface includes whatever the workspace defined, so
            # the only honest answer to "what was offered" is what was wired.
            # Skills and subagents are not on the graph, so they are asked of
            # the same functions `build_agent` asked -- 0.04ms and 1.4ms against
            # an admit already measured at 15-46ms.
            withheld=withheld_by_kind(
                allowed,
                cfg,
                graph,
                self.catalogue,
                # Only where a vocabulary is in force. With none, `held` is
                # `None`, nothing was narrowed by source ids and the filter is a
                # no-op -- so the spec is not merely unused, it is unavailable:
                # an injected graph never resolves one, which is exactly the
                # case every test that hands in its own graph is.
                agent=(
                    self._agent_for(request, session.directory, source_ids=source_ids)
                    if held is not None
                    else None
                ),
                held=held,
            ),
            delegate_only=delegate_only(allowed, cfg, catalogue=self.catalogue),
            indistinct=indistinct_delegates(
                cfg,
                allowed,
                catalogue=self.catalogue,
                run_on=request.run_on,
            ),
        )

    def _open_turn(self, admitted: Admitted) -> Prepared:
        """Create the turn and compose what the loop needs."""
        cfg, dirs = self.cfg, self.dirs
        request, session = admitted.request, admitted.session
        session_id = session.id

        # The aggregate owns turn allocation: atomic, and a caller-supplied id wins.
        turn = session.allocate_turn(dirs, request.turn_id)

        place_inputs(request.inputs, turn.input_dir)

        logger = JsonlRunLogger(
            log_path(session.directory),
            model=cfg.models.default,
            endpoint=cfg.models.resolve()[0].endpoint,
            session_id=session_id,
        )
        logger.run_start(request.task, turn.virtual_dir)

        return Prepared(
            graph=admitted.graph,
            release=admitted.release,
            history=read_transcript(session.directory),
            message=turn_message(
                request.task,
                turn,
                admitted.placement.placed,
                has_inputs=bool(request.inputs),
            ),
            session=session,
            turn=turn,
            logger=logger,
            config={
                "configurable": {"thread_id": session_id},
                "callbacks": [logger],
                "recursion_limit": cfg.recursion_limit,
            },
            events=opening_events(
                turn.virtual_dir,
                admitted.unprotected,
                admitted.placement,
                admitted.withheld,
                admitted.indistinct,
                admitted.delegate_only,
            ),
            deadline=monotonic() + cfg.turn_timeout_s,
            timeout_s=cfg.turn_timeout_s,
        )

    def _keep(self, prepared: Prepared) -> tuple[str, ...]:
        """Persist what this turn produced, and name it."""
        self._record(prepared)
        kept = collect_artifacts(prepared.session.directory)
        if self.sessions_store is not None:
            # Two names beyond what `collect_artifacts` walks, which is `/derived`
            # and `/memory`. Both are under `.harness` and both have to survive a
            # machine, and neither is an artifact -- what a turn *produced* is what
            # the caller is handed, and these are what a session *is*.
            #
            # The transcript, or a session that outlived its machine comes back
            # with its files and no conversation -- measured, of a first draft that
            # wrote it and never kept it.
            #
            # And the pinned agent, which the store never saw while it lived under
            # `state_dir`. A session moving between hosts found no pin on the new
            # one, re-pinned from *that* host's catalogue, and accepted whatever
            # agent the request named -- so "a session is fixed to the agent it
            # opened with" held on one machine and quietly failed across two.
            #
            # The claim and the run log stay behind, and deliberately. A restored
            # claim would make the session look busy for `claim_stale_after` --
            # minutes -- before anyone could take the slot; the log is diagnostics
            # that would be re-uploaded whole every turn as it grows.
            keep_from(
                self.sessions_store,
                prepared.session.id,
                prepared.session.directory,
                (*kept, TRANSCRIPT, AGENT_SNAPSHOT),
            )
        return kept

    def _finished(
        self, prepared: Prepared, answer: str, kept: tuple[str, ...], *, stop_reason: str
    ) -> RunEvent:
        """The terminal event, built the same way whichever loop produced it."""
        return RunEvent(
            kind="finished",
            text=answer,
            result=RunResult(
                session_id=prepared.session.id,
                turn_id=prepared.turn.id,
                answer=answer,
                virtual_dir=prepared.turn.virtual_dir,
                run_dir=prepared.turn.directory,
                log_path=log_path(prepared.session.directory),
                # Collected after the graph has finished, so it reflects what
                # the turn actually left behind -- including what the shell
                # wrote, which no file tool would have reported.
                artifacts=kept,
                stop_reason=stop_reason,
            ),
        )

    def _record(self, prepared: Prepared) -> None:
        """Write what was said this turn, as records this package owns."""
        if not self.cfg.conversation_enabled:
            return
        read = getattr(prepared.graph, "get_state", None)
        if read is None:
            return
        try:
            snapshot = read(prepared.config)
        except ValueError:
            # `No checkpointer set` -- an injected graph that keeps no state
            # between supersteps. Structural, like the missing method above, and
            # not a conversation that failed to be read. Caught by name rather
            # than by suppressing everything, so a graph that genuinely cannot
            # answer still says so.
            return
        if snapshot is None:
            # What the paragraph above describes, now that persistence runs at
            # the end of *every* turn rather than only a completed one: a graph
            # that died before its first superstep has no state to hand back.
            # Reading `.values` off it raised, which turned "nothing to add"
            # into a second failure on top of the first.
            return
        messages = snapshot.values.get("messages")
        if messages:
            write_transcript(prepared.session.directory, runtime.as_transcript(messages))

    def stream(
        self, request: str | Request, *, source_ids: Held | None = None
    ) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens."""
        # Coerced here rather than only in `_prepare`, because holding the
        # session now happens first and a bare task string has no session id to
        # read.
        request = Request.coerce(request)
        with self._held_session(request) as session:
            yield from self._stream_turn(request, session, source_ids=source_ids)

    def _stream_turn(
        self, request: Request, session: Session, *, source_ids: Held | None = None
    ) -> Iterator[RunEvent]:
        """One turn, with its directory already held."""
        prepared = self._prepare(request, session, source_ids=source_ids)
        answer = ""
        ok = False
        stop_reason = "end_turn"
        kept: tuple[str, ...] = ()
        delegates = runtime.Delegates()
        try:
            # Inside the `try`, not before it. A caller that stops reading
            # during these -- `run_start` is the first -- used to leave the turn
            # with no end at all: the claim stayed taken, the checkpointer
            # stayed open, and nothing was persisted.
            yield from prepared.events
            for namespace, mode, chunk in prepared.graph.stream(
                runtime.user_payload(prepared.message, prepared.history),
                config=prepared.config,
                stream_mode=runtime.STREAM_MODES,
                subgraphs=True,
            ):
                answer, events = consume(namespace, mode, chunk, answer, delegates)
                yield from events
                if (stop := overrun(prepared)) is not None:
                    stop_reason = "max_duration"
                    yield stop
                    break
            answer = normalize_answer(answer)
            ok = True
        except runtime.OutOfSteps:
            # The other bound, reported like the first. `ok` stays true: the
            # turn ended in a way the caller was told about, which is what that
            # flag records -- not that every step it wanted happened.
            answer = normalize_answer(answer)
            stop_reason = "max_steps"
            ok = True
            yield out_of_steps(self.cfg)
        except Exception as exc:
            # Translated, not handled: a 401 is the one model-call failure the
            # person at the terminal caused and can fix, so it joins the errors
            # reported as a line instead of a traceback. Everything else is
            # re-raised untouched and keeps its traceback, which is what makes a
            # bug here still look like a bug.
            if (refused := refused_credentials(exc, self.cfg)) is None:
                raise
            raise refused from exc
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # Before the slot goes back, and inside its own `finally` so that a
            # store which is unreachable does not also leak the claim. Ending
            # the turn is the only moment that happens whether the caller read
            # the last event or walked away after the answer.
            try:
                kept = self._keep(prepared)
            finally:
                # The slot goes back however the turn ended -- answered, refused
                # mid-stream, or cut short by its deadline.
                prepared.session.release(self.dirs, claim_path(prepared.session.directory))
            # And so does the connection, when this service opened one. A
            # per-session database is a file descriptor per session, so a
            # process serving many would otherwise hold every one it touched.
            release_checkpointer(prepared.release)
            # And the QuickJS runtime, which is the one of the three that hangs
            # the process rather than leaking a handle. See `release_interpreter`.
            release_interpreter(self.cfg, prepared.graph)

        yield self._finished(prepared, answer, kept, stop_reason=stop_reason)

    def run(
        self,
        request: str | Request,
        *,
        source_ids: Held | None = None,
        delete_session: bool = False,
    ) -> RunResult:
        """Run one task to completion. A drain of `stream`.

        `delete_session=True` disposes of the session the turn used -- directory,
        conversation, claim and the store's copy -- once the turn has finished,
        and only then. A turn stopped at a bound keeps all of it: the partial
        work is real, and its conversation is what a retry on the same session
        is rebuilt from. A deletion that fails is on the result as
        `deletion_failure`, rather than the caller getting an answer and no sign
        the session stayed.

        Offered here and not on `stream`, which is not the asymmetry it looks
        like. A generator has no "after the turn" this library controls: past
        the final yield never runs for a caller who stops reading at the answer,
        and a `finally` fires on `GeneratorExit` too -- so a session would be
        deleted because somebody closed a loop early. A drain has an after.
        """
        result: RunResult | None = None
        for event in self.stream(request, source_ids=source_ids):
            if event.kind == "finished":
                result = event.result

        if result is None:  # pragma: no cover -- stream always ends with `finished`
            msg = "stream() ended without a finished event"
            raise RuntimeError(msg)
        if delete_session and result.completed:
            failure = self.delete_session(result.session_id)
            if failure:
                result = replace(result, deletion_failure=failure)
        return result

"""The application service: wired once, then asked to run things.

`stream()` used to build its own world on every call -- checkpointer, session
directories, workspace layout, permissions -- and take a keyword argument for each
thing a test might want to substitute. That list grows with every port, and it made
construction a per-request event for a program whose next shape is a server that
constructs once and serves many.

Measured, so the trade is a fact rather than a guess: 9.2ms median and 10.0ms p95 for
an unrestricted agent, of which 7.2ms is `create_deep_agent` compiling the graph --
everything kingfisher does around it is sub-millisecond. Against a turn of 1.5-1.9s
that is 0.6%.

  subagent      +5-6ms   each compiles its own graph; the range is the delegate
  custom tool   +0.47ms  linear to at least 50
  middleware    +0.03ms
  skill          0.0ms   sixteen measure the same as none
  deny rule      0.0ms   a hundred measure the same as none

Re-measured 2026-09-03. The baseline above held -- 8.1ms median, 9.2ms p95, 124
builds a second -- and the subagent row did not: 4.3ms became 5.1ms for a delegate
declaring one built-in tool and nothing else, and 6.2ms for the shipped
`assets_examples/`. It is the largest term, so the additive prediction below now runs
low, and the drift is not decay: a delegate costs what a delegate declares, and one
number cannot say that. Every figure here is *per item added to an otherwise
identical build*, which is the only form of it that transfers.

The custom-tool row was not re-measured. Doing it needs tool files in the workspace,
and their presence moves the baseline they would be measured against -- the delta and
the ground shift together. Left as it was, and marked so.

The costs are additive: 10 tools, 5 middleware, 20 deny rules and 2 subagents
predicted 20.8ms and measured 21.6ms, about 1% of a turn -- on the numbers above as
they stood in August. With the subagent row re-measured the same shape predicts a
little more, and the point survives either way: the model is additive and the total
is small against a turn.

Construction is CPU-bound Python, so it does not parallelise: ~100 builds per second
per process, and worker threads make it slightly worse (0.85x) rather than better. At
1.5s a turn that ceiling is around 150 concurrent turns, or about 34 if every one
activates eight subagents. Below that it is noise; above it, a cache keyed on session
*and* capabilities *and* a fingerprint of the definitions would be the thing to reach
for -- the fingerprint because uploads change what a session offers between turns,
which is the staleness this avoids by not caching at all.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack
from functools import partial
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
from kingfisher.config import Config
from kingfisher.domain.access import (
    AccessError,
    AccessReport,
    Groups,
    Held,
    _Unscoped,
    reaches,
)
from kingfisher.domain.agent import AgentSpec
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
from kingfisher.infrastructure.catalogue.agents import read_agent
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.activation import (
    defined_subagents,
    indistinct_delegates,
)
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.checkpointing import (
    async_session_checkpointer,
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
from kingfisher.infrastructure.workspace.files import fetch_refs
from kingfisher.infrastructure.workspace.layout import ensure_layout
from kingfisher.infrastructure.workspace.permissions import protect_data
from kingfisher.infrastructure.workspace.placement import check_placeable, place_data, place_inputs
from kingfisher.infrastructure.workspace.seeding import SEED_HINT, STARTER_AGENT
from kingfisher.infrastructure.workspace.sessions import (
    LocalSessionDirs,
    LocalSessionRoot,
    collect_artifacts,
)
from kingfisher.infrastructure.workspace.snapshots import (
    agent_snapshot,
    agent_started_with,
    remember_agent,
)
from kingfisher.infrastructure.workspace.uploads import provision

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.domain.ports import (
        CommandRunner,
        DefinitionStore,
        FileStore,
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
        definitions: DefinitionStore | None = None,
        files: FileStore | None = None,
        # Where a session's files go when the machine may not keep them. `None`
        # means the session directory is the only copy, which is what every
        # deployment has had until now and stays correct wherever the host is
        # allowed to hold data.
        sessions: SessionStore | None = None,
        session_root: SessionRoot | None = None,
        runner: Callable[[Path], CommandRunner] | None = None,
        catalogue: Definitions | Mapping[str, Path] | None = None,
        grants: Capabilities | None = None,
        middleware: Mapping[str, MiddlewareFactory] | None = None,
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
        # Host-side, beside the run logs, because the session directory is the
        # agent's own root -- a claim kept there would be something `execute`
        # could delete. `state_dir` is the one place the agent never addresses.
        self._claims: Path = self.cfg.state_dir / "claims"
        self.dirs.ensure(self._claims)
        # Three shapes, and the difference is who owns the connection. An instance is a
        # shared store the deployment made and manages; a callable is a factory this
        # service calls per session and closes after the turn; `None` means the default,
        # which is a database inside each session.
        self.threads: Any = threads
        self._shared: Any = threads if (threads is not None and not callable(threads)) else None
        # No default. A deployment that never serves uploaded definitions has
        # nothing to wire, and a request that supplies ids without one is a
        # configuration error worth saying out loud rather than a silent no-op.
        self.definitions: Any = definitions
        # Beside `definitions` and for the same reason: a caller with no host
        # paths names files by id, and only something the deployment wired can
        # turn a name into content.
        self.files: Any = files
        # What this deployment permits, before any request asks for anything.
        # Unrestricted by default, so a single-caller deployment is unaffected;
        # a service in front of many callers sets it, and `intersect` can only
        # subtract, so no request can widen past it.
        self.grants: Capabilities = grants if grants is not None else UNRESTRICTED
        # What a definition may name in its `middleware:` field. Empty by
        # default, so any such line fails loudly until a deployment wires one --
        # kingfisher cannot define these, only a deployment knows what its
        # middleware is. Registering is not the same as permitting: `grants`
        # still clamps which registered names a request may reach.
        self.middleware: Mapping[str, MiddlewareFactory] = middleware or {}
        # Walked here rather than when a definition names one. An entry nothing
        # can build is a fact about this deployment's own code, true before any
        # request arrives and true of entries no definition names yet -- so the
        # deployment hears it where it wired the registry, not from the first
        # caller unlucky enough to reach the wrong name. `_instantiate` keeps
        # its own guard for `build_agent`, which takes a registry directly.
        refuse_unbuildable_middleware(self.middleware)
        self._graph = graph
        # There is nothing to reconcile, and that is the shape of the design rather than
        # an omission. Audiences live in the definitions, so a definition *is* the asset
        # it is about -- there is no such thing as a line naming something the workspace
        # does not offer, and a definition naming a tool that does not exist was already
        # refused by `Offering.refuse_unknown` long before any of this.
        self.access: Groups | None = self.cfg.access
        self.access_report: AccessReport = AccessReport()
        if self.access is not None:
            # One walk of the definitions, not three. `defined_subagents` reads a
            # directory, and asking it once per question is how this came to do it three
            # times at every startup.
            kinds = (
                ("agent", self.catalogue.agents.specs),
                ("subagent", defined_subagents(self.cfg, None, catalogue=self.catalogue)),
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

    def held_for(self, groups: Held | None) -> frozenset[str] | None:
        """The caller's expanded groups, or `None` for no vocabulary / UNSCOPED.

        **Any sequence of names, not only a tuple.** This tested `isinstance(groups,
        tuple)` and answered `None` -- "no opinion", the same as no vocabulary at all
        -- for anything else. That was safe only because `for_groups` coerced first
        and was the single documented way in; with `groups=` the only way,
        `groups=["analysts"]` would have validated the name and then narrowed
        nothing. A list is the obvious thing to write, so it must mean what it looks
        like.
        """
        if self.access is None or groups is None or isinstance(groups, _Unscoped):
            return None
        if isinstance(groups, str):
            msg = f"groups is a sequence of names, not a string -- write [{groups!r}]"
            raise AccessError(msg)
        return self.access.expand(tuple(groups))

    def _effective_grants(self, groups: Held | None) -> Capabilities:
        """The ceiling for one call: this deployment's, narrowed by the caller's."""
        if self.access is None:
            if groups is not None:
                msg = (
                    "this deployment has no access policy, so naming groups means "
                    "nothing here -- write groups.yaml in the workspace, or set "
                    "KINGFISHER_GROUPS_FILE"
                )
                raise AccessError(msg)
            return self.grants
        if groups is None:
            msg = (
                "this deployment has an access policy, so a call must say who is "
                "calling: pass groups=[...] with the caller's groups, or "
                "groups=UNSCOPED to run without one"
            )
            raise AccessError(msg)
        if isinstance(groups, _Unscoped):
            return self.grants
        # Nothing central left to intersect with: the narrowing that groups
        # imply is per definition, and happens in `AgentSpec.declares` where
        # the spec is known. What this still does is validate the names -- a
        # caller naming a group this deployment does not declare is refused
        # here rather than quietly reaching nothing.
        self.access.expand(groups)
        return self.grants












    def _graph_for(
        self,
        request: Request,
        session_dir: Path,
        capabilities: Capabilities | None = None,
        checkpointer: Any = _UNSET,
        *,
        groups: Held | None = None,
    ) -> Any:
        """The graph that serves one request, rooted at its session."""
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            return self._graph

        return build_agent(
            self.cfg,
            agent=self._agent_for(request, session_dir.name, groups=groups),
            held=self.held_for(groups),
            # Called here rather than passed down. This is where a turn first
            # has a session directory, and `build_agent` is where one is already
            # known -- so the harness keeps taking a runner, and only the
            # service, which does not know the session until now, takes a way to
            # make one.
            runner=self._runner(session_dir) if self._runner is not None else None,
            capabilities=capabilities if capabilities is not None else request.capabilities,
            session_dir=session_dir,
            run_on=request.run_on,
            middleware_registry=self.middleware,
            checkpointer=self.threads if checkpointer is _UNSET else checkpointer,
            catalogue=self.catalogue,
        )

    def remember_agent(self, session_id: str, name: str | None) -> None:
        """Have this session keep the agent it opened with."""
        if name is None:
            return
        documents = getattr(self.catalogue.agents, "documents", {})
        if (text := documents.get(name)) is not None:
            remember_agent(self.cfg.state_dir, session_id, text)

    def _agent_for(
        self, request: Request, session_id: str, *, groups: Held | None = None
    ) -> AgentSpec | None:
        """The agent this turn runs, which is the one its session opened with."""
        kept = agent_started_with(self.cfg.state_dir, session_id)
        if kept is None:
            spec = self.agent_named(request.agent, groups=groups)
            self.remember_agent(session_id, request.agent)
            return spec

        started = read_agent(kept, agent_snapshot(self.cfg.state_dir, session_id))
        if request.agent is not None and request.agent != started.name:
            msg = (
                f"this session is running {started.name!r}; it was fixed when the "
                f"session opened and cannot be changed to {request.agent!r} "
                f"mid-conversation -- start a session to run a different agent"
            )
            raise CapabilityError(msg)
        return started

    def agent_named(
        self, name: str | None, *, groups: Held | None = None
    ) -> AgentSpec | None:
        """The agent this request asked for, out of the catalogue."""
        offered = self.catalogue.agents.specs
        # Filtered before the listing is built, not after, so the message a caller reads
        # never names an agent they cannot open. An agent out of reach is spelled
        # exactly the way an agent that was never written is: anything else lets a
        # caller enumerate the catalogue by guessing, and sends them off to try
        # something they will only be refused for.
        if (reach := self.access) is not None:
            if groups is None:
                msg = (
                    "this deployment has an access policy, so a call must say who "
                    "is calling: pass groups=[...] with the caller's groups, or "
                    "groups=UNSCOPED to run without one"
                )
                raise AccessError(msg)
            if isinstance(groups, tuple):
                held = reach.expand(groups)
                offered = {
                    n: spec for n, spec in offered.items() if reaches(spec.groups, held)
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
        request: str | Request,
        session: Session | None = None,
        checkpointer: Any = _UNSET,
        *,
        groups: Held | None = None,
    ) -> Prepared:
        """Do everything up to the model call, and return what the loop needs.

        Blocking, and deliberately so: filesystem work plus building the agent,
        measured at 15-46ms end to end -- of which 9.2ms is the agent. `astream` runs
        it on a worker thread rather than pretending otherwise.
        """
        return self._open_turn(self._admit(request, session, checkpointer, groups=groups))

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

    async def _async_checkpointer_for(self, stack: AsyncExitStack, session_dir: Path) -> Any:
        """The saver an async turn runs on, entered into the turn's exit stack."""
        if not self.cfg.conversation_enabled:
            return None
        if self.threads is None:
            return await stack.enter_async_context(async_session_checkpointer(session_dir))
        if callable(self.threads):
            made = self.threads(session_dir)
            if hasattr(made, "__aenter__"):
                return await stack.enter_async_context(made)
            return made
        return self.threads




    def _admit(
        self,
        request: str | Request,
        session: Session | None = None,
        checkpointer: Any = _UNSET,
        *,
        groups: Held | None = None,
    ) -> Admitted:
        """Everything that can refuse, before anything a refusal would strand."""
        request = Request.coerce(request)
        cfg, dirs = self.cfg, self.dirs
        session = session if session is not None else self.open_session_for(request)
        # A turn writes inside the session, never to the session itself, so the
        # timestamp `retention.expired` reads would still say "idle" for a
        # conversation in daily use. Recorded here, at the top of a turn, rather
        # than at the end: a turn that fails still happened.
        dirs.mark_used(session.directory)
        # Before the other refusals rather than after: those read the session,
        # and a turn arriving halfway through would be reading it as it moved.
        session.claim(dirs, self._claims, stale_after=cfg.claim_stale_after, now=time())
        try:
            return self._admitted(request, session, cfg, checkpointer, groups=groups)
        except BaseException:
            session.release(dirs, self._claims)
            raise

    def _admitted(
        self,
        request: Request,
        session: Session,
        cfg: Config,
        checkpointer: Any = _UNSET,
        *,
        groups: Held | None = None,
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

        # Both halves of "a request naming something that is not there must
        # fail before it leaves anything behind": the paths are checked by
        # `place_data`, the ids by the store, and neither has written yet.
        fetched = fetch_refs(request, self.files)

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        placement = place_data(request.data, session.directory, contents=fetched.data)

        # Before the agent, which discovers definitions by reading the
        # directories this writes.
        brought = provision(
            request, self.definitions, session.directory, cfg, catalogue=self.catalogue
        )

        # What this deployment permits, narrowed by what the request asked for.
        # Definitions the request brought itself are added back: their content
        # came from the caller, so a grant list -- written before their names
        # existed -- has no opinion about them.
        allowed = self._effective_grants(groups).intersect(request.capabilities).including(
            skills=brought.skills, subagents=brought.subagents
        )
        # Named here rather than inline below, because two things want it and
        # the expression is a mouthful. `None` for a run with no policy or an
        # `UNSCOPED` one: both see the whole workspace, so there is nothing to
        # filter the report against.
        held = self.held_for(groups)
        # Resolved here rather than in `__init__`, because the default is a
        # database inside this session and there is no session until now. The
        # async path opens its own on the event loop and hands it down, which is
        # what `checkpointer` carries.
        release: Any = None
        if checkpointer is _UNSET:
            checkpointer, release = self._checkpointer_for(session.directory)
        graph = self._graph_for(
            request,
            session.directory,
            capabilities=allowed,
            checkpointer=checkpointer,
            groups=groups,
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
            fetched_inputs=fetched.inputs,
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
                session.directory,
                graph,
                self.catalogue,
                # Only where a vocabulary is in force. With none, `held` is
                # `None`, nothing was narrowed by groups and the filter is a
                # no-op -- so the spec is not merely unused, it is unavailable:
                # an injected graph never resolves one, which is exactly the
                # case every test that hands in its own graph is.
                agent=(
                    self._agent_for(request, session.directory.name, groups=groups)
                    if held is not None
                    else None
                ),
                held=held,
            ),
            delegate_only=delegate_only(allowed, cfg, catalogue=self.catalogue),
            indistinct=indistinct_delegates(
                cfg,
                allowed,
                session.directory,
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

        place_inputs(request.inputs, turn.input_dir, contents=admitted.fetched_inputs)

        logger = JsonlRunLogger(
            log_path(cfg.state_dir, session_id),
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
                # Fetched inputs are inputs. The agent is told the directory
                # exists on the same terms either way -- where a file came from
                # is the deployment's business, not the agent's.
                has_inputs=bool(request.inputs or admitted.fetched_inputs),
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
            # The transcript is named separately rather than collected. It sits at the
            # session root, and `collect_artifacts` walks `/derived` and `/memory` -- so
            # a first draft wrote it and never kept it, and a session that outlived its
            # machine came back with its files and no conversation.
            keep_from(
                self.sessions_store,
                prepared.session.id,
                prepared.session.directory,
                (*kept, TRANSCRIPT),
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
                log_path=log_path(self.cfg.state_dir, prepared.session.id),
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
        self, request: str | Request, *, groups: Held | None = None
    ) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens."""
        # Coerced here rather than only in `_prepare`, because holding the
        # session now happens first and a bare task string has no session id to
        # read.
        request = Request.coerce(request)
        with self._held_session(request) as session:
            yield from self._stream_turn(request, session, groups=groups)

    def _stream_turn(
        self, request: Request, session: Session, *, groups: Held | None = None
    ) -> Iterator[RunEvent]:
        """One turn, with its directory already held."""
        prepared = self._prepare(request, session, groups=groups)
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
                prepared.session.release(self.dirs, self._claims)
            # And so does the connection, when this service opened one. A
            # per-session database is a file descriptor per session, so a
            # process serving many would otherwise hold every one it touched.
            release_checkpointer(prepared.release)
            # And the QuickJS runtime, which is the one of the three that hangs
            # the process rather than leaking a handle. See `release_interpreter`.
            release_interpreter(self.cfg, prepared.graph)

        yield self._finished(prepared, answer, kept, stop_reason=stop_reason)

    async def astream(
        self, request: str | Request, *, groups: Held | None = None
    ) -> AsyncIterator[RunEvent]:
        """`stream`, on an event loop.

        The same turn and the same ordering -- `_prepare` is shared, so there is one
        copy of the sequence that matters. What this buys is not a faster turn: a
        turn is the model's time, and measurement puts our own code at 15-46ms of
        1.5-1.9s. It is concurrency. Four turns measured against the live gateway
        cost 0.4-1.2 turns of wall clock instead of four.
        """
        request = Request.coerce(request)
        async with AsyncExitStack() as stack:
            # On the worker thread and into the stack that already wraps this
            # turn, so the root is released the same way the saver is -- and so
            # that holding it, which for a mount is real work, does not block
            # every other turn sharing this loop.
            holding = self._held_session(Request.coerce(request))
            session = await asyncio.to_thread(holding.__enter__)
            # Pushed rather than entered through the stack, for two reasons.
            # `enter_context` loses the session's type through `to_thread`, and
            # `push` leaves the turn's exception reaching a provider's
            # `__exit__` -- a callback would swallow which way the turn ended.
            # After entering, so a hold that failed is not then released.
            stack.push(holding)
            saver = await self._async_checkpointer_for(stack, session.directory)
            async for event in self._astream_turn(request, session, saver, groups=groups):
                yield event

    async def _astream_turn(
        self, request: Request, session: Session, saver: Any, *, groups: Held | None = None
    ) -> AsyncIterator[RunEvent]:
        """One async turn, with its session and saver already resolved."""
        prepared = await asyncio.to_thread(
            partial(self._prepare, request, session, saver, groups=groups)
        )
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
            for event in prepared.events:
                yield event
            async for namespace, mode, chunk in prepared.graph.astream(
                runtime.user_payload(prepared.message, prepared.history),
                config=prepared.config,
                stream_mode=runtime.STREAM_MODES,
                subgraphs=True,
            ):
                answer, events = consume(namespace, mode, chunk, answer, delegates)
                for event in events:
                    yield event
                if (stop := overrun(prepared)) is not None:
                    stop_reason = "max_duration"
                    yield stop
                    break
            answer = normalize_answer(answer)
            ok = True
        except runtime.OutOfSteps:
            # See the same branch in `stream`. Written twice rather than shared,
            # like the loop above it: the two differ only in `async for`, and
            # factoring three lines out of a generator costs more than it saves.
            answer = normalize_answer(answer)
            stop_reason = "max_steps"
            ok = True
            yield out_of_steps(self.cfg)
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # As in `stream`, and on a worker thread for the same reason
            # `_prepare` is: a directory walk and a store write would otherwise
            # block every other turn sharing this loop.
            try:
                kept = await asyncio.to_thread(self._keep, prepared)
            finally:
                # The slot goes back however the turn ended -- answered, refused
                # mid-stream, or cut short by its deadline.
                prepared.session.release(self.dirs, self._claims)
            # And so does the connection, when this service opened one. A
            # per-session database is a file descriptor per session, so a
            # process serving many would otherwise hold every one it touched.
            release_checkpointer(prepared.release)
            # And the QuickJS runtime, which is the one of the three that hangs
            # the process rather than leaking a handle. See `release_interpreter`.
            release_interpreter(self.cfg, prepared.graph)

        yield self._finished(prepared, answer, kept, stop_reason=stop_reason)

    async def arun(
        self, request: str | Request, *, groups: Held | None = None
    ) -> RunResult:
        """Run one task to completion on an event loop. A drain of `astream`."""
        result: RunResult | None = None
        async for event in self.astream(request, groups=groups):
            if event.kind == "finished":
                result = event.result

        if result is None:  # pragma: no cover -- astream always ends with `finished`
            msg = "astream() ended without a finished event"
            raise RuntimeError(msg)
        return result

    def run(self, request: str | Request, *, groups: Held | None = None) -> RunResult:
        """Run one task to completion. A drain of `stream`."""
        result: RunResult | None = None
        for event in self.stream(request, groups=groups):
            if event.kind == "finished":
                result = event.result

        if result is None:  # pragma: no cover -- stream always ends with `finished`
            msg = "stream() ended without a finished event"
            raise RuntimeError(msg)
        return result

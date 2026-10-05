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
subagents. Above that, a cache keyed on capabilities *and* a fingerprint of the
definitions is the thing to reach for.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
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
    Prepared,
    consume,
    decision_discarded,
    decision_needed,
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
from kingfisher.domain.request import DecisionError, Request, Resume
from kingfisher.domain.result import (
    AWAITING,
    END_TURN,
    PendingDecision,
    RunEvent,
    RunResult,
    normalize_answer,
)
from kingfisher.domain.session import Session, SessionBusyError
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.activation import (
    defined_subagents,
    indistinct_delegates,
)
from kingfisher.infrastructure.harness.agent import (
    Assembled,
    build_agent,
    builtin_tool_names,
)
from kingfisher.infrastructure.harness.backend import (
    DefaultBackends,
    SessionBackends,
)
from kingfisher.infrastructure.harness.checkpointing import (
    SharedThreads,
    build_session_checkpointer,
    harness_mark,
    paused_state,
    release_checkpointer,
    resumed_saver,
)
from kingfisher.infrastructure.harness.interpreter import release_interpreter
from kingfisher.infrastructure.harness.kinds.declared_middleware import (
    MiddlewareFactory,
    refuse_unbuildable_middleware,
)
from kingfisher.infrastructure.harness.runlog import LoggedRunEvents, RunLogger
from kingfisher.infrastructure.harness.session_files import (
    collect_artifacts,
    place_data,
    read_artifact,
)
from kingfisher.infrastructure.session_store import (
    AGENT_MARK,
    PENDING_MARK,
    HarnessFiles,
    clear_pause,
    pending_as_mark,
    pending_from_mark,
    read_pause_mark,
    read_transcript,
    write_pause_mark,
    write_transcript,
)
from kingfisher.infrastructure.steps import (
    Steps,
    adrive,
    adrive_finished,
    changing,
    drive,
    on_host,
)
from kingfisher.infrastructure.threads import finished, thread_pool
from kingfisher.infrastructure.workspace import (
    SEED_HINT,
    STARTER_AGENT,
    agent_started_with,
    ensure_layout,
    remember_agent,
)
from kingfisher.kinds.agents.reading import read
from kingfisher.kinds.agents.spec import AgentSpec
from kingfisher.layout import CLAIM, PAUSED_STATE

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.domain.ports import RunEvents


#: `kingfisher.origins`, and deliberately not `kingfisher`.
logger = logging.getLogger("kingfisher.origins")


@dataclass
class _Turn:
    """What a turn accumulates: written by its loop, read by its lifecycle."""

    prepared: Prepared
    answer: str = ""
    ok: bool = False
    stop_reason: str = END_TURN
    kept: tuple[str, ...] = ()
    delegates: runtime.Delegates = field(default_factory=runtime.Delegates)
    #: What the lifecycle produced and the loop still owes its caller: a context
    #: manager cannot yield into the generator around it.
    pending: list[RunEvent] = field(default_factory=list)
    #: Gated calls this turn stopped on, filled by the lifecycle from the one state
    #: read it already makes. Non-empty is what makes `stop_reason` `awaiting_decision`.
    awaiting: tuple[PendingDecision, ...] = ()


#: "Nothing was supplied", distinct from `None`, which is a deliberate choice to
#: run without a checkpointer at all.
_UNSET: Any = object()


def _asked(value: str | Request | Resume) -> Request | Resume:
    """Whatever a caller handed in, as one of the two things a turn starts from."""
    return value if isinstance(value, Request | Resume) else Request.coerce(value)


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
        # A langgraph checkpointer every session shares, a factory given a session
        # id, or nothing for the default -- see `_checkpointer_for`. The
        # checkpointer is langgraph's own type, which this layer may not name, so
        # it is `Any` and `SharedThreads` is what kingfisher asks of it.
        threads: Any = None,
        backends: SessionBackends | None = None,
        catalogue: Definitions | Mapping[str, Path] | None = None,
        grants: Capabilities | None = None,
        middlewares: Mapping[str, MiddlewareFactory] | None = None,
        graph: Any | None = None,
        run_events: RunEvents | None = None,
    ) -> None:
        self.cfg = cfg or config_module.config_from_env()
        config_module.enforce_local_only_tracing()
        # Asked for here rather than at the first async turn, so a second size in
        # one process is refused where the service is built.
        self._pool = thread_pool(self.cfg.thread_pool_size)

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

        # Session backends and not a backend: one backend is rooted at one session,
        # so a single instance shared by every session would be one filesystem for
        # every caller. A deployment separating its callers by *where their files
        # are* -- which is what replacing the backend is usually for -- would have
        # written the leak it was replacing the backend to avoid, and nothing about
        # the call site would look wrong.
        if backends is not None and not isinstance(backends, SessionBackends):
            msg = (
                "backends= takes a SessionBackends, which opens each session's backend "
                "and answers for every session: which there are, how big, when each was "
                "used, and deleting one. A backend is rooted at a session, so sharing one "
                "is sharing a filesystem between callers. To build on kingfisher's own, "
                "subclass DefaultBackends and override open; to build your own, subclass "
                "SessionBackends, which brings aopen and asessions with it"
            )
            raise TypeError(msg)
        # Where each turn's record of itself goes. The default is the `kingfisher.run`
        # logger, which the default logging configuration discards: a deployment
        # decides whether it keeps them by configuring logging or by passing a sink.
        self.run_events: RunEvents = (
            run_events if run_events is not None else LoggedRunEvents()
        )
        # Three shapes, and the difference is who owns the connection. An instance is a
        # shared store the deployment made and manages; a callable is a factory this
        # service calls per session and closes after the turn; `None` means the default,
        # which is `InMemorySaver` and holds nothing after the turn that made it.
        self.threads: Any = threads
        self._shared = (
            SharedThreads(threads) if (threads is not None and not callable(threads)) else None
        )
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
        # Required, and said at construction for the reason the catalogue is read
        # there: it is a wiring mistake, and this is the last moment it is cheap to say
        # so. `None` stays the default only so that leaving it out gets this message.
        #
        # None used to mean kingfisher picked one, and the reason it no longer does is
        # that the backend is the sandbox: it wraps every command in `sandbox-exec` or
        # Landlock, refuses host paths, and carries the route table a read-only rule is
        # only legal against. Inheriting that in silence was never unsafe -- the
        # default is the strict option, and still is -- but it meant a deployment could
        # wire the whole service without learning there was a boundary at all.
        #
        # Beside a pre-built graph too. Its agent runs on the backend it was compiled
        # with, but kingfisher places a request's data, collects what a turn left and
        # takes the turn lock through one of these -- and only the graph's builder
        # knows which session backends reach where that backend keeps a session.
        if backends is None:
            msg = (
                "kingfisher does not pick the filesystem its agents run on: pass "
                "backends=default_backends for the one it used to build for you, or "
                "session backends of your own for something else. A pre-built graph "
                "needs them too: they are how kingfisher reaches the sessions its "
                "backend keeps"
            )
            raise ValueError(msg)
        # Where the sessions are: what each turn opens, and what answers the questions
        # no single session does -- which there are, how big, when each was used.
        self._backends: SessionBackends = backends
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
        return Origins.of(
            self.cfg,
            catalogue=self.catalogue,
            sessions=None if type(self._backends) is DefaultBackends else self._backends,
        )

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
        # Nothing central left to intersect with: the narrowing that source ids
        # imply is per definition, and happens in `AgentSpec.declares` where
        # the spec is known. What this still does is ask `caller_holds` for the
        # refusal every door shares, and validate the names -- a caller naming a
        # source id this deployment does not declare is refused here rather than
        # quietly reaching nothing.
        access.caller_holds(self.access, source_ids)
        return self.grants

    def _graph_for(  # noqa: PLR0913 -- one keyword per thing a build needs and
        # cannot work out for itself. The two at the end were derived here until a
        # turn was measured resolving each of them twice; deriving them again would
        # be the shorter signature and the second answer.
        self,
        request: Request | Resume,
        capabilities: Capabilities,
        checkpointer: Any = _UNSET,
        *,
        agent: AgentSpec | None,
        held: frozenset[str] | None,
        files: Any,
    ) -> Any:
        """What serves one request, rooted at its session.

        **Two shapes, and the asymmetry is the point rather than an oversight.** A
        build kingfisher made comes back as `Assembled`, carrying what was attached;
        a graph the deployment supplied comes back as itself, because there is no
        record of a build that did not happen here. Returning the record either way
        would mean inventing one with every field empty -- a record asserting a build
        that never ran, which is worse than the union, since it would be asserted on.
        Callers take `.graph` where they need the graph; `_built_turn` is the only one
        in `src/`.
        """
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            return self._graph

        return build_agent(
            self.cfg,
            # Resolved by the caller, which is the only one there is: setup needs the
            # same spec for the withheld report, and asking twice meant the
            # first call resolving it from the catalogue and writing the pin while
            # the second read that pin back and parsed it.
            agent=agent,
            held=held,
            # The one setup opened or was handed, which placed this turn's data.
            # Opened again here, a remote backend would be asked for two sessions'
            # worth of sandbox, and the agent could run in the one that was never
            # given the data.
            backend=files,
            # What the deployment permits, narrowed by what the request asked for,
            # and never the request's own: they are equal only where the deployment
            # restricts nothing, which is why defaulting to them here looked
            # harmless. A caller asking for a tool the deployment withheld was
            # handed it -- measured, and the whole suite stayed green.
            capabilities=capabilities,
            run_on=request.run_on,
            middleware_registry=self.middlewares,
            checkpointer=self.threads if checkpointer is _UNSET else checkpointer,
            catalogue=self.catalogue,
        )

    def _files_for(self, session_id: str) -> Steps[Any]:
        """The backend a session's files are reached through, by kingfisher and any agent it builds.

        What runs its commands is the session backends' to decide, not this
        service's: a runner is about where commands run on a host, and only the
        session backends know which host a session is on.
        """
        return (
            yield changing(self._backends, "open", self.cfg, session_id, catalogue=self.catalogue)
        )

    def files_for(self, session_id: str) -> Any:
        """The backend a turn in this session is given, built the way a turn builds it.

        For a caller that reaches the session's files around a turn as well: hand it
        to the turn as `files=` and the turn runs on it rather than opening another,
        which on a remote backend is a second sandbox. Nothing here asks who the
        caller is acting for -- the turn still does, through the backend it is handed.
        """
        return drive(self._files_for(session_id))

    async def afiles_for(self, session_id: str) -> Any:
        """`files_for`, for a caller on an event loop."""
        return await adrive(self._files_for(session_id))

    def pending(
        self, session_id: str, *, source_ids: Held | None = None
    ) -> tuple[PendingDecision, ...]:
        """What this session's paused turn is waiting on, or nothing.

        Read from the mark the pause wrote rather than by starting a turn: asking what
        is pending must not be a thing that can supersede it, and every other way into
        the session is a turn.

        Checked the way reading a session is, because what it returns is somebody's
        tool calls with their arguments. A caller who cannot reach the session gets
        `UnknownSessionError`, the same answer a wrong id gets.
        """
        return drive(self._pending_steps(session_id, source_ids))

    async def apending(
        self, session_id: str, *, source_ids: Held | None = None
    ) -> tuple[PendingDecision, ...]:
        """`pending`, for a caller on an event loop."""
        return await adrive(self._pending_steps(session_id, source_ids))

    def _pending_steps(
        self, session_id: str, source_ids: Held | None
    ) -> Steps[tuple[PendingDecision, ...]]:
        reached = yield from self._reached(session_id, source_ids, files_wanted=True)
        if reached is None:
            raise self._unknown_session(session_id)
        _, files = reached
        mark = yield from read_pause_mark(HarnessFiles(files, session_id))
        return pending_from_mark(mark or {})

    def artifact(
        self, session_id: str, name: str, *, source_ids: Held | None = None
    ) -> bytes:
        """One file a turn in this session produced, fetched through its backend.

        `name` is as `RunResult.artifacts` gave it. Checked the way reading a session
        is: a caller who cannot reach the session gets `UnknownSessionError`, the
        same answer a wrong id gets.
        """
        return drive(self._artifact_steps(session_id, name, source_ids))

    async def aartifact(
        self, session_id: str, name: str, *, source_ids: Held | None = None
    ) -> bytes:
        """`artifact`, for a caller on an event loop."""
        return await adrive(self._artifact_steps(session_id, name, source_ids))

    def _artifact_steps(self, session_id: str, name: str, source_ids: Held | None) -> Steps[bytes]:
        reached = yield from self._reached(session_id, source_ids, files_wanted=True)
        if reached is None:
            raise self._unknown_session(session_id)
        _, files = reached
        return (yield from read_artifact(files, name))

    def _pin_agent_in(self, harness: HarnessFiles, name: str | None) -> Steps[None]:
        """Keep the agent, in the session this is about.

        **The session's own files, never a path re-derived from its id.** This took an id and
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
        if (text := self.catalogue.agents.documents.get(name)) is not None:
            yield from remember_agent(harness, text)

    def _agent_for(
        self,
        request: Request | Resume,
        harness: HarnessFiles,
        *,
        source_ids: Held | None = None,
        pinned: Any = _UNSET,
    ) -> Steps[AgentSpec | None]:
        """The agent this turn runs, which is the one its session opened with.

        `pinned` is the session's pin where the caller has read it already, as setup
        has: it read the pin to decide whether the caller reaches the session at all.
        """
        kept = (yield from agent_started_with(harness)) if pinned is _UNSET else pinned
        if kept is None:
            spec = self.agent_named(request.agent, source_ids=source_ids)
            yield from self._pin_agent_in(harness, request.agent)
            return spec

        started = read(kept)
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
        if (held := access.caller_holds(self.access, source_ids)) is not None:
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

    def _prepare_steps(
        self,
        request: Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> Steps[Prepared]:
        """Everything up to the model call, as the one sequence both loops drive.

        Every refusal comes before `run_start`, so a refused request leaves no turn
        behind, and the claim is the one thing taken before them, given back if one
        of them refuses. Measured at 15-46ms end to end, of which 9.2ms is building
        the agent -- which is `_built_turn`, the one piece of it done on this host.
        """
        cfg = self.cfg
        # Without an id this turn would mint a new session and run it in another's files.
        if files is not None and request.session_id is None:
            msg = (
                "files= is one session's backend and this request names no session: "
                "pass the session_id the backend was opened for"
            )
            raise ValueError(msg)
        session = yield from self._session_for(request)
        # Who is calling, before the session is marked, claimed or written to. Any
        # later and a refused caller's files are already in the `/data` of a session
        # that was never theirs; after the claim, and a turn running in it would
        # answer "busy" where an id nobody issued answers "no session". The grant
        # is asked for here only for its refusals, and again below for itself.
        self._effective_grants(source_ids)
        # Opened here, before anything else reads the session: who may touch it is
        # decided by its pinned agent, and that is read through the backend too.
        if files is None:
            files = yield from self._files_for(session.id)
        harness = HarnessFiles(files, session.id)
        held = self.held_for(source_ids)
        # Read once, for both of its readers: whether this caller reaches the session,
        # here, and which agent the turn runs, below. Not read where neither asks -- a
        # supplied graph under no policy.
        pinned = (
            (yield from agent_started_with(harness))
            if held is not None or self._graph is None
            else None
        )
        if not self._reaches_pin(pinned, held):
            raise self._unknown_session(session.id)
        # A turn writes inside the session, never to the session itself, so the
        # timestamp `retention.expired` reads would still say "idle" for a
        # conversation in daily use. Recorded here, at the top of a turn, rather
        # than at the end: a turn that fails still happened.
        yield changing(self._backends, "mark_used", cfg, session.id)
        # Before the other refusals rather than after: those read the session,
        # and a turn arriving halfway through would be reading it as it moved.
        if not (yield changing(files, "claim", CLAIM, stale_after=cfg.claim_stale_after)):
            msg = (
                f"session {session.id} already has a turn running; "
                f"wait for it to finish or start another session"
            )
            raise SessionBusyError(msg)
        try:
            return (
                yield from self._claimed(
                    request,
                    session,
                    files=files,
                    harness=harness,
                    pinned=pinned,
                    source_ids=source_ids,
                )
            )
        except BaseException:
            # A call that changes something, so a cancel arriving during it waits for it.
            yield changing(files, "release", CLAIM)
            raise

    def _claimed(  # noqa: PLR0913 -- what setup already built, handed on rather
        # than built twice: a second backend is a second sandbox on a remote one
        self,
        request: Request | Resume,
        session: Session,
        *,
        files: Any,
        harness: HarnessFiles,
        pinned: Any,
        source_ids: Held | None = None,
    ) -> Steps[Prepared]:
        """The rest of setup, once the session is claimed."""
        # What the backend could not make read-only, where it is one that says. Reported
        # rather than raised; see `DefaultBackends`.
        unprotected = tuple(getattr(files, "unprotected", ()))

        # Before the data is placed, not after: placing it grows the session,
        # so checking afterwards would let a request that is already over
        # budget add to it and only then be refused.
        yield from self._refuse_if_over_budget(session)

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        #
        # A resume places nothing. It is finishing work already proposed rather
        # than asking for something, so there is no `data` on it to place -- see
        # `Resume`, where the absence of the field carries the reason.
        placement = yield from place_data(getattr(request, "data", ()), files)

        # What this deployment permits, narrowed by what the request asked for.
        allowed = self._effective_grants(source_ids).intersect(request.capabilities)
        # Named here rather than inline below, because two things want it and
        # the expression is a mouthful. `None` for a run with no policy or an
        # `UNSCOPED` one: both see the whole workspace, so there is nothing to
        # filter the report against.
        held = self.held_for(source_ids)
        # What an earlier turn stopped on: the state this turn answers, or a pause it
        # supersedes and drops now. Before the graph is built, because a resume runs
        # on a saver that already holds the pause.
        paused, resume, discarded = yield from self._take_pause(request, session, harness)
        # Once, here, where both readers of it are in view: the build, and the
        # withheld report. Measured before this moved: a turn under a policy
        # resolved the agent twice and down different branches of the same function
        # -- the first writing the pin, the second reading it back and parsing it.
        #
        # Not resolved at all where neither reader wants it. A deployment that
        # supplied its own graph and declares no policy never asked for one, and
        # asking anyway would make such a session start refusing a request that names
        # a different agent, which today it does not.
        agent = (
            (yield from self._agent_for(request, harness, source_ids=source_ids, pinned=pinned))
            if self._graph is None or held is not None
            else None
        )
        history = yield from read_transcript(harness)
        return (
            yield on_host(
                self._built_turn,
                request,
                session,
                files=files,
                harness=harness,
                allowed=allowed,
                held=held,
                agent=agent,
                paused=paused,
                resume=resume,
                discarded=discarded,
                unprotected=unprotected,
                placement=placement,
                history=history,
            )
        )

    def _take_pause(
        self, request: Request | Resume, session: Session, harness: HarnessFiles
    ) -> Steps[tuple[bytes | None, dict[str, Any] | None, tuple[str, ...]]]:
        """Deal with whatever an earlier turn left waiting, before this turn starts:
        the paused state a resume restores, the answers it resumes with, and the
        tools a new request superseded.

        Both ways out of a pause meet here, because both have to happen before the
        graph exists: an answer needs the saver holding the state it answers, and a
        supersede needs the state gone before a turn runs on a saver still holding it.
        """
        held = yield from read_pause_mark(harness)
        if not isinstance(request, Resume):
            if held is None:
                return None, None, ()
            # Superseded. The transcript this turn replays ends at the unanswered
            # call, and `PatchToolCallsMiddleware` tells the model it was cancelled
            # -- so the agent learns the gated call never ran rather than silently
            # losing it. What the caller is told is `decision_discarded`.
            waiting = tuple(item.tool for item in pending_from_mark(held))
            yield from clear_pause(harness)
            return None, None, waiting
        if held is None:
            msg = f"session {session.id} is not waiting on a decision"
            raise DecisionError(msg)
        self._refuse_stale_pause(session, held, request)
        state = yield from harness.fetch(PAUSED_STATE)
        if state is None:
            msg = f"session {session.id} recorded a pause whose state is missing"
            raise DecisionError(msg)
        # Read back rather than derived again from the restored state. These are the
        # very ids the caller was handed, so answering them cannot drift from being
        # asked them -- and re-deriving would need the graph, which does not exist
        # until after this runs.
        return state, runtime.resume_payload(request.decisions, pending_from_mark(held)), ()

    def _refuse_stale_pause(
        self, session: Session, held: Mapping[str, str], request: Resume
    ) -> None:
        """Refuse a resume the paused graph would not be the same graph for.

        Checked rather than attempted. A paused session outliving a deploy is
        ordinary, and the difference between these two sentences and what a failed
        deserialise says -- a traceback about a node nobody has heard of -- is the
        whole reason the mark is written beside the state.
        """
        was = held.get(AGENT_MARK) or None
        # `was is None` is not a mismatch: nothing was recorded because nothing was
        # resolved -- an injected graph under no policy, where the deployment's own
        # graph runs whatever the request calls it and a *request* naming an agent is
        # not refused either. Comparing against it refused the one name that was right.
        if was is not None and request.agent is not None and was != request.agent:
            msg = (
                f"session {session.id} paused under agent {was or 'none'!r}, "
                f"and this resume names {request.agent!r}"
            )
            raise DecisionError(msg)
        now = harness_mark()
        if moved := sorted(k for k, v in now.items() if k in held and held[k] != v):
            msg = (
                f"session {session.id} paused before {', '.join(moved)} moved "
                f"({', '.join(f'{k} {held[k]}->{now[k]}' for k in moved)}); "
                "the pause did not survive the upgrade and the turn must be asked again"
            )
            raise DecisionError(msg)

    def _checkpointer_for(self, session_id: str) -> tuple[Any, Any]:
        """The saver this turn runs on, and how to release it when the turn ends."""
        if not self.cfg.conversation_enabled:
            return None, None
        if self.threads is None:
            saver = build_session_checkpointer()
            return saver, saver
        if callable(self.threads):
            saver = self.threads(session_id)
            return saver, saver
        return self.threads, None

    def _built_turn(  # noqa: PLR0913 -- everything setup decided, for the one piece
        # of it that is work on this host rather than calls to a port
        self,
        request: Request | Resume,
        session: Session,
        *,
        files: Any,
        harness: HarnessFiles,
        allowed: Capabilities,
        held: frozenset[str] | None,
        agent: AgentSpec | None,
        paused: bytes | None,
        resume: dict[str, Any] | None,
        discarded: tuple[str, ...],
        unprotected: tuple[str, ...],
        placement: Any,
        history: tuple[Any, ...],
    ) -> Prepared:
        """The saver, the graph and the turn itself: what setup builds rather than asks
        for. Done in one piece, on kingfisher's pool for `astream`, because building
        the agent is CPU-bound and on the loop would hold every other turn up.
        """
        cfg = self.cfg
        # Resolved here rather than in `__init__`, because a saver is built per
        # session and there is no session until now.
        checkpointer, release = self._checkpointer_for(session.id)
        graph = None
        try:
            saver = checkpointer if paused is None else self._restored(session, paused)
            built = self._graph_for(
                request,
                capabilities=allowed,
                checkpointer=saver,
                agent=agent,
                held=held,
                files=files,
            )
            # `isinstance` rather than `getattr(built, "graph", built)`: the two shapes
            # `_graph_for` returns are named types, and a duck test here would also
            # accept anything else carrying a `graph` attribute -- which is how the
            # backend seam lost its shell once already.
            graph = built.graph if isinstance(built, Assembled) else built
            # Tools come off the assembled graph rather than a list kept somewhere: the
            # surface includes whatever the workspace defined, so the only honest
            # answer to "what was offered" is what was wired. Skills and subagents are
            # not on the graph, so they are asked of the same functions `build_agent`
            # asked -- 0.04ms and 1.4ms against a setup measured at 15-46ms.
            withheld = withheld_by_kind(
                allowed,
                cfg,
                graph,
                self.catalogue,
                # Only where a vocabulary is in force. With none, `held` is `None`,
                # nothing was narrowed by source ids and the filter is a no-op -- so
                # the spec is not merely unused, it is unavailable: an injected graph
                # never resolves one, which is exactly the case every test that hands
                # in its own graph is.
                agent=agent if held is not None else None,
                held=held,
            )
            shared = delegate_only(allowed, cfg, catalogue=self.catalogue)
            indistinct = indistinct_delegates(
                cfg, allowed, catalogue=self.catalogue, run_on=request.run_on
            )
            # A caller-supplied id wins; otherwise one is made. No directory is
            # claimed, so there is nothing to be atomic about any more.
            turn = session.allocate_turn(request.turn_id)
            logger = RunLogger(
                self.run_events,
                model=cfg.models.default,
                endpoint=cfg.models.resolve()[0].endpoint,
                session_id=session.id,
                turn_id=turn.id,
            )
            # What this turn is: a task, or the answers to one already asked. The event
            # says which, because a resume with a task-shaped `run_start` reads as a
            # second request for work that was never re-requested.
            asked = getattr(request, "task", "")
            started = asked or f"resuming {len(getattr(request, 'decisions', ()))} decision(s)"
            logger.run_start(started)
        except BaseException:
            # Built and not yet handed over, so nothing else will let go of them.
            if graph is not None:
                release_interpreter(cfg, graph)
            release_checkpointer(release)
            raise

        return Prepared(
            graph=graph,
            files=files,
            harness=harness,
            release=release,
            saver=saver,
            context=built.context if isinstance(built, Assembled) else None,
            resume=resume,
            discarded=discarded,
            # The agent setup resolved, never `request.agent`: a session's agent is
            # fixed when it opens, so the request names one only on the turn that
            # opened it and this would be empty for every pause after that.
            agent_name=agent.name if agent is not None else None,
            history=history,
            # A resume adds no message: it continues a superstep that already has
            # everything it needs, and a new user turn appended there would be one
            # the model never saw asked.
            message=turn_message(asked, placement.placed) if asked else "",
            session=session,
            turn=turn,
            logger=logger,
            config={
                "configurable": {"thread_id": session.id},
                "callbacks": [logger],
                "recursion_limit": cfg.recursion_limit,
            },
            events=(
                *opening_events(turn.id, unprotected, placement, withheld, indistinct, shared),
                # At the start of the turn that did the superseding, which is where
                # it belongs: it is a fact about *this* turn, not the terminal state
                # of the one it replaced.
                *((decision_discarded(discarded),) if discarded else ()),
            ),
            deadline=monotonic() + cfg.turn_timeout_s,
            timeout_s=cfg.turn_timeout_s,
            unprotected=unprotected,
            placement=placement,
            withheld=withheld,
            indistinct=indistinct,
            delegate_only=shared,
        )

    def _restored(self, session: Session, state: bytes) -> Any:
        """The saver a paused turn left, for its resume to run on."""
        try:
            return resumed_saver(state)
        except (ValueError, NotImplementedError) as exc:
            # A backend has no rename, so a pause is written in place and a crash can
            # leave part of one. No part of one deserialises; this says which session
            # rather than leaving msgpack to.
            msg = f"session {session.id} recorded a pause whose state cannot be read: {exc}"
            raise DecisionError(msg) from exc

    def _keep(self, prepared: Prepared, snapshot: Any) -> Steps[tuple[str, ...]]:
        """Persist what this turn produced, and name it."""
        yield from self._record_transcript(prepared, snapshot)
        return (yield from collect_artifacts(prepared.files))

    def _finished(  # noqa: PLR0913 -- one terminal event, assembled from the four
        # things a turn ends holding. A parameter object here would exist only to
        # be unpacked one line later.
        self,
        prepared: Prepared,
        answer: str,
        kept: tuple[str, ...],
        *,
        stop_reason: str,
        waiting: tuple[PendingDecision, ...] = (),
        discarded: tuple[str, ...] = (),
    ) -> RunEvent:
        """The terminal event, built the same way whichever loop produced it."""
        return RunEvent(
            kind="finished",
            text=answer,
            result=RunResult(
                pending=waiting,
                discarded=discarded,
                session_id=prepared.session.id,
                turn_id=prepared.turn.id,
                answer=answer,
                # Collected after the graph has finished, so it reflects what
                # the turn actually left behind -- including what the shell
                # wrote, which no file tool would have reported.
                artifacts=kept,
                stop_reason=stop_reason,
            ),
        )

    def _settled(self, prepared: Prepared) -> Any:
        """This turn's final graph state, read once, or `None` where there is none.

        One read feeding both the transcript and the pause. Two would pass every test
        in this tree and come apart the first time one of them learned something the
        other did not -- which is the shape `test_no_surface_decides_for_itself_what_a
        _finished_turn_is` already exists to prevent one layer up.
        """
        read = getattr(prepared.graph, "get_state", None)
        if read is None:
            return None
        try:
            # A paused turn's own state, so the pause has to be visible here rather
            # than only on the stream chunk `run` never sees.
            return read(prepared.config)
        except ValueError:
            # `No checkpointer set` -- an injected graph that keeps no state
            # between supersteps. Structural, like the missing method above, and
            # not a conversation that failed to be read. Caught by name rather
            # than by suppressing everything, so a graph that genuinely cannot
            # answer still says so.
            return None

    def _record_transcript(self, prepared: Prepared, snapshot: Any) -> Steps[None]:
        """Write what was said this turn, as records this package owns."""
        if not self.cfg.conversation_enabled:
            return
        if snapshot is None:
            # A graph that keeps no state, or one that died before its first
            # superstep -- persistence runs at the end of *every* turn rather than
            # only a completed one, and reading `.values` off nothing raised, which
            # turned "nothing to add" into a second failure on top of the first.
            return
        messages = snapshot.values.get("messages")
        if messages:
            yield from write_transcript(prepared.harness, runtime.as_transcript(messages))

    def _settle_pause(
        self, prepared: Prepared, snapshot: Any
    ) -> Steps[tuple[PendingDecision, ...]]:
        """Keep a paused turn's graph state, or clear a pause this turn finished.

        Written here and nowhere else, which is what keeps the file's presence a
        truthful mark. Every path out of a turn arrives at this one: answered,
        refused, cut short at a bound -- and each of those is a turn that is no
        longer waiting.

        Only a resume has a pause left to clear by then. Setup read the mark for every
        turn, and dropped it for a request that superseded one; a turn that found none
        has nothing on disk, and clearing it anyway was three round trips -- two
        deletes and a read to check them -- at the end of every turn there was.
        """
        harness = prepared.harness
        waiting = runtime.pending_in(snapshot) if snapshot is not None else ()
        if not waiting or prepared.saver is None:
            # Including the turn that was just resumed and ran to the end. A saver
            # this service did not open cannot be written out either -- an injected
            # store is the deployment's, and holding its state in a file of ours
            # would be a second copy nobody asked for.
            if prepared.resume is not None:
                yield from clear_pause(harness)
            return ()
        yield from harness.store(PAUSED_STATE, (yield on_host(paused_state, prepared.saver)))
        # The state first, then the mark. The mark is what every other path tests to
        # decide a session is waiting, so writing it second means a write that dies
        # between the two leaves a session that is simply not paused -- rather than
        # one that claims to be and has nothing to resume into.
        yield from write_pause_mark(
            harness,
            {
                AGENT_MARK: prepared.agent_name or "",
                PENDING_MARK: pending_as_mark(waiting),
                **harness_mark(),
            },
        )
        return waiting

    def stream(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        A `Resume` answers a turn that stopped at an approval gate. It goes through
        the same door rather than a method of its own: the admission it faces is the
        same admission, and a second entry point would be a second place for those
        checks to be forgotten.

        `files` is the session's backend from `files_for`, for a caller that uses it
        around the turn too; the request must name that session.
        """
        # Coerced here, because setup reads the request's session id first and a bare
        # task string has none to read.
        yield from self._stream_turn(_asked(request), source_ids=source_ids, files=files)

    @contextmanager
    def _turn_outcome(self, turn: _Turn) -> Iterator[None]:
        """How a turn's graph loop ended, recorded on `turn`, for both loops.

        A bound or a translation added here reaches `stream` and `astream` at once;
        they differ only in the loop, and in how the ending is run after it.
        """
        try:
            yield
            turn.answer = normalize_answer(turn.answer)
            turn.ok = True
        except runtime.OutOfSteps:
            # The other bound, reported like the first. `ok` stays true: the turn
            # ended in a way the caller was told about, which is what that flag
            # records -- not that every step it wanted happened.
            turn.answer = normalize_answer(turn.answer)
            turn.stop_reason = "max_steps"
            turn.ok = True
            turn.pending.append(out_of_steps(self.cfg))
        except Exception as exc:
            # Translated, not handled: a 401 is the one model-call failure the
            # person at the terminal caused and can fix, so it joins the errors
            # reported as a line instead of a traceback. Everything else is
            # re-raised untouched and keeps its traceback, which is what makes a
            # bug here still look like a bug.
            if (refused := refused_credentials(exc, self.cfg)) is None:
                raise
            raise refused from exc

    def _end_turn(self, turn: _Turn) -> None:
        """Everything a turn does once it is over, for `stream`, which runs it inline."""
        drive(self._ending_steps(turn))

    async def _aend_turn(self, turn: _Turn) -> None:
        """Everything a turn does once it is over, for `astream`.

        Awaited as a task of its own, which no cancel reaches, and waited for however
        many times the caller is cancelled meanwhile -- so every step runs, reads
        included, as on a thread nothing could interrupt, without holding one: each
        step is a round trip on a remote backend, and on kingfisher's pool two hundred
        concurrent turns queued for them. What a task does not have that a thread did
        is surviving its event loop, and the loop goes on while `astream` waits here,
        which it always does.
        """
        _, cancelled = await finished(adrive(self._ending_steps(turn)))
        if cancelled is not None:
            raise cancelled

    def _ending_steps(self, turn: _Turn) -> Steps[None]:
        """What a turn does once it is over, however it ended."""
        prepared = turn.prepared
        yield on_host(prepared.logger.run_end, ok=turn.ok, answer_chars=len(turn.answer))
        # Before the slot goes back, and inside its own `finally` so that a
        # store which is unreachable does not also leak the claim. Ending
        # the turn is the only moment that happens whether the caller read
        # the last event or walked away after the answer.
        # One read, before anything is let go of: the saver still holds the
        # paused state, and `_settle_pause` is what writes it out.
        snapshot = yield on_host(self._settled, prepared)
        turn.awaiting = yield from self._settle_pause(prepared, snapshot)
        if turn.awaiting:
            turn.pending.append(decision_needed(turn.awaiting))
            # A bound that already fired keeps the reason it gave. Both are true
            # -- the gate is real and the checkpoint is written either way -- and
            # `stop_reason` answers why the turn *ended*, which for a turn cut
            # off at its deadline is the deadline and not the question it was
            # holding. The pending calls are reported regardless, so a caller is
            # never left guessing what was in flight.
            if turn.stop_reason == END_TURN:
                turn.stop_reason = AWAITING
        try:
            turn.kept = yield from self._keep(prepared, snapshot)
        finally:
            # The slot goes back however the turn ended -- answered, refused
            # mid-stream, cut short by its deadline, or cancelled during setup.
            yield changing(prepared.files, "release", CLAIM)
        # And so does the connection, when this service opened one. A
        # per-session database is a file descriptor per session, so a
        # process serving many would otherwise hold every one it touched.
        yield on_host(release_checkpointer, prepared.release)
        # And the QuickJS runtime, which is the one of the three that hangs
        # the process rather than leaking a handle. See `release_interpreter`.
        yield on_host(release_interpreter, self.cfg, prepared.graph)

    def _read(self, turn: _Turn, namespace: Any, mode: Any, chunk: Any) -> tuple[RunEvent, ...]:
        """One stream chunk, read as events. The answer accumulates on `turn`."""
        turn.answer, events = consume(namespace, mode, chunk, turn.answer, turn.delegates)
        return events

    def _payload(self, turn: _Turn) -> Any:
        """What the graph is driven with: a conversation, or answers to resume into.

        The two are alternatives. A resume re-enters the node that stopped rather
        than starting a superstep, which is the whole difference between answering a
        gate and asking the same question over again.
        """
        if turn.prepared.resume is not None:
            return turn.prepared.resume
        return runtime.user_payload(turn.prepared.message, turn.prepared.history)

    def _driving(self, turn: _Turn) -> dict[str, Any]:
        """The keywords both graph streams are driven with.

        Shared so that one of them cannot quietly lose `subgraphs`, which would
        leave a delegate's tokens out of that path and nothing else changed.
        """
        driving: dict[str, Any] = {
            "config": turn.prepared.config,
            "stream_mode": runtime.STREAM_MODES,
            "subgraphs": True,
        }
        if turn.prepared.context is not None:
            # Here rather than beside the payload, so that a resume carries it: the
            # payload is what differs between a turn and its resume, and the context
            # is in no checkpoint for the resume to find.
            #
            # Left off for a graph the caller built, not passed as `None`. All that is
            # known of such a graph is that it takes the three keywords above, and one
            # that is not langgraph's refuses a fourth.
            driving["context"] = turn.prepared.context
        return driving

    def _bound(self, turn: _Turn) -> RunEvent | None:
        """The turn's deadline, read between chunks. `None` while there is time."""
        stop = overrun(turn.prepared)
        if stop is not None:
            turn.stop_reason = "max_duration"
        return stop

    def _ending(self, turn: _Turn) -> tuple[RunEvent, ...]:
        """What a turn owes its caller once the lifecycle has closed."""
        return (
            *turn.pending,
            self._finished(
                turn.prepared,
                turn.answer,
                turn.kept,
                stop_reason=turn.stop_reason,
                waiting=turn.awaiting,
                discarded=turn.prepared.discarded,
            ),
        )

    def _stream_turn(
        self,
        request: Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> Iterator[RunEvent]:
        """One turn, on the graph's own stream."""
        turn = _Turn(drive(self._prepare_steps(request, source_ids=source_ids, files=files)))
        try:
            with self._turn_outcome(turn):
                # Inside, not before. A caller that stops reading during these --
                # `run_start` is the first -- used to leave the turn with no end at
                # all: the claim stayed taken, the checkpointer stayed open, and
                # nothing was persisted.
                yield from turn.prepared.events
                for chunk in turn.prepared.graph.stream(
                    self._payload(turn), **self._driving(turn)
                ):
                    yield from self._read(turn, *chunk)
                    if (stop := self._bound(turn)) is not None:
                        yield stop
                        break
        finally:
            self._end_turn(turn)
        yield from self._ending(turn)

    async def _astream_turn(
        self,
        request: Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> AsyncGenerator[RunEvent, None]:
        """The same turn on the graph's own async stream.

        `AsyncGenerator` because `astream` closes this by hand, and the type has
        to admit `aclose`.
        """
        prepared, cancelled = await adrive_finished(
            self._prepare_steps(request, source_ids=source_ids, files=files)
        )
        turn = _Turn(prepared)
        if cancelled is not None:
            # The cancel arrived while the turn was being built, so the built turn is
            # the caller's to let go of: ended the way any turn ends, then the cancel.
            await self._aend_turn(turn)
            raise cancelled
        try:
            with self._turn_outcome(turn):
                for event in turn.prepared.events:
                    yield event
                async for chunk in turn.prepared.graph.astream(
                    self._payload(turn), **self._driving(turn)
                ):
                    for event in self._read(turn, *chunk):
                        yield event
                    if (stop := self._bound(turn)) is not None:
                        yield stop
                        break
        finally:
            await self._aend_turn(turn)
        for event in self._ending(turn):
            yield event

    def run(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        delete_session: bool = False,
        files: Any = None,
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
        for event in self.stream(request, source_ids=source_ids, files=files):
            if event.kind == "finished":
                result = event.result
        return drive(self._drained_steps(result, delete_session=delete_session))

    def _drained_steps(
        self, result: RunResult | None, *, delete_session: bool
    ) -> Steps[RunResult]:
        """What both drains do once the stream they read has ended."""
        if result is None:  # pragma: no cover -- a stream always ends with `finished`
            msg = "the stream ended without a finished event"
            raise RuntimeError(msg)
        if delete_session and result.completed:
            failure = yield from self._delete_session_steps(result.session_id)
            if failure:
                result = replace(result, deletion_failure=failure)
        return result

    async def astream(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> AsyncIterator[RunEvent]:
        """`stream`, for a caller already on an event loop. Cancelling is immediate.

        **This path asks two things `stream` does not.** The `a`-prefixed
        middleware hook is the one that runs, so a middleware written only as
        `wrap_model_call` raises the first time this reaches it; and a saver
        passed as `threads=` needs `aget_tuple` and `aput`. Both refusals are
        loud, and neither reaches a caller of `stream`.
        """
        turn = self._astream_turn(_asked(request), source_ids=source_ids, files=files)
        try:
            async for event in turn:
                yield event
        finally:
            # By hand: an async generator dropped by another waits for the loop to
            # finalise it, and `yield from`'s close has no async spelling. Without
            # this a caller who stops reading leaves the session claimed.
            await turn.aclose()

    async def arun(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        delete_session: bool = False,
        files: Any = None,
    ) -> RunResult:
        """`run`, for a caller already on an event loop. A drain of `astream`.

        `delete_session` means here what it means on `run`, and for the same
        reason it is offered on neither stream: a drain has an "after" that a
        generator does not.

        No `aclose` to match `astream`'s, deliberately: measured, a cancelled
        drain gives the claim back one turn of the loop later either way, so the
        close would be a line nothing can observe. `findings.md` has the numbers.
        """
        events = self.astream(request, source_ids=source_ids, files=files)
        result: RunResult | None = None
        async for event in events:
            if event.kind == "finished":
                result = event.result
        return await adrive(self._drained_steps(result, delete_session=delete_session))

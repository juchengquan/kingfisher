"""The service: wired once, then asked to run things.

Each request builds its agent afresh on purpose: construction is cheap beside a turn.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field, replace
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core import environment as config_module
from kingfisher.core.access import checks as access
from kingfisher.core.access.capabilities import (
    UNRESTRICTED,
    Capabilities,
    CapabilityError,
)
from kingfisher.core.access.reach import reaches
from kingfisher.core.access.source_ids import AccessReport, Held, SourceIds, with_datazone
from kingfisher.core.agent.activation import (
    defined_subagents,
    for_the_caller,
    indistinct_subagents,
    settled_request,
)
from kingfisher.core.agent.assembly import (
    Assembled,
    build_agent,
    builtin_tool_names,
)
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.agent.kinds.declared_middleware import (
    MiddlewareFactory,
    refuse_unbuildable_middleware,
)
from kingfisher.core.agent.prompting import InstructionsFile, refuse_a_workspace_prompt
from kingfisher.core.agent.usable_backend import refuse_unusable_backend
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.definitions.catalogue import Definitions, resolve_definitions
from kingfisher.core.definitions.seeding import SEED_HINT, starter_agent
from kingfisher.core.interfaces import Instructions, Sessions
from kingfisher.core.layout import PAUSED_STATE
from kingfisher.core.origins import Origins
from kingfisher.core.reporting import (
    opening_events,
    subagent_only,
    withheld_by_kind,
)
from kingfisher.core.request import DecisionError, Request, Resume
from kingfisher.core.result import (
    AWAITING,
    END_TURN,
    LedgerEntry,
    PendingDecision,
    RunEvent,
    RunResult,
    normalize_answer,
)
from kingfisher.core.session import runtime
from kingfisher.core.session.checkpointing import (
    build_session_checkpointer,
    harness_mark,
    paused_state,
    resumed_saver,
)
from kingfisher.core.session.disposal import Disposal
from kingfisher.core.session.files import (
    collect_artifacts,
    place_inputs,
)
from kingfisher.core.session.ledger import Ledger, keep_ledger
from kingfisher.core.session.methods import SessionMethods
from kingfisher.core.session.model import (
    Conversation,
    SessionBusyError,
    unknown_session,
)
from kingfisher.core.session.runlog import LoggedRunEvents, RunLogger
from kingfisher.core.session.steps import (
    Steps,
    adrive,
    adrive_finished,
    changing,
    drive,
    on_host,
)
from kingfisher.core.session.store import (
    AGENT_MARK,
    PENDING_MARK,
    HarnessFiles,
    KeptLedger,
    agent_started_with,
    clear_pause,
    pending_as_mark,
    pending_from_mark,
    read_ledger,
    read_pause_mark,
    read_transcript,
    remember_agent,
    write_pause_mark,
    write_transcript,
)
from kingfisher.core.session.threads import finished, thread_pool
from kingfisher.core.session.turn import (
    Prepared,
    consume,
    decision_discarded,
    decision_needed,
    out_of_steps,
    out_of_tokens,
    overrun,
    turn_message,
)
from kingfisher.core.workspace import ensure_layout

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.interfaces import RunEvents


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
    subagent_names: runtime.SubagentNames = field(default_factory=runtime.SubagentNames)
    seen: runtime.SeenMessages = field(default_factory=runtime.SeenMessages)
    #: What the lifecycle produced and the loop still owes its caller: a context
    #: manager cannot yield into the generator around it.
    pending: list[RunEvent] = field(default_factory=list)
    #: Gated calls this turn stopped on; non-empty makes `stop_reason` `awaiting_decision`.
    awaiting: tuple[PendingDecision, ...] = ()
    #: What this turn's tools returned, as the lifecycle kept it.
    recorded: tuple[LedgerEntry, ...] = ()


#: "Nothing was supplied", distinct from `None`, which is a deliberate choice to
#: run without a checkpointer at all.
_UNSET: Any = object()


def _asked(value: str | Request | Resume) -> Request | Resume:
    """Whatever a caller handed in, as one of the two things a turn starts from."""
    return value if isinstance(value, Request | Resume) else Request.coerce(value)


#: Matched on the exception's `status_code`, not its class: `anthropic` and `openai`
#: each raise their own `AuthenticationError`, and the command may import neither.
UNAUTHORIZED = 401


def refused_credentials(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 401 as the configuration error it is, or `None` for anything else.

    The endpoint is matched by the failed request's URL, not taken as the default: a
    subagent may run elsewhere. The shell is named because `load_dotenv` runs with
    `override=False`, so an exported key beats the one in `.env`.
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
    if named and (entry := named[0][1].key_entry):
        return ConfigError(
            f"{where} rejected the key from entry {entry!r} in {cfg.models.keys_file} "
            f"(401); that entry is the one to check"
        )
    supplied = f" from {named[0][1].key_env}" if named and named[0][1].key_env else ""
    return ConfigError(
        f"{where} rejected the key{supplied} (401). A value exported in the shell "
        f"overrides the one in .env, so check the environment before the file"
    )


class Kingfisher(SessionMethods, Disposal):
    """A configured kingfisher. Construct once; call `run` or `stream` per request."""

    def __init__(  # noqa: PLR0913 -- the composition root: each argument is substitutable
        self,
        cfg: Config | None = None,
        *,
        # No default: the backend is the sandbox, and a default would let a deployment
        # wire the service without learning there was a boundary. Required beside a
        # pre-built graph too, whose builder alone knows where a session is kept.
        sessions: Sessions,
        middlewares: Mapping[str, MiddlewareFactory] | None = None,
        graph: Any | None = None,
        run_events: RunEvents | None = None,
        # Left out, `INSTRUCTIONS.md` beside the definitions. Asked once per agent built,
        # so a change reaches the next turn.
        instructions: Instructions | None = None,
    ) -> None:
        self.cfg = cfg or config_module.config_from_env()
        refuse_a_workspace_prompt(self.cfg)
        config_module.enforce_local_only_tracing()
        # Asked for here rather than at the first async turn, so a second size in
        # one process is refused where the service is built.
        self._pool = thread_pool(self.cfg.thread_pool_size)

        # Only what sessions share: a session's own path is known once a request names it.
        self.workspace: Path = ensure_layout(self.cfg.workspace)

        # Read and parsed now, not per request: a catalogue that cannot be read is a
        # wiring mistake, to fail at startup rather than serve an agent told about nothing.
        self.catalogue: Definitions = resolve_definitions(self.cfg).warm()
        # Whether a workspace tool wears a built-in's name, refused at startup: only an
        # assembled graph can say, so `warm` cannot. Only with tools: nothing else shadows.
        if self.catalogue.tools.found:
            # Only `ConfigError`: the deployment could not be assembled, which the turn and
            # `doctor` report, and raised here it would stop the `sessions` and `reap`
            # commands, which need no model. `CapabilityError` still refuses.
            with suppress(ConfigError):
                builtin_tool_names(self.cfg, self.catalogue)

        # Sessions, not a backend: one backend shared by every session is one filesystem
        # for every caller, and nothing at the call site would look wrong.
        if not isinstance(sessions, Sessions):
            msg = (
                "sessions= takes a Sessions, which opens each session's backend "
                "and answers for every session: which there are, how big, when each was "
                "used, and deleting one. A backend is rooted at a session, so sharing one "
                "is sharing a filesystem between callers. To build on kingfisher's own, "
                "subclass LocalSessions and override open; to build your own, subclass "
                "Sessions, which brings aopen and asessions with it"
            )
            raise TypeError(msg)
        self.run_events: RunEvents = (
            run_events if run_events is not None else LoggedRunEvents()
        )
        # `intersect` can only subtract, so no request can widen past this.
        self.grants: Capabilities = (
            self.cfg.grants if self.cfg.grants is not None else UNRESTRICTED
        )
        # What a definition may name under `middlewares:`. Registering is not permitting:
        # `grants` still clamps which registered names a request may reach.
        self.middlewares: Mapping[str, MiddlewareFactory] = middlewares or {}
        self.instructions: Instructions = instructions or InstructionsFile()
        # Here, not when a definition names one, so the deployment hears it where it
        # wired the registry rather than from the first caller to reach the wrong name.
        refuse_unbuildable_middleware(self.middlewares)
        self._sessions: Sessions = sessions
        self._graph = graph
        # Nothing to reconcile against the catalogue: audiences live in the definitions,
        # and one naming a tool that does not exist was refused by `Offering.refuse_unknown`.
        self.access: SourceIds | None = self.cfg.access
        self.access_report: AccessReport = AccessReport()
        if self.access is not None:
            # Walked once for both calls: `defined_subagents` reads a directory.
            kinds = (
                ("agent", self.catalogue.agents.specs),
                ("subagent", defined_subagents(self.cfg, catalogue=self.catalogue)),
            )
            # Refusals first: a typo makes a line both undeclared and narrowing, and as a
            # narrowing it would explain the wrong fault. Tools are checked, not audited:
            # one stating no audience is whatever the agent granting it says.
            access.refuse_undeclared(
                *kinds, ("tool", self.catalogue.audienced_tools), vocabulary=self.access
            )
            self.access_report = access.audit(*kinds, vocabulary=self.access)

        # Last, so the line reports what was resolved, and a wiring failure raises
        # instead of announcing a deployment that never came up.
        if logger.isEnabledFor(logging.INFO):
            logger.info("reading from: %s", self.origins.line())

    @property
    def origins(self) -> Origins:
        """Where this deployment is actually reading from."""
        return Origins.of(
            self.cfg,
            catalogue=self.catalogue,
            sessions=self._sessions,
        )

    def _held_for(self, source_ids: Held | None) -> frozenset[str] | None:
        """The caller's expanded source ids, or `None` where nothing narrows."""
        return access.held_by(self.access, source_ids)

    def _effective_grants(self, source_ids: Held | None) -> Capabilities:
        """The ceiling for one call: this deployment's, narrowed by the caller's."""
        # For its refusals: an undeclared source id is refused here rather than quietly
        # reaching nothing. Source ids narrow per definition, in `AgentSpec.declares`.
        # No branch on `source_ids is not None`: `UNSCOPED` is not `None`, and must run
        # where there is no policy.
        access.caller_holds(self.access, source_ids)
        return self.grants

    def _graph_for(  # noqa: PLR0913 -- setup holds each; deriving one here derives it twice
        self,
        request: Request | Resume,
        capabilities: Capabilities,
        checkpointer: Any = None,
        *,
        agent: AgentSpec | None,
        held: frozenset[str] | None,
        files: Any,
        ledger: Ledger | None = None,
    ) -> Any:
        """What serves one request, rooted at its session.

        `Assembled` for a build made here; a supplied graph comes back as itself on this
        turn's saver, because an `Assembled` for it would assert a build that never ran.
        """
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            # On this turn's saver, never its own: its own would replay every earlier turn
            # beside the transcript, or keep no state to write one, or lose a pause.
            return self._graph.copy(update={"checkpointer": checkpointer})

        return build_agent(
            self.cfg,
            # Resolved by setup, which needs the same spec for the withheld report.
            agent=agent,
            held=held,
            # The one setup opened, holding the inputs: opened again, a remote backend
            # would be a second sandbox without them.
            backend=files,
            # Never `request.capabilities`, which would hand a caller a tool the
            # deployment withheld.
            capabilities=capabilities,
            run_on=request.run_on,
            middleware_registry=self.middlewares,
            instructions=self.instructions.text(self.cfg),
            checkpointer=checkpointer,
            catalogue=self.catalogue,
            ledger=ledger,
        )

    def _files_for(self, session_id: str) -> Steps[Any]:
        """The backend a session's files are reached through, by kingfisher and any agent it builds.

        What runs its commands is the sessions' choice: only they know a session's host.
        """
        files = yield changing(
            self._sessions, "open", self.cfg, session_id, catalogue=self.catalogue
        )
        # Here as well as at the build: a pre-built graph has none, and setup reads the
        # session through this backend before any build.
        refuse_unusable_backend(
            files, (*self.cfg.layout.denied_scopes(), *self.cfg.layout.denied_read_scopes())
        )
        return files

    def _pin_agent_in(self, harness: HarnessFiles, name: str | None) -> Steps[None]:
        """Keep the agent, in the session's own files.

        Never at a path rebuilt from the id: that is the session only under the default
        `session_root`, and a pin written elsewhere is never read back.
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

        `pinned` is the session's pin where the caller has read it already, as setup has.
        """
        kept = (yield from agent_started_with(harness)) if pinned is _UNSET else pinned
        if kept is None:
            spec = self._agent_named(request.agent, source_ids=source_ids)
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

    def _agent_named(
        self, name: str | None, *, source_ids: Held | None = None
    ) -> AgentSpec | None:
        """The agent this request asked for, out of the catalogue."""
        offered = self.catalogue.agents.specs
        # Filtered before the listing is built: an agent out of reach must read exactly
        # like one never written, or a caller could enumerate the catalogue by guessing.
        if (held := access.caller_holds(self.access, source_ids)) is not None:
            offered = {
                n: spec for n, spec in offered.items() if reaches(spec.source_ids, held)
            }
        listing = ", ".join(sorted(offered)) if offered else "none"
        # With nothing offered, a starter file as well as `SEED_HINT`, whose `--from DIR`
        # needs a DIR a reader who only installed the package may not have.
        root = self.catalogue.agents.root
        starter = "" if root is None else f", or write one:\n\n{starter_agent(root)}"
        empty = "" if offered else f" -- try {SEED_HINT}{starter}"
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
        behind; the claim is taken before them and given back if one refuses.
        """
        cfg = self.cfg
        session = yield from self._session_for(request)
        # Who is calling, before the session is marked, claimed or written to: later, a
        # refused caller's files would be in its `/inputs`, and a session out of reach
        # would answer "busy" rather than "no session". Asked here only for its refusals.
        self._effective_grants(source_ids)
        # Opened before anything else reads the session: who may touch it is decided by
        # its pinned agent, and that is read through the backend too.
        if files is None:
            files = yield from self._files_for(session.id)
        harness = HarnessFiles(files, session.id)
        held = self._held_for(source_ids)
        # Read once, for reach here and for the agent the turn runs; neither asks for a
        # supplied graph under no policy.
        pinned = (
            (yield from agent_started_with(harness))
            if held is not None or self._graph is None
            else None
        )
        if not self._reaches_pin(pinned, held):
            raise unknown_session(session.id)
        # A turn writes inside the session, which leaves the timestamp `retention.expired`
        # reads alone. At the top of the turn, not the end: a turn that fails still happened.
        yield changing(self._sessions, "mark_used", cfg, session.id)
        # Before the other refusals: those read the session, and a turn arriving halfway
        # through would be reading it as it moved.
        claimed = yield changing(
            self._sessions, "claim", cfg, session.id, stale_after=cfg.claim_stale_after
        )
        if not claimed:
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
            yield changing(self._sessions, "release", cfg, session.id)
            raise

    def _claimed(  # noqa: PLR0913 -- setup's, handed on: a second backend is a second sandbox
        self,
        request: Request | Resume,
        session: Conversation,
        *,
        files: Any,
        harness: HarnessFiles,
        pinned: Any,
        source_ids: Held | None = None,
    ) -> Steps[Prepared]:
        """The rest of setup, once the session is claimed."""
        # Optional on a backend, like `host_path`; reported rather than raised.
        unprotected = tuple(getattr(files, "unprotected", ()))

        # Before the inputs are placed: placing them grows the session.
        yield from self._refuse_if_over_budget(session)

        # Before the turn exists, so a missing file fails with nothing placed.
        placement = yield from place_inputs(
            getattr(request, "inputs", ()), files, self.cfg.layout
        )

        granted = self._effective_grants(source_ids)
        allowed = granted.intersect(request.capabilities)
        held = self._held_for(source_ids)
        # Before the build: a resume runs on a saver that already holds the pause.
        paused, resume, discarded = yield from self._take_pause(request, session, harness)
        # Not resolved for a supplied graph with no policy: that would refuse a
        # request naming another agent.
        agent = (
            (yield from self._agent_for(request, harness, source_ids=source_ids, pinned=pinned))
            if self._graph is None or held is not None
            else None
        )
        if self._graph is None:
            # Before the build and the reports, which must both see a bare name settled to
            # the one this caller reaches.
            allowed = settled_request(
                self.cfg,
                allowed,
                ceiling=for_the_caller(agent, granted, held=held).subagents,
                held=held,
                catalogue=self.catalogue,
            )
        history = yield from read_transcript(harness)
        # Only for a graph built here: a supplied one has nothing hooked to record into it.
        earlier = (yield from read_ledger(harness)) if self._graph is None else None
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
                earlier=earlier,
            )
        )

    def _take_pause(
        self, request: Request | Resume, session: Conversation, harness: HarnessFiles
    ) -> Steps[tuple[bytes | None, dict[str, Any] | None, tuple[str, ...]]]:
        """Whatever an earlier turn left waiting: the state and answers a resume restores,
        or the tools a new request superseded.

        Both before the graph exists: an answer needs the saver holding its state, and a
        supersede needs that state gone before a turn runs on it.
        """
        held = yield from read_pause_mark(harness)
        if not isinstance(request, Resume):
            if held is None:
                return None, None, ()
            # Superseded: `PatchToolCallsMiddleware` tells the model the unanswered call
            # was cancelled, and the caller is told by `decision_discarded`.
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
        # The ids read back, not re-derived: they are the ones the caller was handed, and
        # re-deriving would need the graph, which does not exist yet.
        return state, runtime.resume_payload(request.decisions, pending_from_mark(held)), ()

    def _refuse_stale_pause(
        self, session: Conversation, held: Mapping[str, str], request: Resume
    ) -> None:
        """Refuse a resume the paused graph would not be the same graph for.

        Checked rather than attempted: a pause outliving a deploy is ordinary, and a
        failed deserialise is a traceback about a node nobody has heard of.
        """
        was = held.get(AGENT_MARK) or None
        # `None` is no mismatch: nothing was resolved, for an injected graph under no
        # policy, where a request naming any agent is not refused either.
        if was is not None and request.agent is not None and was != request.agent:
            msg = (
                f"session {session.id} paused under agent {was!r}, "
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

    def _checkpointer(self) -> Any:
        """The saver this turn runs on, empty, or `None` where nothing is remembered.

        Always new: the transcript carries the conversation, so a saver that remembered
        earlier turns would hand the model each of them twice.
        """
        return build_session_checkpointer() if self.cfg.conversation_enabled else None

    def _built_turn(  # noqa: PLR0913 -- everything setup decided, for its on-host part
        self,
        request: Request | Resume,
        session: Conversation,
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
        earlier: KeptLedger | None = None,
    ) -> Prepared:
        """The saver, the graph and the turn itself: what setup builds rather than asks
        for. On kingfisher's pool for `astream`, because building the agent is CPU-bound
        and on the loop would hold every other turn up.
        """
        cfg = self.cfg
        checkpointer = self._checkpointer()
        graph = None
        try:
            saver = checkpointer if paused is None else self._restored(session, paused)
            # Before the build, because every row the graph records names the turn.
            turn = session.allocate_turn(request.turn_id)
            ledger = (
                Ledger(turn.id, earlier, resumed=resume is not None)
                if earlier is not None
                else None
            )
            built = self._graph_for(
                request,
                capabilities=allowed,
                checkpointer=saver,
                agent=agent,
                held=held,
                files=files,
                ledger=ledger,
            )
            # `isinstance`, not a duck test on `.graph`, which would accept anything
            # carrying one.
            graph = built.graph if isinstance(built, Assembled) else built
            # Tools are read off the graph, the only honest answer to what was offered;
            # skills and subagents are asked of the functions `build_agent` asked.
            withheld = withheld_by_kind(
                allowed,
                cfg,
                graph,
                self.catalogue,
                # Only under a vocabulary: without one the filter is a no-op, and an
                # injected graph has no spec to give.
                agent=agent if held is not None else None,
                held=held,
            )
            shared = subagent_only(allowed, cfg, catalogue=self.catalogue)
            # The agent and the caller as well as the grant: the grant alone may be
            # `"*"`, naming subagents the build never activated.
            indistinct = indistinct_subagents(
                cfg,
                allowed,
                catalogue=self.catalogue,
                run_on=request.run_on,
                agent=agent,
                held=held,
            )
            logger = RunLogger(
                self.run_events,
                model=cfg.models.default,
                endpoint=cfg.models.resolve()[0].endpoint,
                session_id=session.id,
                turn_id=turn.id,
            )
            # A resume says so in `run_start`: a task-shaped one would read as the work
            # being requested again.
            asked = getattr(request, "task", "")
            started = asked or f"resuming {len(getattr(request, 'decisions', ()))} decision(s)"
            logger.run_start(started)
        except BaseException:
            # Built and not yet handed over, so nothing else will let go of them.
            if graph is not None:
                release_interpreter(cfg, graph)
            raise

        return Prepared(
            graph=graph,
            files=files,
            harness=harness,
            saver=saver,
            context=built.context if isinstance(built, Assembled) else None,
            resume=resume,
            discarded=discarded,
            # Never `request.agent`, which is set only on the turn that opened the session.
            agent_name=agent.name if agent is not None else None,
            history=history,
            ledger=ledger,
            # A resume adds no message: it continues a superstep, and a user turn
            # appended there would be one the model never saw asked.
            message=turn_message(asked, placement.placed, earlier, layout=self.cfg.layout)
            if asked
            else "",
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
                # At the start of the superseding turn: a fact about this turn, not the
                # terminal state of the one it replaced.
                *((decision_discarded(discarded),) if discarded else ()),
            ),
            deadline=monotonic() + cfg.turn_timeout_s,
            timeout_s=cfg.turn_timeout_s,
            unprotected=unprotected,
            placement=placement,
            withheld=withheld,
            indistinct=indistinct,
            subagent_only=shared,
        )

    def _restored(self, session: Conversation, state: bytes) -> Any:
        """The saver a paused turn left, for its resume to run on."""
        try:
            return resumed_saver(state)
        except (ValueError, NotImplementedError) as exc:
            # A backend has no rename, so a crash can leave part of a pause; this names
            # the session rather than leaving msgpack to.
            msg = f"session {session.id} recorded a pause whose state cannot be read: {exc}"
            raise DecisionError(msg) from exc

    def _keep(self, prepared: Prepared, snapshot: Any) -> Steps[tuple[str, ...]]:
        """Persist what this turn produced, and name it."""
        yield from self._record_transcript(prepared, snapshot)
        return (yield from collect_artifacts(prepared.files, self.cfg.layout))

    def _finished(  # noqa: PLR0913 -- a parameter object would only be unpacked a line later
        self,
        prepared: Prepared,
        answer: str,
        kept: tuple[str, ...],
        *,
        stop_reason: str,
        waiting: tuple[PendingDecision, ...] = (),
        discarded: tuple[str, ...] = (),
        ledger: tuple[LedgerEntry, ...] = (),
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
                # Collected after the graph finished, so it includes what the shell
                # wrote, which no file tool would have reported.
                artifacts=kept,
                stop_reason=stop_reason,
                ledger=ledger,
            ),
        )

    def _settled(self, prepared: Prepared) -> Any:
        """This turn's final graph state, or `None` where there is none.

        Read once for both the transcript and the pause: two reads could come apart.
        """
        read = getattr(prepared.graph, "get_state", None)
        if read is None:
            return None
        try:
            # The pause has to be visible here, not only on the stream chunk `run`
            # never sees.
            return read(prepared.config)
        except ValueError:
            # `No checkpointer set`: an injected graph that keeps no state. Only this
            # type, so a graph that genuinely cannot answer still says so.
            return None

    def _record_ledger(
        self, prepared: Prepared, snapshot: Any
    ) -> Steps[tuple[LedgerEntry, ...]]:
        """Keep what this turn's tools returned, whether or not the turn paused."""
        if prepared.ledger is None:
            return ()
        return (
            yield from keep_ledger(
                prepared.files,
                prepared.harness,
                prepared.ledger,
                summarised=runtime.summarised_in(snapshot),
                tool_results_route=self.cfg.layout.tool_results_route,
            )
        )

    def _record_transcript(self, prepared: Prepared, snapshot: Any) -> Steps[None]:
        """Write what was said this turn, as records this package owns."""
        if not self.cfg.conversation_enabled:
            return
        if snapshot is None:
            # No state, or a turn that died before its first superstep: this runs after
            # every turn, failed ones included.
            return
        messages = snapshot.values.get("messages")
        if messages:
            yield from write_transcript(prepared.harness, runtime.as_transcript(messages))

    def _settle_pause(
        self, prepared: Prepared, snapshot: Any
    ) -> Steps[tuple[PendingDecision, ...]]:
        """Keep a paused turn's graph state, or clear a pause this turn finished.

        Written here and nowhere else, so the file's presence stays a truthful mark. Only
        a resume has a pause left to clear: setup dropped any a new request superseded.
        """
        harness = prepared.harness
        waiting = runtime.pending_in(snapshot) if snapshot is not None else ()
        if not waiting or prepared.saver is None:
            # Including the turn that was just resumed and ran to the end.
            if prepared.resume is not None:
                yield from clear_pause(harness)
            return ()
        yield from harness.store(PAUSED_STATE, (yield on_host(paused_state, prepared.saver)))
        # The state before the mark, which is what says a session is waiting: a write
        # dying between them leaves a session not paused, not one with nothing to resume.
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
        datazone: str | None = None,
        files: Any = None,
    ) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        A `Resume` comes through here too, so the admission checks have one door.
        `files` is the session's backend from `files_for`, for a caller that uses it
        around the turn; the request must name that session.
        """
        # Coerced here: setup reads the session id first, and a bare string has none.
        yield from self._stream_turn(
            _asked(request), source_ids=with_datazone(source_ids, datazone), files=files
        )

    @contextmanager
    def _turn_outcome(self, turn: _Turn) -> Iterator[None]:
        """How a turn's graph loop ended, recorded on `turn`, for `stream` and `astream`."""
        try:
            yield
            turn.answer = normalize_answer(turn.answer)
            turn.ok = True
        except runtime.OutOfSteps:
            # `ok` stays true: it records that the caller was told how the turn ended.
            turn.answer = normalize_answer(turn.answer)
            turn.stop_reason = "max_steps"
            turn.ok = True
            turn.pending.append(out_of_steps(self.cfg))
        except Exception as exc:
            # Only a 401, which the person at the terminal can fix, becomes a one-line
            # error; the rest keep their traceback, so a bug still looks like one.
            if (refused := refused_credentials(exc, self.cfg)) is None:
                raise
            raise refused from exc

    def _end_turn(self, turn: _Turn) -> None:
        """Everything a turn does once it is over, for `stream`, which runs it inline."""
        drive(self._ending_steps(turn))

    async def _aend_turn(self, turn: _Turn) -> None:
        """Everything a turn does once it is over, for `astream`.

        A task of its own, which no cancel reaches, so every step runs; not a thread,
        because on the pool concurrent turns queued for these round trips. A task dies
        with its loop, which is safe only because `astream` always waits here.
        """
        _, cancelled = await finished(adrive(self._ending_steps(turn)))
        if cancelled is not None:
            raise cancelled

    def _ending_steps(self, turn: _Turn) -> Steps[None]:
        """What a turn does once it is over, however it ended."""
        prepared = turn.prepared
        yield on_host(prepared.logger.run_end, ok=turn.ok, answer_chars=len(turn.answer))
        # Before anything is let go of: the saver still holds the paused state.
        snapshot = yield on_host(self._settled, prepared)
        try:
            # Before the pause as well as the transcript: a resume carries on from that
            # state, and a result in either must already have its row.
            turn.recorded = yield from self._record_ledger(prepared, snapshot)
            turn.awaiting = yield from self._settle_pause(prepared, snapshot)
            if turn.awaiting:
                turn.pending.append(decision_needed(turn.awaiting))
                # A bound that already fired keeps its reason: `stop_reason` says why
                # the turn ended, and the pending calls are reported either way.
                if turn.stop_reason == END_TURN:
                    turn.stop_reason = AWAITING
            # Off the state, not the stream, so it is the agent's last reply and never a
            # subagent's, whose cut answer is its caller's to read.
            if turn.stop_reason == END_TURN and (cut := runtime.ended_cut_off(snapshot)):
                turn.stop_reason = "max_tokens"
                turn.pending.append(out_of_tokens(cut))
            turn.kept = yield from self._keep(prepared, snapshot)
        finally:
            # However the turn ended, and even if the store was unreachable.
            yield changing(self._sessions, "release", self.cfg, prepared.session.id)
        # Left open, the QuickJS runtime hangs the process rather than leaking a handle.
        yield on_host(release_interpreter, self.cfg, prepared.graph)

    def _read(self, turn: _Turn, namespace: Any, mode: Any, chunk: Any) -> tuple[RunEvent, ...]:
        """One stream chunk, read as events. The answer accumulates on `turn`."""
        turn.answer, events = consume(
            namespace, mode, chunk, turn.answer, turn.subagent_names, seen=turn.seen
        )
        return events

    def _payload(self, turn: _Turn) -> Any:
        """What the graph is driven with: a conversation, or the answers a resume re-enters
        the stopped node with, rather than asking the same question again."""
        if turn.prepared.resume is not None:
            return turn.prepared.resume
        return runtime.user_payload(turn.prepared.message, turn.prepared.history)

    def _driving(self, turn: _Turn) -> dict[str, Any]:
        """The keywords both graph streams are driven with, shared so neither loses
        `subgraphs` and with it a subagent's tokens."""
        driving: dict[str, Any] = {
            "config": turn.prepared.config,
            "stream_mode": runtime.STREAM_MODES,
            "subgraphs": True,
        }
        if turn.prepared.context is not None:
            # Here, not beside the payload, so a resume carries it: no checkpoint holds
            # it. Left off rather than `None` for a supplied graph, which may refuse it.
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
                ledger=turn.recorded,
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
                # Inside, not before: a caller that stops reading during these must still
                # end the turn.
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
        """The same turn on the graph's async stream; an `AsyncGenerator` so `astream` can
        `aclose` it."""
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
        datazone: str | None = None,
        delete_session: bool = False,
        files: Any = None,
    ) -> RunResult:
        """Run one task to completion. A drain of `stream`.

        `delete_session=True` disposes of the session once the turn has completed; one
        stopped at a bound keeps it, as a retry is rebuilt from it. A failed deletion is
        `deletion_failure` on the result. Not on `stream`: a generator's `finally` also
        fires when a caller stops reading early, which would delete the session.
        """
        result: RunResult | None = None
        for event in self.stream(request, source_ids=source_ids, datazone=datazone, files=files):
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
        datazone: str | None = None,
        files: Any = None,
    ) -> AsyncIterator[RunEvent]:
        """`stream`, for a caller already on an event loop. Cancelling is immediate.

        Middleware runs through its `a`-prefixed hooks here, so one written only as
        `wrap_model_call` raises the first time this reaches it.
        """
        turn = self._astream_turn(
            _asked(request), source_ids=with_datazone(source_ids, datazone), files=files
        )
        try:
            async for event in turn:
                yield event
        finally:
            # By hand: a dropped async generator waits for the loop to finalise it, and
            # until then a caller who stopped reading leaves the session claimed.
            await turn.aclose()

    async def arun(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        delete_session: bool = False,
        files: Any = None,
    ) -> RunResult:
        """`run`, for a caller already on an event loop. A drain of `astream`.

        No `aclose` to match `astream`'s: a cancelled drain gives the claim back one turn
        of the loop later either way.
        """
        events = self.astream(request, source_ids=source_ids, datazone=datazone, files=files)
        result: RunResult | None = None
        async for event in events:
            if event.kind == "finished":
                result = event.result
        return await adrive(self._drained_steps(result, delete_session=delete_session))

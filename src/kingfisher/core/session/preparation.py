"""A turn before its agent runs: the session claimed, the caller checked, the inputs
placed, a pause restored, and the graph built."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.access.source_ids import Held, zone_held
from kingfisher.core.agent.activation import (
    for_the_caller,
    indistinct_subagents,
    settled_request,
)
from kingfisher.core.agent.assembly import Assembled
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.config import Config
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.layout import PAUSED_STATE
from kingfisher.core.reporting import (
    opening_events,
    subagent_only,
    withheld_by_kind,
)
from kingfisher.core.request import DecisionError, Request, Resume
from kingfisher.core.session import runtime
from kingfisher.core.session.checkpointing import (
    build_session_checkpointer,
    harness_mark,
    resumed_saver,
)
from kingfisher.core.session.files import place_inputs, planned_inputs
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.model import (
    Conversation,
    SessionBusyError,
    unknown_session,
)
from kingfisher.core.session.runlog import RunLogger
from kingfisher.core.session.steps import (
    Steps,
    changing,
    on_host,
)
from kingfisher.core.session.store import (
    AGENT_MARK,
    HarnessFiles,
    KeptLedger,
    clear_pause,
    pending_from_mark,
    pinned_in,
    read_ledger,
    read_pause_mark,
    read_transcript,
    remember_zone,
)
from kingfisher.core.session.turn import (
    Prepared,
    decision_discarded,
    turn_message,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.core.interfaces import RunEvents


class Preparation:
    """Everything a turn does before its agent runs, as the one sequence both loops drive."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    catalogue: Definitions
    run_events: RunEvents
    _sessions: Any
    _graph: Any
    _session_for: Callable[..., Steps[Conversation]]
    _reaches_pin: Callable[..., bool]
    _refuse_if_over_budget: Callable[..., Steps[None]]
    _files_for: Callable[..., Steps[Any]]
    _effective_grants: Callable[..., Capabilities]
    _held_for: Callable[..., frozenset[str] | None]
    _agent_for: Callable[..., Steps[AgentSpec | None]]
    _graph_for: Callable[..., Any]

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
        pinned, zone = (
            (yield from pinned_in(harness))
            if held is not None or self._graph is None
            else (None, None)
        )
        if not self._reaches_pin(pinned, zone, held):
            raise unknown_session(session.id)
        # A turn writes inside the session, which leaves the timestamp `retention.expired`
        # reads alone. At the top of the turn, not the end: a turn that fails still happened.
        yield changing(self._sessions, "mark_used", cfg, session.id)
        # Before the other refusals: those read the session, and a turn arriving halfway
        # through would be reading it as it moved.
        claimed = yield changing(
            self._sessions, "claim", cfg, session.id, stale_after=cfg.claim_stale_after
        )
        if claimed is None:
            msg = (
                f"session {session.id} already has a turn running; "
                f"wait for it to finish or start another session"
            )
            raise SessionBusyError(msg)
        try:
            prepared = yield from self._claimed(
                request,
                session,
                files=files,
                harness=harness,
                pinned=pinned,
                zone=zone,
                source_ids=source_ids,
            )
        except BaseException:
            # A call that changes something, so a cancel arriving during it waits for it.
            yield changing(self._sessions, "release", cfg, session.id, claimed)
            raise
        return replace(prepared, claim=claimed)

    def _claimed(  # noqa: PLR0913 -- setup's, handed on: a second backend is a second sandbox
        self,
        request: Request | Resume,
        session: Conversation,
        *,
        files: Any,
        harness: HarnessFiles,
        pinned: Any,
        zone: str | None,
        source_ids: Held | None = None,
    ) -> Steps[Prepared]:
        """The rest of setup, once the session is claimed."""
        # Optional on a backend, like `host_path`; reported rather than raised.
        unprotected = tuple(getattr(files, "unprotected", ()))

        # Before the inputs are placed: placing them grows the session.
        yield from self._refuse_if_over_budget(session)

        # Before the turn exists, so a missing file fails before anything else is asked.
        placement = yield from planned_inputs(
            getattr(request, "inputs", ()), files, self.cfg.layout
        )

        granted = self._effective_grants(source_ids)
        allowed = granted.intersect(request.capabilities)
        held = self._held_for(source_ids)
        # Before the build: a resume runs on a saver that already holds the pause.
        paused, resume, discarded, superseded = yield from self._take_pause(
            request, session, harness
        )
        # Not resolved for a supplied graph with no policy: that would refuse a
        # request naming another agent.
        agent = (
            (yield from self._agent_for(request, harness, source_ids=source_ids, pinned=pinned))
            if self._graph is None or held is not None
            else None
        )
        # Beside the agent's pin and before any tool runs: what a zoned caller's tools
        # return stays in the session, so from here it is read only from that datazone.
        if zone is None and held is not None and (ran_in := zone_held(held)) is not None:
            yield from remember_zone(harness, ran_in)
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
        prepared = yield on_host(
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
        # After the build's refusals and before the graph runs: a refused request must
        # leave the session's files and its gate as they were. The inputs first: a caller
        # can send a file again, and nobody can recreate a pause.
        try:
            yield from place_inputs(placement, files, self.cfg.layout)
            if superseded:
                yield from clear_pause(harness)
        except BaseException:
            # Built and not yet handed over, so no ending will let go of it.
            release_interpreter(self.cfg, prepared.graph)
            raise
        return prepared

    def _take_pause(
        self, request: Request | Resume, session: Conversation, harness: HarnessFiles
    ) -> Steps[tuple[bytes | None, dict[str, Any] | None, tuple[str, ...], bool]]:
        """Whatever an earlier turn left waiting: the state and answers a resume restores,
        or the tools a new request supersedes and whether there is a pause to drop.

        Before the graph exists: an answer needs the saver holding its state. A supersede
        is dropped by the caller, once the build has had its chance to refuse.
        """
        held = yield from read_pause_mark(harness)
        if not isinstance(request, Resume):
            if held is None:
                return None, None, (), False
            # Superseded: `PatchToolCallsMiddleware` tells the model the unanswered call
            # was cancelled, and the caller is told by `decision_discarded`.
            return None, None, tuple(item.tool for item in pending_from_mark(held)), True
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
        return state, runtime.resume_payload(request.decisions, pending_from_mark(held)), (), False

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

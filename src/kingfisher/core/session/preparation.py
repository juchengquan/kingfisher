"""A turn before its agent runs: the session claimed, the caller checked, the inputs
placed, a pause restored, and the graph built."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import Capabilities, Held, caller_holds
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
from kingfisher.core.layout import INPUTS_SEEN, PAUSED_STATE
from kingfisher.core.logged import stamp_warnings
from kingfisher.core.reporting import (
    opening_events,
    subagent_only,
    withheld_by_kind,
)
from kingfisher.core.request import DecisionError, Request, Resume, caller_of
from kingfisher.core.versions import built_from

from . import runtime
from .checkpointing import (
    build_session_checkpointer,
    library_versions,
    resumed_saver,
)
from .files import InputsPlacement, inputs_changed, inputs_seen, place_inputs, planned_inputs
from .ledger import Ledger
from .memory import UsersMemory, as_file, settled_memory, users_memory
from .memory_writer import writer_model
from .model import (
    Conversation,
    Turn,
    TurnIdError,
    refuse_files_of_another,
)
from .runlog import RunLogger
from .steps import (
    Steps,
    changing,
    on_host,
)
from .store import (
    AGENT_MARK,
    BUILT_FROM_MARK,
    TAKEN_MARK,
    HarnessFiles,
    KeptLedger,
    clear_pause,
    pending_from_mark,
    read_drift,
    read_ledger,
    read_listing,
    read_pause_mark,
    take_pause,
)
from .turn import (
    Prepared,
    decision_discarded,
    history_lost,
    not_offered,
    turn_message,
)
from .turnlog import TurnLog, logged_turns, read_log, resuming

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.core.agent.tool_context import Caller
    from kingfisher.core.interfaces import MemoryStore, RunEvents
    from kingfisher.core.versions import Versions


class Preparation:
    """Everything a turn does before its agent runs, as the one sequence both loops drive."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _versions: Versions
    grants: Capabilities
    run_events: RunEvents
    memory_store: MemoryStore | None
    _sessions: Any
    _graph: Any
    _session_for: Callable[..., Steps[Conversation]]
    _refuse_if_over_budget: Callable[..., Steps[None]]
    _open_files: Callable[..., Steps[Any]]
    _known_steps: Callable[[], Steps[tuple[str, ...]]]
    _claim_or_refuse: Callable[[str, str], Steps[Any]]
    _agent_named: Callable[..., AgentSpec]
    _graph_for: Callable[..., Any]

    def _prepare_steps(
        self,
        request: Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> Steps[Prepared]:
        """Everything up to the model call, as the one sequence both loops drive.

        Every refusal comes before `run_start`, so a refused request leaves no turn behind;
        the claim is taken before them and given back if one refuses, and a session this
        request made goes with it unless another turn has used it.
        """
        cfg = self.cfg
        # Once, and kept to the turn's end: asked again, one turn could build its agent
        # from one set of definitions and its tools or subagents from the next. On the
        # host, since a changed folder is read and checked while the turn waits.
        catalogue = yield on_host(self._versions.current)
        session = yield from self._session_for(request)
        # Who is calling, before the session is marked, claimed or written to: later, a
        # refused caller's files would be in its `/inputs`.
        held = caller_holds(catalogue.vocabulary, source_ids)
        # Not resolved for a supplied graph where nothing is locked: it runs no agent from
        # the definitions, so a request there need name none.
        resolved = self._graph is None or held is not None
        # The agent as well, since nothing in the session decides it. A resume naming none
        # is left to its pause, which can say which agent it waits for.
        unnamed_resume = isinstance(request, Resume) and request.agent is None
        agent = (
            self._agent_named(request.agent, held=held, catalogue=catalogue)
            if resolved and not unnamed_resume
            else None
        )
        if files is None:
            # Asked before the open, which makes the session it is asked for: a refusal
            # may take back only a session this request made.
            made = session.id not in (yield from self._known_steps())
            files = yield from self._open_files(session.id, catalogue=catalogue)
        else:
            made = False
            refuse_files_of_another(files, session.id)
        harness = HarnessFiles(files)
        # A turn writes inside the session, which leaves the timestamp `kingfisher sessions`
        # reads alone. At the top of the turn, not the end: a turn that fails still happened.
        yield changing(self._sessions, "mark_used", cfg, session.id)
        # Before the other refusals: those read the session, and a turn arriving halfway
        # through would be reading it as it moved.
        claimed = yield from self._claim_or_refuse(session.id, "wait, or start another session")
        try:
            log = yield from read_log(harness)
            if log.written:
                # Written only by a turn holding the claim, so another turn ran here,
                # whatever the listing said: not a session this request made, nor its to
                # delete.
                made = False
            prepared = yield from self._claimed(
                request,
                session,
                files=files,
                harness=harness,
                held=held,
                catalogue=catalogue,
                agent=agent,
                log=log,
            )
        except BaseException:
            # Each a call that changes something, so a cancel arriving during it waits for
            # it. The delete before the release: once the claim is back, a turn may start
            # in what is being removed.
            if made:
                yield changing(self._sessions, "delete", cfg, session.id)
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
        held: frozenset[str] | None,
        catalogue: Definitions,
        agent: AgentSpec | None,
        log: TurnLog,
    ) -> Steps[Prepared]:
        """The rest of setup, once the session is claimed."""
        # Optional on a backend, like `host_path`; reported rather than raised.
        unprotected = tuple(getattr(files, "unprotected", ()))

        # Before the inputs are placed: placing them grows the session.
        yield from self._refuse_if_over_budget(session, harness)

        # Before the turn exists, so a missing file fails before anything else is asked.
        placement = yield from planned_inputs(
            getattr(request, "inputs", ()), files, self.cfg.layout
        )

        allowed = self.grants.intersect(request.capabilities)
        # Before the build: a resume runs on a saver that already holds the pause.
        paused, resume, discarded, pause = yield from self._read_pause(
            request, session, harness
        )
        if agent is None and (self._graph is None or held is not None):
            # A resume naming none, past a pause that named no agent to refuse it with.
            agent = self._agent_named(request.agent, held=held, catalogue=catalogue)
        recorded = pause.get(BUILT_FROM_MARK) if resume is not None and pause else None
        # A pause written before the record resumes unchecked, as every pause did then.
        if isinstance(recorded, dict) and agent is not None:
            _refuse_changed_since(session, recorded, built_from(catalogue, agent))
        if self._graph is None:
            # Before the build and the reports, which must both see a bare name settled to
            # the one this caller reaches.
            allowed = settled_request(
                allowed,
                ceiling=for_the_caller(agent, self.grants, held=held).subagents,
                held=held,
                catalogue=catalogue,
            )
        # Only for a graph built here: a supplied one has nothing hooked to record into it.
        earlier = (yield from read_ledger(harness, log.off_path)) if self._graph is None else None
        # The ledger, written before the log, names every turn the log opens; a supplied
        # graph keeps none, and the log is all that names its turns.
        taken = earlier.turns if earlier is not None else logged_turns(log)
        if request.turn_id in taken:
            # The ledger and the log name a turn only by its id: a second turn under one
            # would take the first one's place in the log, and its copies of files.
            msg = (
                f"session {session.id} already has a turn {request.turn_id!r}; "
                "give each turn an id of its own"
            )
            raise TurnIdError(msg)
        # Before the build, because every row the graph records names the turn; and before
        # the memory store is asked, so that a warning from here on names it too.
        turn = session.allocate_turn(request.turn_id, taken=taken)
        stamp_warnings(
            session_id=session.id,
            turn_id=turn.id,
            turn_agent=agent.name if agent is not None else None,
        )
        lost = history_lost(
            len(log.conversation),
            cut=log.damaged,
            written=earlier.messages if earlier is not None else None,
        )
        # One for the turn, so the store is asked about the caller its tools are handed; and
        # one start, so the tools and the turn log agree on when the turn began.
        started = datetime.now(UTC)
        caller = caller_of(request, held, started)
        remembers = self._graph is None and self.cfg.memory_enabled and (
            for_the_caller(agent, allowed, held=held).memory is not False
        )
        # Before the build, which tells the agent whose memory it is.
        users = (
            (yield from users_memory(self.memory_store, caller))
            if remembers and self.memory_store is not None
            else UsersMemory()
        )
        prepared = yield on_host(
            self._built_turn,
            request,
            session,
            turn=turn,
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
            log=log,
            earlier=earlier,
            caller=caller,
            started=started.timestamp(),
            users_memory=users.key is not None,
            catalogue=catalogue,
        )
        if lost is not None:
            prepared = replace(prepared, events=(*prepared.events, lost), history_lost=lost.text)
        # After the build's refusals and before the graph runs: a refused request must
        # leave the session's files and its gate as they were: a caller can send a file
        # again, and nobody can recreate a pause. `run_start` last, so a refused request
        # logs no turn.
        try:
            yield from place_inputs(placement, files, self.cfg.layout)
            # A resume adds no message: it continues a superstep, and a user turn
            # appended there would be one the model never saw asked.
            asked = getattr(request, "task", "")
            if asked:
                prepared = yield from self._with_message(
                    prepared, asked, request, placement, earlier=earlier
                )
            if pause is not None and resume is None:
                yield from clear_pause(harness)
            # Before the graph runs, which reads the file: numbered now, so what the
            # turn's end compares with is entries rather than lines nobody has named. The
            # user's entries first, so another session's changes are this turn's baseline.
            if remembers:
                if users.loaded is not None:
                    yield changing(
                        files,
                        "upload_files",
                        [(self.cfg.layout.memory_file_route, as_file(users.loaded).encode())],
                    )
                prepared = replace(
                    prepared,
                    memory=(yield from settled_memory(files, self.cfg.layout)),
                    memory_key=users.key,
                    events=(*prepared.events, *users.events),
                )
            if pause is not None and resume is not None:
                # Last before the graph runs: a resume refused before here leaves its
                # pause answerable, and one killed after it cannot run the calls twice.
                yield from take_pause(harness, pause, prepared.turn.id)
            # A resume says so in `run_start`: by its size alone it would read as a request
            # that asked nothing.
            yield on_host(
                prepared.logger.run_start,
                asked,
                decisions=len(request.decisions) if isinstance(request, Resume) else None,
            )
        except BaseException:
            # Built and not yet handed over, so no ending will let go of it.
            release_interpreter(self.cfg, prepared.graph)
            raise
        return prepared

    def _with_message(
        self,
        prepared: Prepared,
        asked: str,
        request: Request | Resume,
        placement: InputsPlacement,
        *,
        earlier: KeptLedger | None,
    ) -> Steps[Prepared]:
        """`prepared` with its message, saying what changed in the inputs folder since the
        agent last heard, however it changed.
        """
        layout = self.cfg.layout
        history = prepared.log.conversation
        now = yield from inputs_seen(prepared.files, layout)
        # A first turn was told of nothing; a session older than the record, of unknown.
        before = (yield from read_listing(prepared.harness, INPUTS_SEEN)) if history else {}
        drift = yield from read_drift(prepared.harness)
        message = turn_message(
            asked,
            inputs_changed(before, now, placement),
            earlier,
            layout=layout,
            note=getattr(request, "note", ""),
            unavailable=not_offered(history, prepared.granted),
            drift=drift,
        )
        return replace(prepared, message=message, inputs_seen=now, drift=drift)

    def _read_pause(
        self, request: Request | Resume, session: Conversation, harness: HarnessFiles
    ) -> Steps[
        tuple[bytes | None, dict[str, Any] | None, tuple[str, ...], dict[str, Any] | None]
    ]:
        """Whatever an earlier turn left waiting: the state and answers a resume restores,
        or the tools a new request supersedes; and the pause's mark, or `None`.

        Before the graph exists: an answer needs the saver holding its state. A supersede
        is dropped, and a resume takes its pause, once the build has had its chance to
        refuse.
        """
        held = yield from read_pause_mark(harness)
        if not isinstance(request, Resume):
            # Superseded: `PatchToolCallsMiddleware` tells the model the unanswered call
            # was cancelled, and the caller is told by `decision_discarded`. A taken pause
            # names none: its calls were answered, and may have run.
            return None, None, tuple(item.tool for item in pending_from_mark(held or {})), held
        if held is None:
            msg = f"session {session.id} is not waiting on a decision"
            raise DecisionError(msg)
        if taken := held.get(TAKEN_MARK):
            msg = (
                f"session {session.id}'s decision was already answered by turn {taken}, "
                "which did not finish, so its approved calls may have run and are not run "
                "again; check what they did, then send a new request"
            )
            raise DecisionError(msg)
        self._refuse_stale_pause(session, held, request)
        state = yield from harness.fetch(PAUSED_STATE)
        if state is None:
            msg = f"session {session.id} recorded a pause whose state is missing"
            raise DecisionError(msg)
        # The ids read back, not re-derived: they are the ones the caller was handed, and
        # re-deriving would need the graph, which does not exist yet.
        return state, runtime.resume_payload(request.decisions, pending_from_mark(held)), (), held

    def _refuse_stale_pause(
        self, session: Conversation, held: Mapping[str, str], request: Resume
    ) -> None:
        """Refuse a resume the paused graph would not be the same graph for.

        Checked rather than attempted: a pause outliving a deploy is ordinary, and a
        failed deserialise is a traceback about a node nobody has heard of.
        """
        was = held.get(AGENT_MARK) or None
        # `None` is no mismatch: nothing was resolved, for an injected graph where nothing
        # is locked, where a request naming any agent is not refused either. Named back,
        # since every resume must name it and holding the session's id is the grant.
        if was is not None and request.agent != was:
            names = "names no agent" if request.agent is None else f"names {request.agent!r}"
            msg = f"session {session.id} paused under agent {was!r}, and this resume {names}"
            raise DecisionError(msg)
        now = library_versions()
        if moved := sorted(k for k, v in now.items() if k in held and held[k] != v):
            msg = (
                f"session {session.id} paused before {', '.join(moved)} moved "
                f"({', '.join(f'{k} {held[k]}->{now[k]}' for k in moved)}); "
                "the pause did not survive the upgrade and the turn must be asked again"
            )
            raise DecisionError(msg)

    def _built_turn(  # noqa: PLR0913 -- everything setup decided, for its on-host part
        self,
        request: Request | Resume,
        session: Conversation,
        *,
        turn: Turn,
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
        log: TurnLog,
        catalogue: Definitions,
        started: float,
        earlier: KeptLedger | None = None,
        caller: Caller | None = None,
        users_memory: bool = False,
    ) -> Prepared:
        """The saver, the graph and the turn's logger: what setup builds rather than asks
        for. On kingfisher's pool for `astream`, because building the agent is CPU-bound
        and on the loop would hold every other turn up.
        """
        cfg = self.cfg
        graph = None
        try:
            # New for every turn but a resume: the turn log carries the conversation, so
            # a saver that remembered earlier turns would hand the model each of them twice.
            saver = (
                build_session_checkpointer() if paused is None else self._restored(session, paused)
            )
            ledger = (
                Ledger(
                    turn.id,
                    earlier,
                    resumed=resume is not None,
                    keep_rounds=cfg.keep_results_rounds,
                )
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
                session_id=session.id,
                catalogue=catalogue,
                ledger=ledger,
                caller=caller,
                users_memory=users_memory,
            )
            # `isinstance`, not a duck test on `.graph`, which would accept anything
            # carrying one.
            graph = built.graph if isinstance(built, Assembled) else built
            # Now, before the resume runs on it and moves the state past the pause.
            config = {"configurable": {"thread_id": session.id}}
            answering = (
                resuming(graph, config, saver, request.decisions)
                if isinstance(request, Resume) and resume is not None
                else None
            )
            # Tools are read off the graph, the only honest answer to what was offered;
            # skills and subagents are asked of the functions `build_agent` asked.
            withheld = withheld_by_kind(
                allowed,
                cfg,
                graph,
                catalogue,
                # Only for a caller: without one the filter is a no-op, and an
                # injected graph has no spec to give.
                agent=agent if held is not None else None,
                held=held,
            )
            shared = subagent_only(allowed, catalogue=catalogue)
            # The agent and the caller as well as the grant: the grant alone may be
            # `"*"`, naming subagents the build never activated.
            indistinct = indistinct_subagents(
                cfg,
                allowed,
                catalogue=catalogue,
                run_on=request.run_on,
                agent=agent,
                held=held,
            )
            # The agent's own model where it names one, which is what this turn runs.
            running = cfg.models.resolve(agent.wanted if agent is not None else None)[0]
            logger = RunLogger(
                self.run_events,
                model=running.model,
                endpoint=running.endpoint,
                session_id=session.id,
                turn_id=turn.id,
                # Not `request.agent`, which on a supplied graph where nothing is locked is
                # checked by nothing and names no agent that runs.
                turn_agent=agent.name if agent is not None else None,
                endpoints={name: p.endpoint for name, p in cfg.models.models.items()},
            )
        except BaseException:
            # Built and not yet handed over, so nothing else will let go of them.
            if graph is not None:
                release_interpreter(cfg, graph)
            raise

        asked = getattr(request, "task", "")
        # One clock: what the turn is stopped at between chunks is what no run goes past.
        deadline = monotonic() + cfg.turn_timeout_s
        return Prepared(
            graph=graph,
            files=files,
            harness=harness,
            saver=saver,
            context=replace(built.context, deadline=deadline)
            if isinstance(built, Assembled)
            else None,
            granted=built.granted if isinstance(built, Assembled) else None,
            resume=resume,
            resuming=answering,
            discarded=discarded,
            agent_name=agent.name if agent is not None else None,
            built_from=built_from(catalogue, agent)
            if agent is not None and isinstance(built, Assembled)
            else None,
            log=log,
            ledger=ledger,
            # Written once the inputs are placed, which this must not wait for.
            message=(),
            session=session,
            turn=turn,
            logger=logger,
            task=asked,
            caller_context=request.context,
            # The endpoints the agent's own model was held to, so the writer reaches no
            # endpoint this caller may not.
            memory_model=writer_model(
                cfg, built.model, for_the_caller(agent, allowed, held=held).endpoints
            )
            if isinstance(built, Assembled)
            else None,
            config={
                **config,
                "callbacks": [logger],
                "recursion_limit": cfg.recursion_limit,
            },
            events=(
                *opening_events(turn.id, unprotected, placement, withheld, indistinct, shared),
                # At the start of the superseding turn: a fact about this turn, not the
                # terminal state of the one it replaced.
                *((decision_discarded(discarded),) if discarded else ()),
            ),
            deadline=deadline,
            timeout_s=cfg.turn_timeout_s,
            started=started,
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


#: How many changed files a refused resume names before it counts the rest.
NAMED = 3


def _refuse_changed_since(
    session: Conversation, recorded: Mapping[str, str], now: Mapping[str, str]
) -> None:
    """Refuse a resume whose agent's tools, skills, subagents or middleware changed since
    it paused: the turn was planned against what those files held then."""
    changed = sorted(
        path for path in recorded.keys() | now.keys() if recorded.get(path) != now.get(path)
    )
    if not changed:
        return
    more = f" and {len(changed) - NAMED} more" if len(changed) > NAMED else ""
    msg = (
        f"session {session.id} paused before {', '.join(changed[:NAMED])}{more} changed, "
        "which its agent uses; the pause did not survive the change and the turn must "
        "be asked again"
    )
    raise DecisionError(msg)

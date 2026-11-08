"""Running a turn and settling it: `run`, `stream` and their async twins, the graph's
stream, and what a turn leaves once it ends."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import Held, with_datazone
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.config import Config
from kingfisher.core.layout import PAUSED_STATE, REWOUND_FILE
from kingfisher.core.logged import TurnWarnings, warning_into
from kingfisher.core.request import Request, Resume
from kingfisher.core.result import (
    AWAITING,
    END_TURN,
    LedgerEntry,
    MemoryChanges,
    PendingDecision,
    RunEvent,
    RunResult,
    normalize_answer,
)

from . import runtime
from .checkpointing import (
    library_versions,
    paused_state,
)
from .file_history import keep_turn_files
from .files import collect_artifacts, walk_artifacts
from .ledger import file_left, keep_ledger, sweep_results
from .memory import kept_in_store
from .memory_writer import kept_memory
from .messages import joined
from .provider_errors import refused_request
from .steps import (
    Steps,
    adrive,
    adrive_finished,
    changing,
    drive,
    on_host,
)
from .store import (
    AGENT_MARK,
    BUILT_FROM_MARK,
    PENDING_MARK,
    clear_pause,
    pending_as_mark,
    write_inputs_seen,
    write_pause_mark,
)
from .threads import finished
from .turn import (
    Prepared,
    decision_needed,
    model_unavailable,
    out_of_steps,
    out_of_tokens,
    overrun,
)
from .turnlog import keep_turns

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, Callable, Iterable, Sequence

    from kingfisher.core.interfaces import MemoryStore, RunEvents, UnusedResults

    from .messages import Message



@dataclass
class _Turn:
    """What a turn accumulates: written by its loop, read by its lifecycle."""

    prepared: Prepared
    answer: str = ""
    ok: bool = False
    stop_reason: str = END_TURN
    artifacts: tuple[str, ...] = ()
    subagent_names: runtime.SubagentNames = field(default_factory=runtime.SubagentNames)
    seen: runtime.SeenMessages = field(default_factory=runtime.SeenMessages)
    delegations: runtime.Delegations = field(default_factory=runtime.Delegations)
    #: What the lifecycle produced and the loop still owes its caller: a context
    #: manager cannot yield into the generator around it.
    pending: list[RunEvent] = field(default_factory=list)
    #: Gated calls this turn stopped on; non-empty makes `stop_reason` `awaiting_decision`.
    awaiting: tuple[PendingDecision, ...] = ()
    #: What this turn's tools returned, as the lifecycle kept it.
    recorded: tuple[LedgerEntry, ...] = ()
    #: What the turn did to the memory file's entries.
    memory: MemoryChanges = field(default_factory=MemoryChanges)
    #: Why the ending could not keep all the turn did, or `None`.
    keep_failure: str | None = None


#: What the turn's stream hands back once it has nothing more.
_DONE = object()


def _within(
    heard: TurnWarnings, stream: Callable[..., Iterable[Any]], /, *args: Any, **kwargs: Any
) -> Iterator[Any]:
    """`stream(*args, **kwargs)`, each item made inside the turn's warnings and handed on
    outside them, where the caller's own code runs."""
    with warning_into(heard):
        items = iter(stream(*args, **kwargs))
    while True:
        with warning_into(heard):
            item = next(items, _DONE)
        if item is _DONE:
            return
        yield item


async def _awithin(
    heard: TurnWarnings, stream: Callable[..., AsyncIterable[Any]], /, *args: Any, **kwargs: Any
) -> AsyncIterator[Any]:
    """`_within`, for the graph's async stream."""
    with warning_into(heard):
        items = aiter(stream(*args, **kwargs))
    while True:
        with warning_into(heard):
            item = await anext(items, _DONE)
        if item is _DONE:
            return
        yield item


def _as_request(value: str | Request | Resume) -> Request | Resume:
    """Whatever a caller handed in, as one of the two things a turn starts from."""
    return value if isinstance(value, Request | Resume) else Request.coerce(value)


class Running:
    """A turn from its first model call to what it leaves, however it ends."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _sessions: Any
    _prepare_steps: Callable[..., Steps[Prepared]]
    _delete_session_steps: Callable[[str], Steps[str | None]]
    run_events: RunEvents
    unused_results: UnusedResults
    memory_store: MemoryStore | None

    def _keep_memory(self, turn: _Turn, snapshot: Any) -> Steps[None]:
        """The memory writer's pass over this turn, and the store's copy, where the session
        keeps memory."""
        prepared = turn.prepared
        if prepared.memory is None:
            return
        turn.memory, said = yield from kept_memory(
            prepared,
            snapshot,
            cfg=self.cfg,
            answer=turn.answer,
            ok=turn.ok,
            paused=bool(turn.awaiting),
        )
        turn.pending.extend(said)
        # Only what changed: the store merges it into what other sessions saved since.
        # A dropped entry is a removal to the store, or its next load brings it back.
        store = self.memory_store
        if turn.memory and prepared.memory_key is not None and store is not None:
            failed = yield from kept_in_store(
                store,
                prepared.memory_key,
                replace(
                    turn.memory,
                    removed=turn.memory.removed + turn.memory.dropped,
                    dropped=(),
                ),
                prepared.files,
                self.cfg.layout,
            )
            if failed is not None:
                turn.pending.append(failed)

    def _settled(self, prepared: Prepared) -> Any:
        """This turn's final graph state, or `None` where there is none.

        Read once for both the turn log and the pause: two reads could come apart.
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
        """Keep what this turn's tools returned, whether or not the turn paused, filing
        first what left the conversation with no file."""
        if prepared.ledger is None:
            return ()
        yield from file_left(
            prepared.files,
            prepared.ledger,
            runtime.summarised_away(snapshot),
            tool_results_route=self.cfg.layout.tool_results_route,
        )
        written = self._ended_on(snapshot)
        return (
            yield from keep_ledger(
                prepared.harness,
                prepared.ledger,
                summarised=runtime.summarised_in(snapshot),
                messages=len(written) if written else None,
            )
        )

    def _sweep_results(self, prepared: Prepared, recorded: tuple[LedgerEntry, ...]) -> Steps[None]:
        """Hand over the result files no row names, once nothing this turn writes can name
        one."""
        if prepared.ledger is None:
            return
        yield from sweep_results(
            prepared.files,
            prepared.ledger,
            recorded,
            tool_results_route=self.cfg.layout.tool_results_route,
            unused_results=self.unused_results,
            session_id=prepared.session.id,
        )

    def _ended_on(self, snapshot: Any) -> tuple[Message, ...]:
        """The conversation this turn ended on, or nothing where there is no state."""
        if snapshot is None:
            # No state, or a turn that died before its first superstep: this runs after
            # every turn, failed ones included.
            return ()
        messages = runtime.as_seen(snapshot)
        return runtime.as_messages(messages) if messages else ()

    def _record_turn(self, prepared: Prepared, snapshot: Any, *, answer: str) -> Steps[None]:
        """Log what was said this turn, as records this package owns, and the answer the
        caller is handed."""
        if self._ended_on(snapshot):
            # After the ledger: a write dying between the two leaves the log a turn short of
            # the ledger's count, which the next turn reports.
            yield from keep_turns(
                prepared.harness,
                prepared.log,
                turn=prepared.turn.id,
                parts=prepared.message,
                history=prepared.history,
                resumed=prepared.resuming,
                snapshot=snapshot,
                ledger=prepared.ledger.earlier if prepared.ledger is not None else None,
                answer=answer,
            )
            # Only with the message saying so: kept by a turn the agent never heard from,
            # what changed before it would never be said.
            if prepared.inputs_seen is not None:
                yield from write_inputs_seen(prepared.harness, prepared.inputs_seen)
            # Told now, in a conversation that keeps it; a turn that wrote none tells it again.
            if prepared.drift is not None:
                yield from prepared.harness.drop(REWOUND_FILE)

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
                **library_versions(),
                **(
                    {BUILT_FROM_MARK: dict(prepared.built_from)}
                    if prepared.built_from is not None
                    else {}
                ),
            },
        )
        return waiting

    def stream(
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
        files: Any = None,
    ) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        A `Resume` comes through here too, so the admission checks have one door.
        `files` is the session's backend from `Session.files()`, for a caller that uses it
        around the turn; one opened for another session is refused with
        `UnknownSessionError` before anything is claimed or written.
        """
        # Coerced here: setup reads the session id first, and a bare string has none.
        yield from self._stream_turn(
            _as_request(request), source_ids=with_datazone(source_ids, datazone, extra_datazones),
            files=files,
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
        except runtime.ModelUnavailableError as exc:
            turn.answer = normalize_answer(turn.answer)
            turn.stop_reason = "model_unavailable"
            turn.ok = True
            turn.pending.append(model_unavailable(exc.why))
        except Exception as exc:
            # Only a provider's 4xx, which the person at the terminal can fix, becomes a
            # one-line error; the rest keep their traceback, so a bug still looks like one.
            if (refused := refused_request(exc, self.cfg)) is None:
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
        # Known only once the saved state is read, and never for a turn that failed:
        # `end_turn` there would read as a turn that finished.
        stopped: str | None = None
        try:
            try:
                # Before anything is let go of: the saver still holds the paused state.
                snapshot = yield on_host(self._settled, prepared)
                # Before the pause as well as the turn log: a resume carries on from
                # that state, and a result in either must already have its row.
                turn.recorded = yield from self._record_ledger(prepared, snapshot)
                turn.awaiting = yield from self._settle_pause(prepared, snapshot)
                if turn.awaiting:
                    turn.pending.append(decision_needed(turn.awaiting))
                    # A bound that already fired keeps its reason: `stop_reason` says why
                    # the turn ended, and the pending calls are reported either way.
                    if turn.stop_reason == END_TURN:
                        turn.stop_reason = AWAITING
                # Off the state, not the stream, so it is the agent's last reply and never
                # a subagent's, whose cut answer is its caller's to read.
                if turn.stop_reason == END_TURN and (cut := runtime.ended_cut_off(snapshot)):
                    turn.stop_reason = "max_tokens"
                    turn.pending.append(out_of_tokens(cut))
                stopped = turn.stop_reason if turn.ok else None
            finally:
                # However the steps above end, so every `run_start` has its `run_end`.
                yield on_host(
                    prepared.logger.run_end,
                    ok=turn.ok,
                    answer_chars=len(turn.answer),
                    stop_reason=stopped,
                )
            # Normalised by `_turn_outcome` already; a turn that raised hands back none.
            yield from self._record_turn(
                prepared, snapshot, answer=turn.answer if turn.ok else ""
            )
            try:
                yield from self._keep_memory(turn, snapshot)
            finally:
                # After the writer and the store, either of which may make the memory file
                # this turn hands back; and however they end, so a failed write still
                # leaves the rest named.
                walked = yield from walk_artifacts(prepared.files, self.cfg.layout)
                turn.artifacts = collect_artifacts(walked)
            yield from self._sweep_results(prepared, turn.recorded)
            # Last, after the memory writer and the store: the memory file as the turn left it.
            # The same walk: the sweep hands over results in the scratchpad, which it does not
            # reach.
            yield from keep_turn_files(prepared, self.cfg, walked)
        except OSError as exc:
            # A full disk refusing the record is no reason to lose the answer as well: it
            # was paid for, and nothing streamed it before `finished`.
            turn.keep_failure = str(exc)
        finally:
            try:
                # However the turn ended, and even if the store was unreachable.
                yield changing(
                    self._sessions, "release", self.cfg, prepared.session.id, prepared.claim
                )
            finally:
                # Left open, the QuickJS runtime hangs the process rather than leaking a
                # handle.
                yield on_host(release_interpreter, self.cfg, prepared.graph)

    def _read(self, turn: _Turn, namespace: Any, mode: Any, chunk: Any) -> tuple[RunEvent, ...]:
        """One stream chunk, read as events. The answer accumulates on `turn`."""
        if (text := runtime.answer_in(namespace, mode, chunk)) is not None:
            turn.answer = text
        return tuple(
            runtime.events_in(
                namespace,
                mode,
                chunk,
                turn.subagent_names,
                seen=turn.seen,
                delegations=turn.delegations,
            )
        )

    def _payload(self, turn: _Turn) -> Any:
        """What the graph is driven with: a conversation, or the answers a resume re-enters
        the stopped node with, rather than asking the same question again."""
        if turn.prepared.resume is not None:
            return turn.prepared.resume
        return runtime.user_payload(joined(turn.prepared.message), turn.prepared.history)

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
        prepared = turn.prepared
        finished = RunEvent(
            kind="finished",
            text=turn.answer,
            result=RunResult(
                pending=turn.awaiting,
                discarded=prepared.discarded,
                session_id=prepared.session.id,
                turn_id=prepared.turn.id,
                answer=turn.answer,
                # Collected after the graph finished, so it includes what the shell
                # wrote, which no file tool would have reported.
                artifacts=turn.artifacts,
                memory=turn.memory,
                stop_reason=turn.stop_reason,
                ledger=turn.recorded,
                history_lost=prepared.history_lost,
                keep_failure=turn.keep_failure,
            ),
        )
        return (*turn.pending, finished)

    def _stream_turn(
        self,
        request: Request | Resume,
        *,
        source_ids: Held | None = None,
        files: Any = None,
    ) -> Iterator[RunEvent]:
        """One turn, on the graph's own stream."""
        heard = TurnWarnings(self.run_events)
        with warning_into(heard):
            turn = _Turn(drive(self._prepare_steps(request, source_ids=source_ids, files=files)))
        try:
            with self._turn_outcome(turn):
                # Inside, not before: a caller that stops reading during these must still
                # end the turn.
                yield from turn.prepared.events
                for chunk in _within(
                    heard, turn.prepared.graph.stream, self._payload(turn), **self._driving(turn)
                ):
                    yield from self._read(turn, *chunk)
                    if (stop := self._bound(turn)) is not None:
                        yield stop
                        break
        finally:
            with warning_into(heard):
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
        heard = TurnWarnings(self.run_events)
        with warning_into(heard):
            prepared, cancelled = await adrive_finished(
                self._prepare_steps(request, source_ids=source_ids, files=files)
            )
        turn = _Turn(prepared)
        if cancelled is not None:
            # The cancel arrived while the turn was being built, so the built turn is
            # the caller's to let go of: ended the way any turn ends, then the cancel.
            with warning_into(heard):
                await self._aend_turn(turn)
            raise cancelled
        try:
            with self._turn_outcome(turn):
                for event in turn.prepared.events:
                    yield event
                async for chunk in _awithin(
                    heard, turn.prepared.graph.astream, self._payload(turn), **self._driving(turn)
                ):
                    for event in self._read(turn, *chunk):
                        yield event
                    if (stop := self._bound(turn)) is not None:
                        yield stop
                        break
        finally:
            with warning_into(heard):
                await self._aend_turn(turn)
        for event in self._ending(turn):
            yield event

    def run(  # noqa: PLR0913 -- the caller's three, beside the turn's own
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
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
        for event in self.stream(
            request,
            source_ids=source_ids,
            datazone=datazone,
            extra_datazones=extra_datazones,
            files=files,
        ):
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
        extra_datazones: Sequence[str] = (),
        files: Any = None,
    ) -> AsyncIterator[RunEvent]:
        """`stream`, for a caller already on an event loop. Cancelling is immediate.

        Middleware runs through its `a`-prefixed hooks here, so one written only as
        `wrap_model_call` raises the first time this reaches it.
        """
        turn = self._astream_turn(
            _as_request(request), source_ids=with_datazone(source_ids, datazone, extra_datazones),
            files=files,
        )
        try:
            async for event in turn:
                yield event
        finally:
            # By hand: a dropped async generator waits for the loop to finalise it, and
            # until then a caller who stopped reading leaves the session claimed.
            await turn.aclose()

    async def arun(  # noqa: PLR0913 -- the caller's three, beside the turn's own
        self,
        request: str | Request | Resume,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
        delete_session: bool = False,
        files: Any = None,
    ) -> RunResult:
        """`run`, for a caller already on an event loop. A drain of `astream`.

        No `aclose` to match `astream`'s: a cancelled drain gives the claim back one turn
        of the loop later either way.
        """
        result: RunResult | None = None
        async for event in self.astream(
            request,
            source_ids=source_ids,
            datazone=datazone,
            extra_datazones=extra_datazones,
            files=files,
        ):
            if event.kind == "finished":
                result = event.result
        return await adrive(self._drained_steps(result, delete_session=delete_session))

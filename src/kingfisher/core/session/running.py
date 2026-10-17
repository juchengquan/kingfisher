"""Running a turn and settling it: `run`, `stream` and their async twins, the graph's
stream, and what a turn leaves once it ends."""

from __future__ import annotations

from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from kingfisher.core.access.source_ids import Held, with_datazone
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.layout import PAUSED_STATE
from kingfisher.core.request import Request, Resume
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
    harness_mark,
    paused_state,
)
from kingfisher.core.session.files import collect_artifacts
from kingfisher.core.session.ledger import keep_ledger
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
    clear_pause,
    pending_as_mark,
    write_pause_mark,
    write_transcript,
)
from kingfisher.core.session.threads import finished
from kingfisher.core.session.turn import (
    Prepared,
    consume,
    decision_needed,
    model_unavailable,
    out_of_steps,
    out_of_tokens,
    overrun,
)

if TYPE_CHECKING:
    from collections.abc import Callable



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


class Running:
    """A turn from its first model call to what it leaves, however it ends."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _sessions: Any
    _prepare_steps: Callable[..., Steps[Prepared]]
    _delete_session_steps: Callable[[str], Steps[str | None]]

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
        except runtime.ModelUnavailableError as exc:
            turn.answer = normalize_answer(turn.answer)
            turn.stop_reason = "model_unavailable"
            turn.ok = True
            turn.pending.append(model_unavailable(exc.why))
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

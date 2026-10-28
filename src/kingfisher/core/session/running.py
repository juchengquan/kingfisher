"""Running a turn and settling it: `run`, `stream` and their async twins, the graph's
stream, and what a turn leaves once it ends."""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import Held, with_datazone
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.layout import PAUSED_STATE
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
    harness_mark,
    paused_state,
)
from .files import collect_artifacts
from .ledger import keep_ledger, sweep_results
from .memory import kept_in_store
from .memory_writer import kept_memory
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
    PENDING_MARK,
    clear_pause,
    pending_as_mark,
    write_pause_mark,
    write_transcript,
)
from .threads import finished
from .turn import (
    Prepared,
    consume,
    decision_needed,
    model_unavailable,
    out_of_steps,
    out_of_tokens,
    overrun,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from kingfisher.core.config import Endpoint
    from kingfisher.core.interfaces import MemoryStore, UnusedResults



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


def _asked(value: str | Request | Resume) -> Request | Resume:
    """Whatever a caller handed in, as one of the two things a turn starts from."""
    return value if isinstance(value, Request | Resume) else Request.coerce(value)


#: Matched on the exception's `status_code`, not its class: `anthropic` and `openai`
#: each raise their own `AuthenticationError`, and the command may import neither.
UNAUTHORIZED = 401
CLIENT_ERROR = 400


def refused_credentials(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 401 as the configuration error it is, or `None` for anything else.

    The endpoint is matched by the failed request's URL, not taken as the default: a
    subagent may run elsewhere. The shell is named because `load_dotenv` runs with
    `override=False`, so an exported key beats the one in `.env`.
    """
    if getattr(exc, "status_code", None) != UNAUTHORIZED:
        return None
    named = _endpoint_of(exc, cfg)
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


def refused_request(exc: BaseException, cfg: Config) -> ConfigError | None:
    """A provider's 4xx, or the same refusal sent inside a stream, as the configuration
    error it is, or `None` for anything else.

    Not a 429, which may pass and ends a turn as a stop before this is reached. The rest
    of the range -- a model name the endpoint does not serve, a request it will not
    take -- fails the same way on every retry, so the fix is in the deployment.
    """
    if (refused := refused_credentials(exc, cfg)) is not None:
        return refused
    status = getattr(exc, "status_code", None)
    if not isinstance(status, int) or status == runtime.TOO_MANY_REQUESTS:
        return None
    # Named by its type: a stream's error carries the 200 the stream began with.
    if status < CLIENT_ERROR and runtime.error_type(exc) == runtime.REFUSED_TYPE:
        code: int | str = runtime.REFUSED_TYPE
    elif CLIENT_ERROR <= status < runtime.SERVER_ERROR:
        code = status
    else:
        return None
    named = _endpoint_of(exc, cfg)
    where = f"endpoint {named[0][0]!r} ({named[0][1].base_url})" if named else "the endpoint"
    model = _model_of(exc)
    which = f"model {model!r}" if model else "the model"
    return ConfigError(f"{where} refused a call to {which} ({code}): {runtime.said(exc)}")


def _endpoint_of(exc: BaseException, cfg: Config) -> list[tuple[str, Endpoint]]:
    """The configured endpoints the failed request was sent to, by its URL."""
    url = str(getattr(getattr(getattr(exc, "response", None), "request", None), "url", ""))
    return [
        (name, endpoint)
        for name, endpoint in cfg.models.endpoints.items()
        if url.startswith(endpoint.base_url)
    ]


def _model_of(exc: BaseException) -> str | None:
    """The model the failed request named, read from the body it sent."""
    request = getattr(getattr(exc, "response", None), "request", None)
    try:
        body = json.loads(getattr(request, "content", b"") or b"{}")
    # httpx raises `RequestNotRead`, a `RuntimeError`, for a body it streamed.
    except (ValueError, TypeError, RuntimeError):
        return None
    model = body.get("model") if isinstance(body, dict) else None
    return model if isinstance(model, str) else None


class Running:
    """A turn from its first model call to what it leaves, however it ends."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _sessions: Any
    _prepare_steps: Callable[..., Steps[Prepared]]
    _delete_session_steps: Callable[[str], Steps[str | None]]
    unused_results: UnusedResults
    memory_store: MemoryStore | None

    def _keep(self, prepared: Prepared, snapshot: Any) -> Steps[tuple[str, ...]]:
        """Persist what this turn produced, and name it."""
        yield from self._record_transcript(prepared, snapshot)
        return (yield from collect_artifacts(prepared.files, self.cfg.layout))

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
                prepared.harness,
                prepared.ledger,
                summarised=runtime.summarised_in(snapshot),
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

    def _record_transcript(self, prepared: Prepared, snapshot: Any) -> Steps[None]:
        """Write what was said this turn, as records this package owns."""
        if not self.cfg.conversation_enabled:
            return
        if snapshot is None:
            # No state, or a turn that died before its first superstep: this runs after
            # every turn, failed ones included.
            return
        messages = runtime.as_seen(snapshot)
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
            _asked(request), source_ids=with_datazone(source_ids, datazone, extra_datazones),
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
                # Before the pause as well as the transcript: a resume carries on from
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
            turn.kept = yield from self._keep(prepared, snapshot)
            if prepared.memory is not None:
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
            yield from self._sweep_results(prepared, turn.recorded)
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
        turn.answer, events = consume(
            namespace,
            mode,
            chunk,
            turn.answer,
            turn.subagent_names,
            seen=turn.seen,
            delegations=turn.delegations,
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
                artifacts=turn.kept,
                memory=turn.memory,
                stop_reason=turn.stop_reason,
                ledger=turn.recorded,
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
            _asked(request), source_ids=with_datazone(source_ids, datazone, extra_datazones),
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

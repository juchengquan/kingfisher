"""Older tool results and file text in a graph's conversation, swapped for handles once it
grows large."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from deepagents.middleware.summarization import _is_context_overflow
from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from kingfisher.core.agent.prompting import EDIT_FILE, FILE_TOOLS, WRITE_FILE
from kingfisher.core.config import ConfigError
from kingfisher.core.definitions.skills import READ_FILE
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import with_call_args

from .model_reply import NOT_RUN
from .recorded import (
    STAMP_PATTERN,
    arguments,
    stamp,
    stamped_id,
)

if TYPE_CHECKING:
    from kingfisher.core.agent.models import SummarizerCount
    from kingfisher.core.result import LedgerEntry
    from kingfisher.core.session.ledger import Ledger

#: The high mark on a large window, in tokens: the conversation size a pass starts at.
#: Each pass leaves it at half this, the low mark.
DEFAULT_MARK = 64_000

#: The share of the summariser's trigger the default high mark may reach, so that on a
#: small window clearing still starts well before summarising does.
TRIGGER_SHARE = 0.5

#: The most recent clearable results kept whole, besides the latest batch.
KEEP_RECENT = 3

#: A pass runs only if it could free this fraction of the high mark, or once what cannot be
#: cleared nears the low mark every call is a pass that frees little and costs the cache.
WORTH = 1 / 4

#: The arguments that carry a file's text, by the file tool they belong to.
FILE_TEXT = {WRITE_FILE: ("content",), EDIT_FILE: ("old_string", "new_string")}

#: What a handle adds for a result the model was never shown, being in the batch a refused
#: call was to hand it: told only to call the same tool again, it would make the same
#: call and be refused again.
UNSHOWN = " before it was shown, the conversation being too long for it"

#: The handles this module writes, as each ends. Asked before clearing anything: a later pass
#: would otherwise read a handle as what it replaced and clear it again, wrongly.
_RESULT_HANDLE = re.compile(
    rf"(?:\n\([\d,]+ bytes, cleared(?:;|{re.escape(UNSHOWN)}:) [^\n]*\){STAMP_PATTERN}"
    r"| -- (?:[\d,]+ chars, )?cleared(?:; call \S+ again to see it as it is now"
    rf"|{re.escape(UNSHOWN)}; call \S+ again [^\n]*))\Z"
)
_TEXT_HANDLE = re.compile(
    r"\A[\d,]+ chars(?: written)?, cleared(?:; .* holds the current text)?\Z", re.S
)

#: The ways a result says it failed once the transcript has dropped its status, which a
#: replayed result has: a file tool's error, a call that never finished or never ran
#: (deepagents' `PatchToolCallsMiddleware`), a command that exited non-zero, and a call
#: refused because the reply was cut off writing it.
_FAILED = re.compile(
    r"\AError"
    r"|\ATool call \S+ with id \S+ (did not complete|could not be executed)"
    r"|^\[Command failed with exit code -?\d+\]$"
    rf"|\A{re.escape(NOT_RUN)}",
    re.MULTILINE,
)


def high_mark(trigger: int, written: int | None, *, subject: str) -> int:
    """Where clearing starts on `subject`, a graph whose summariser fires at `trigger`.

    `written` is the entry's `clear_results_at`, used as written. A model passed in has no
    entry and takes the default.
    """
    if written is None:
        return min(DEFAULT_MARK, int(TRIGGER_SHARE * trigger))
    # One past deepagents' own was refused beside the graph's middleware, which only then
    # says whose summariser runs; this is a deployment's, firing sooner or later.
    if written >= trigger:
        msg = (
            f"{subject} runs a summariser of the deployment's that fires at {trigger} "
            f"tokens, and its model sets clear_results_at: {written}, so the summariser "
            f"would come first and nothing would be cleared. Lower clear_results_at below "
            f"{trigger} in models.yaml, or leave it out for the default"
        )
        raise ConfigError(msg)
    return written


def result_clearing(
    ledger: Ledger | None,
    trigger: int,
    written: int | None,
    *,
    count: SummarizerCount,
    subject: str,
) -> Callable[[tuple[str, ...] | None], ResultClearingMiddleware] | None:
    """What builds `subject`'s clearing once its grant is known, or `None` where no ledger
    is kept for a handle to name.

    Each graph's own `trigger`, `written` and `count`: a subagent on a smaller model, or
    naming a summariser of its own, would otherwise clear by a mark it never reaches.
    """
    if ledger is None:
        return None
    return partial(
        ResultClearingMiddleware,
        ledger,
        high_mark(trigger, written, subject=subject),
        count=count,
    )


@dataclass(frozen=True)
class _Item:
    """One thing a pass could clear, oldest first by `at`, then by `order`."""

    #: The message's position in the conversation.
    at: int
    order: int
    #: The message with this item cleared.
    clear: Callable[[Any], Any]
    #: What clearing it alone frees, in the summariser's tokens.
    frees: float


class ResultClearingMiddleware(AgentMiddleware):
    """Swap older results and file text for handles in the conversation itself, once it
    has grown past the high mark, from inside each model call.

    Inside the call, not a `before_model` hook: a hook is a graph step the recursion limit
    counts. The cost is the summariser's count on the call a pass starts on, taken first;
    with the mark at most half its trigger, only a batch adding more than that between two
    calls gets there first.

    A message keeps its id and place, and only its content changes: the summariser's cut is a
    position in this list. Nothing here writes a file, so reading a handle back cannot start
    another pass.
    """

    def __init__(
        self,
        ledger: Ledger,
        high: int,
        tools: tuple[str, ...] | None = None,
        *,
        count: SummarizerCount,
    ) -> None:
        super().__init__()
        #: The turn's, whose rows say where each result with an id is kept.
        self._ledger = ledger
        self.high = high
        self._low = high // 2
        # The summariser's, not langchain's bare default: the marks are read off its
        # trigger, and its default counts 3.3 characters a token on an Anthropic model.
        self.count = count
        # The graph's grant, not `request.tools`: this runs before `GrantedOnlyMiddleware`
        # narrows them.
        self._granted = None if tools is None else frozenset(tools)

    def _holds(self, name: str) -> bool:
        return self._granted is None or name in self._granted

    # A refusal for length is answered here before deepagents' summariser sees it, which
    # would summarise and write the batch to a folder of its own. Its first error is what it
    # gets when this cannot help, as if this had not been here.
    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        request, update = self._cleared(request)
        try:
            return _with(handler(request), update)
        except Exception as exc:
            if not _is_context_overflow(exc):
                raise
            refused = exc
        forced = self._forced(request, update)
        if forced is None:
            raise refused
        try:
            return _with(handler(forced[0]), forced[1])
        except Exception as exc:
            if not _is_context_overflow(exc):
                raise
        raise refused

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        request, update = self._cleared(request)
        try:
            return _with(await handler(request), update)
        except Exception as exc:
            if not _is_context_overflow(exc):
                raise
            refused = exc
        forced = self._forced(request, update)
        if forced is None:
            raise refused
        try:
            return _with(await handler(forced[0]), forced[1])
        except Exception as exc:
            if not _is_context_overflow(exc):
                raise
        raise refused

    def _cleared(self, request: Any) -> tuple[Any, Command[Any] | None]:
        """The request with this pass's handles in it, and the state update carrying them,
        or the request as it came and `None` where no pass runs."""
        sent = list(request.messages)
        fixed = [] if request.system_message is None else [request.system_message]
        total = self.count.request([*fixed, *sent], request.tools)
        if total < self.high:
            return request, None
        # Scaled as the total is, or a pass would clear past the low mark by the factor
        # the provider's reported usage put on the total and not on each message.
        scale = total / max(1, self.count.unscaled([*fixed, *sent], request.tools))

        def tokens(message: Any) -> float:
            return self.count.each([message]) * scale

        messages = list(request.state.get("messages") or ())
        # The summariser's view omits what its summary stands for and may shorten arguments,
        # so a handle is made from the message in state and counted against the one sent.
        as_sent = {one.id: one for one in sent if one.id is not None}
        items = self._items(messages, as_sent, tokens)
        if sum(item.frees for item in items) < self.high * WORTH:
            return request, None
        changed: dict[int, Any] = {}
        for item in items:
            if total <= self._low:
                break
            counted = changed[item.at] if item.at in changed else as_sent[messages[item.at].id]
            after = item.clear(changed.get(item.at, messages[item.at]))
            total -= tokens(counted) - tokens(after)
            changed[item.at] = after
        if not changed:
            return request, None
        replaced = {messages[at].id: one for at, one in changed.items()}
        cleared = request.override(messages=[replaced.get(one.id, one) for one in sent])
        return cleared, Command(update={"messages": list(replaced.values())})

    def _forced(
        self, request: Any, update: Command[Any] | None
    ) -> tuple[Any, Command[Any]] | None:
        """`request`, refused as too long, with everything clearable cleared and nothing
        spared, and the state update carrying it with `update`'s, or `None` where nothing
        more can be cleared.

        `request` already sends `update`'s handles, so a message cleared again is built
        from that one: built from state, a write the first pass cleared would come back
        whole beside an edit this one clears.
        """
        sent = list(request.messages)
        handed = None if update is None else update.update
        earlier = {one.id: one for one in handed["messages"]} if handed else {}
        messages = [earlier.get(one.id, one) for one in request.state.get("messages") or ()]
        as_sent = {one.id: one for one in sent if one.id is not None}
        changed: dict[int, Any] = {}
        # Unscaled: it clears everything, so a count serves only to skip what frees nothing.
        for item in self._items(messages, as_sent, self._each, spare=False):
            changed[item.at] = item.clear(changed.get(item.at, messages[item.at]))
        if not changed:
            return None
        replaced = {**earlier, **{messages[at].id: one for at, one in changed.items()}}
        cleared = request.override(messages=[replaced.get(one.id, one) for one in sent])
        return cleared, Command(update={"messages": list(replaced.values())})

    def _each(self, message: Any) -> float:
        return self.count.each([message])

    def _items(
        self,
        messages: list[Any],
        as_sent: Mapping[str, Any],
        tokens: Callable[[Any], float],
        *,
        spare: bool = True,
    ) -> list[_Item]:
        """Everything a pass may clear, oldest first, each freeing something.

        Spared unless `spare` is false: the latest batch of calls with its results, and the
        `KEEP_RECENT` most recent clearable results with the text of the calls that made
        them. Never: what this call no longer sends.
        """
        latest = max(
            (at for at, one in enumerate(messages) if isinstance(one, AIMessage)),
            default=len(messages),
        )
        calls = {
            call["id"]: call
            for one in messages
            if isinstance(one, AIMessage)
            for call in one.tool_calls
        }
        earlier = (entry for exchange in self._ledger.earlier.exchanges for entry in exchange)
        rows = {entry.id: entry for entry in (*earlier, *self._ledger.entries)}
        succeeded: set[str] = set()
        results: list[tuple[int, str, str]] = []
        for at, one in enumerate(messages):
            if not isinstance(one, ToolMessage) or one.name == TASK_TOOL or _failed(one):
                continue
            succeeded.add(one.tool_call_id)
            handle = self._handle(one, calls.get(one.tool_call_id), rows, shown=at < latest)
            if (at < latest or not spare) and handle is not None:
                results.append((at, one.tool_call_id, handle))
        kept = {call for _, call, _ in results[-KEEP_RECENT:]} if spare else set()

        # A message with no id would be appended by the reducer rather than replaced, and
        # is in `as_sent` under none.
        def sending(at: int) -> Any:
            return as_sent.get(messages[at].id) if messages[at].id is not None else None

        def item(at: int, order: int, clear: Callable[[Any], Any]) -> _Item:
            """One item, freeing what its handle saves against the message as this call
            sends it."""
            frees = tokens(sending(at)) - tokens(clear(messages[at]))
            return _Item(at=at, order=order, clear=clear, frees=frees)

        items = [
            item(at, 0, _replaced(handle))
            for at, call, handle in results
            if sending(at) is not None and call not in kept
        ]
        for at in range(latest if spare else len(messages)):
            one = messages[at]
            if not isinstance(one, AIMessage) or sending(at) is None:
                continue
            for order, call in enumerate(one.tool_calls):
                if call["id"] in succeeded and call["id"] not in kept:
                    cleared = _file_text_cleared(call)
                    if cleared:
                        items.append(item(at, order, _rewritten(call["id"], cleared)))
        return sorted(
            (item for item in items if item.frees > 0), key=lambda item: (item.at, item.order)
        )

    def _handle(
        self,
        result: ToolMessage,
        call: Mapping[str, Any] | None,
        rows: Mapping[str, LedgerEntry],
        *,
        shown: bool = True,
    ) -> str | None:
        """What one result would read as once cleared, or `None` where it has no
        handle: no saved copy, and not a file tool's. One already cleared reads as itself.
        """
        text = result.text
        if _RESULT_HANDLE.search(text):
            return text
        if (number := stamped_id(text)) is not None and (
            row := rows.get(number)
        ) is not None and row.call == result.tool_call_id:
            return self._row_handle(row, shown=shown)
        # What a file tool returned is a file's text or a listing of files, and calling it
        # again gets that back.
        if result.name in FILE_TOOLS:
            args = (call or {}).get("args") or {}
            called = f"{result.name} {arguments(args)}" if args else str(result.name)
            size = _size(result)
            what = f"{size:,} chars, cleared" if size is not None else "cleared"
            if not shown:
                how = "with offset and limit" if result.name == READ_FILE else "narrowed"
                return f"{called} -- {what}{UNSHOWN}; call {result.name} again {how}"
            return f"{called} -- {what}; call {result.name} again to see it as it is now"
        return None

    def _row_handle(self, row: LedgerEntry, *, shown: bool = True) -> str | None:
        """A recorded result's handle, naming where its whole text is kept to an agent
        holding the tool that reaches it there, and the call that made it to any other.
        """
        again = f"call {row.tool} again to see it" if shown else f"call {row.tool} again for less"
        if row.expired_in is not None:
            where = f"expired, {again}"
        elif row.path is not None:
            where = f"full result: {row.path}" if self._holds(READ_FILE) else again
            if not shown and self._holds(READ_FILE):
                where += ", read it a part at a time"
        else:
            return None
        cleared = "cleared;" if shown else f"cleared{UNSHOWN}:"
        return f"{row.summary}\n({row.size:,} bytes, {cleared} {where}){stamp(row.id)}"


def _with(response: Any, update: Command[Any] | None) -> Any:
    """The model's response, carrying the pass's state update where there was a pass.

    Inner layers' updates need no merging: langchain applies each with the model's reply.
    """
    if update is None:
        return response
    return ExtendedModelResponse(model_response=response, command=update)


def _failed(result: ToolMessage) -> bool:
    return result.status == "error" or _FAILED.search(result.text) is not None


def _size(result: ToolMessage) -> int | None:
    """A result's length in characters, or `None` for one carrying more than text."""
    content = result.content
    if isinstance(content, str):
        return len(content)
    if all(isinstance(b, str) or b.get("type") == "text" for b in content):
        return len(result.text)
    return None


def _replaced(handle: str) -> Callable[[Any], Any]:
    return lambda result: result.model_copy(update={"content": handle})


def _file_text_cleared(call: Mapping[str, Any]) -> dict[str, str]:
    """The file text in one write or edit call, each argument as its handle, where the
    handle is the shorter."""
    args = call.get("args") or {}
    path = args.get("file_path") or "the file"
    cleared: dict[str, str] = {}
    for name in FILE_TEXT.get(call.get("name") or "", ()):
        value = args.get(name)
        if not isinstance(value, str) or _TEXT_HANDLE.match(value):
            continue
        if name == "content":
            handle = f"{len(value):,} chars written, cleared; {path} holds the current text"
        elif name == "old_string":
            handle = f"{len(value):,} chars, cleared"
        else:
            handle = f"{len(value):,} chars, cleared; {path} holds the current text"
        if len(handle) < len(value):
            cleared[name] = handle
    return cleared


def _rewritten(call_id: str, cleared: Mapping[str, str]) -> Callable[[Any], Any]:
    """What replaces one call's file text with `cleared` in the message holding it."""
    return lambda message: with_call_args(message, call_id, cleared)

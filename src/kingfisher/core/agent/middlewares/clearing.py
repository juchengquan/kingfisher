"""Older tool results and file text in the main agent's conversation, swapped for handles
once it grows large."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, ExtendedModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately
from langgraph.types import Command

from kingfisher.core.agent.middlewares.cut_replies import NOT_RUN
from kingfisher.core.agent.middlewares.recorder import _arguments
from kingfisher.core.agent.models import summarizer_trigger
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import with_call_args

if TYPE_CHECKING:
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

#: deepagents' file tools. What one returned is a file's text or a listing of files,
#: and calling it again gets that back.
FILE_TOOLS = frozenset({"ls", "glob", "grep", "read_file", "write_file", "edit_file", "delete"})

#: The arguments that carry a file's text, by the file tool they belong to.
FILE_TEXT = {"write_file": ("content",), "edit_file": ("old_string", "new_string")}

#: What the recorder ends a result with. A stub deepagents left numbers its lines, so the
#: id is matched at the end of the text, not as a line of its own.
_STAMP = re.compile(r"\[(A-\d+)\]\s*\Z")

#: How deepagents' eviction stub begins, `_TOO_LARGE_TOOL_MSG` up to its first field.
EVICTED = "Tool result too large, the result of this tool call "

#: The handles this module writes, as each ends. Asked before clearing anything: a later pass
#: would otherwise read a handle as what it replaced and clear it again, wrongly.
_RESULT_HANDLE = re.compile(
    r"(?:\n\([\d,]+ bytes, cleared; [^\n]*\)\n\[A-\d+\]"
    r"| -- (?:[\d,]+ chars, )?cleared; call \S+ again to see it as it is now)\Z"
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


def high_mark(model: Any, written: int | None) -> int:
    """Where clearing starts on a graph running `model`.

    `written` is the entry's `clear_results_at`, used as written: `build_model` has refused
    one the summariser would reach first. A model passed in has no entry and takes the default.
    """
    if written is not None:
        return written
    return min(DEFAULT_MARK, int(TRIGGER_SHARE * summarizer_trigger(model)))


@dataclass(frozen=True)
class _Item:
    """One thing a pass could clear, oldest first by `at`, then by `order`."""

    #: The message's position in the conversation.
    at: int
    order: int
    #: The message with this item cleared.
    clear: Callable[[Any], Any]
    #: What clearing it alone frees, in the summariser's tokens.
    frees: int


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

    def __init__(self, ledger: Ledger, high: int, offloads_route: str) -> None:
        super().__init__()
        #: The turn's, whose rows say where each result with an id is kept.
        self._ledger = ledger
        self.high = high
        self._low = high // 2
        #: This layout's `/scratchpad/large_tool_results/`, as the model addresses it.
        self._offloads = offloads_route

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        request, update = self._cleared(request)
        return _with(handler(request), update)

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        request, update = self._cleared(request)
        return _with(await handler(request), update)

    def _cleared(self, request: Any) -> tuple[Any, Command[Any] | None]:
        """The request with this pass's handles in it, and the state update carrying them,
        or the request as it came and `None` where no pass runs."""
        sent = list(request.messages)
        fixed = [] if request.system_message is None else [request.system_message]
        total = count_tokens_approximately([*fixed, *sent], tools=request.tools)
        if total < self.high:
            return request, None
        messages = list(request.state.get("messages") or ())
        # The summariser's view omits what its summary stands for and may shorten arguments,
        # so a handle is made from the message in state and counted against the one sent.
        as_sent = {one.id: one for one in sent if one.id is not None}
        items = self._items(messages, as_sent)
        if sum(item.frees for item in items) < self.high * WORTH:
            return request, None
        changed: dict[int, Any] = {}
        for item in items:
            if total <= self._low:
                break
            counted = changed[item.at] if item.at in changed else as_sent[messages[item.at].id]
            after = item.clear(changed.get(item.at, messages[item.at]))
            total -= _tokens(counted) - _tokens(after)
            changed[item.at] = after
        if not changed:
            return request, None
        replaced = {messages[at].id: one for at, one in changed.items()}
        cleared = request.override(messages=[replaced.get(one.id, one) for one in sent])
        return cleared, Command(update={"messages": list(replaced.values())})

    def _items(self, messages: list[Any], as_sent: Mapping[str, Any]) -> list[_Item]:
        """Everything a pass may clear, oldest first, each freeing something.

        Spared: the latest batch of calls with its results, the `KEEP_RECENT` most
        recent clearable results with the text of the calls that made them, and what
        this call no longer sends.
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
            handle = self._handle(one, calls.get(one.tool_call_id), rows)
            if at < latest and handle is not None:
                results.append((at, one.tool_call_id, handle))
        kept = {call for _, call, _ in results[-KEEP_RECENT:]}

        # A message with no id would be appended by the reducer rather than replaced, and
        # is in `as_sent` under none.
        def sending(at: int) -> Any:
            return as_sent.get(messages[at].id) if messages[at].id is not None else None

        items = [
            _item(messages[at], sending(at), at, 0, _replaced(handle))
            for at, call, handle in results
            if sending(at) is not None and call not in kept
        ]
        for at in range(latest):
            one = messages[at]
            if not isinstance(one, AIMessage) or sending(at) is None:
                continue
            for order, call in enumerate(one.tool_calls):
                if call["id"] in succeeded and call["id"] not in kept:
                    cleared = _file_text_cleared(call)
                    if cleared:
                        items.append(
                            _item(one, sending(at), at, order, _rewritten(call["id"], cleared))
                        )
        return sorted(
            (item for item in items if item.frees > 0), key=lambda item: (item.at, item.order)
        )

    def _handle(
        self, result: ToolMessage, call: Mapping[str, Any] | None, rows: Mapping[str, LedgerEntry]
    ) -> str | None:
        """What one result would read as once cleared, or `None` where it has no
        handle: no saved copy, and not a file tool's. One already cleared reads as itself.
        """
        text = result.text
        if _RESULT_HANDLE.search(text):
            return text
        if (stamp := _STAMP.search(text)) is not None and (
            row := rows.get(stamp.group(1))
        ) is not None and row.call == result.tool_call_id:
            return self._row_handle(row, text)
        if result.name in FILE_TOOLS:
            args = (call or {}).get("args") or {}
            called = f"{result.name} {_arguments(args)}" if args else str(result.name)
            size = _size(result)
            what = f"{size:,} chars, cleared" if size is not None else "cleared"
            return f"{called} -- {what}; call {result.name} again to see it as it is now"
        return None

    def _row_handle(self, row: LedgerEntry, text: str) -> str | None:
        """A recorded result's handle, naming where its whole text is kept.

        An offloaded one is named by a search for its id, not by its file: deepagents names
        that file after the call id, which a later call with the same id overwrites.
        """
        if (row.path is not None and row.path.startswith(self._offloads)) or (
            text.startswith(EVICTED) and row.size > len(text.encode("utf-8"))
        ):
            where = f"offloaded: grep for [{row.id}] under {self._offloads}"
        elif row.path is not None:
            where = f"full result: {row.path}"
        else:
            return None
        return f"{row.summary}\n({row.size:,} bytes, cleared; {where})\n[{row.id}]"


def _with(response: Any, update: Command[Any] | None) -> Any:
    """The model's response, carrying the pass's state update where there was a pass.

    Inner layers' updates need no merging: langchain applies each with the model's reply.
    """
    if update is None:
        return response
    return ExtendedModelResponse(model_response=response, command=update)


def _tokens(message: Any) -> int:
    """One message as the summariser counts it, which is per message and rounded up."""
    return count_tokens_approximately([message])


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


def _item(message: Any, sent: Any, at: int, order: int, clear: Callable[[Any], Any]) -> _Item:
    """One item, freeing what its handle saves against the message as this call sends it."""
    return _Item(at=at, order=order, clear=clear, frees=_tokens(sent) - _tokens(clear(message)))


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

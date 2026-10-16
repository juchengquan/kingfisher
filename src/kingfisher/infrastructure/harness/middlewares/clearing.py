"""Older tool results and file text in the main agent's conversation, swapped for handles
once it grows large."""

from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from deepagents.middleware.summarization import SUMMARIZATION_EVENT_KEY
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.messages.utils import count_tokens_approximately

from kingfisher.infrastructure.harness.middlewares.recorder import _arguments
from kingfisher.infrastructure.harness.models import summarizer_trigger
from kingfisher.kinds.subagents import TASK_TOOL

if TYPE_CHECKING:
    from kingfisher.domain.result import LedgerEntry
    from kingfisher.infrastructure.harness.ledger import Ledger

#: The high mark on a large window, in tokens: the conversation size a pass starts at.
#: Each pass leaves it at half this, the low mark.
DEFAULT_MARK = 64_000

#: The share of the summariser's trigger the default high mark may reach, so that on a
#: small window clearing still starts well before summarising does.
TRIGGER_SHARE = 0.5

#: The most recent clearable results kept whole, besides the latest batch.
KEEP_RECENT = 3

#: A pass runs only if it could free this fraction of the high mark. Without it, once
#: what can never be cleared nears the low mark, every call is a pass that frees almost
#: nothing and costs the cache everything after the earliest message it touched.
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

#: The handles this module writes, as each ends. Asked before clearing anything, because a
#: handle is read again on every later pass as if it were what it replaced: a file tool's
#: handle cleared again names its own length, and an offloaded result's, no longer a
#: stub, was pointed at the copy the turn's sweep removes.
_RESULT_HANDLE = re.compile(
    r"(?:\n\([\d,]+ bytes, cleared; [^\n]*\)\n\[A-\d+\]"
    r"| -- (?:[\d,]+ chars, )?cleared; call \S+ again to see it as it is now)\Z"
)
_TEXT_HANDLE = re.compile(
    r"\A[\d,]+ chars(?: written)?, cleared(?:; .* holds the current text)?\Z", re.S
)

#: The ways a result says it failed once the transcript has dropped its status, which a
#: replayed result has: a file tool's error, a call that never finished or never ran
#: (deepagents' `PatchToolCallsMiddleware`), and a command that exited non-zero.
_FAILED = re.compile(
    r"\AError"
    r"|\ATool call \S+ with id \S+ (did not complete|could not be executed)"
    r"|^\[Command failed with exit code -?\d+\]$",
    re.MULTILINE,
)


def high_mark(model: Any, written: int | None) -> int:
    """Where clearing starts on a graph running `model`.

    `written` is the `clear_results_at` of the entry that built the model, used as
    written: `build_model` has refused one the summariser would reach first. A model the
    deployment passed in has no entry, and takes the default.
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


class ResultClearing(AgentMiddleware):
    """Before each model call, swap older results and file text for handles in the
    conversation itself, once it has grown past the high mark.

    `before_model`, because the conversation it rewrites is the one deepagents'
    summariser counts on the same call and the one the turn writes to the transcript.
    The summariser's count happens inside `wrap_model_call`, so anything done there
    lands after it has decided, and changes only what one request sends.

    A message keeps its id and its place, and only its content changes. The summariser's
    cut is a position in this list, and a message removed would move it.

    Nothing here writes a file. A handle names a saved copy that already exists, or the
    file a tool read or wrote, so reading one back cannot start another pass.
    """

    def __init__(self, ledger: Ledger, high: int, offloads_route: str) -> None:
        super().__init__()
        #: The turn's, whose rows say where each result with an id is kept.
        self._ledger = ledger
        self.high = high
        self._low = high // 2
        #: This layout's `/scratchpad/large_tool_results/`, as the model addresses it.
        self._offloads = offloads_route
        # What the system prompt and the tools add to the summariser's count, read off
        # the last request. Zero until a model call has passed, so the first call of a
        # turn counts the conversation alone and may start a pass a little later.
        self._fixed = 0

    def before_model(self, state: Any, runtime: Any) -> dict[str, Any] | None:  # noqa: ARG002
        return self._cleared(state)

    async def abefore_model(
        self, state: Any, runtime: Any  # noqa: ARG002
    ) -> dict[str, Any] | None:
        return self._cleared(state)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        self._measured(request)
        return handler(request)

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        self._measured(request)
        return await handler(request)

    def _measured(self, request: Any) -> None:
        fixed = [] if request.system_message is None else [request.system_message]
        self._fixed = count_tokens_approximately(fixed, tools=request.tools)

    def _cleared(self, state: Any) -> dict[str, Any] | None:
        messages = list(state.get("messages") or ())
        start, summary = _summarised(state, len(messages))
        total = self._fixed + count_tokens_approximately([*summary, *messages[start:]])
        if total < self.high:
            return None
        items = self._items(messages, start)
        if sum(item.frees for item in items) < self.high * WORTH:
            return None
        changed: dict[int, Any] = {}
        for item in items:
            if total <= self._low:
                break
            before = changed.get(item.at, messages[item.at])
            after = item.clear(before)
            total -= _tokens(before) - _tokens(after)
            changed[item.at] = after
        return {"messages": list(changed.values())} if changed else None

    def _items(self, messages: list[Any], start: int) -> list[_Item]:
        """Everything a pass may clear, oldest first, each freeing something.

        Spared: the latest batch of calls with its results, the `KEEP_RECENT` most
        recent clearable results with the text of the calls that made them, and what
        the summariser no longer sends.
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

        items = [
            _item(messages[at], at, 0, _replaced(handle))
            for at, call, handle in results
            if at >= start and call not in kept
        ]
        for at in range(start, latest):
            one = messages[at]
            if not isinstance(one, AIMessage):
                continue
            for order, call in enumerate(one.tool_calls):
                if call["id"] in succeeded and call["id"] not in kept:
                    cleared = _file_text_cleared(call)
                    if cleared:
                        items.append(_item(one, at, order, _rewritten(call["id"], cleared)))
        # A message with no id would be appended by the reducer rather than replaced.
        return sorted(
            (item for item in items if item.frees > 0 and messages[item.at].id is not None),
            key=lambda item: (item.at, item.order),
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

        An offloaded one is named by a search for its id rather than by its file:
        deepagents names that file after the call id, a later call with the same id
        overwrites it, and a search then finds nothing rather than that call's result.
        What is offloaded is a row already pointed there, or this turn's result whose
        stub is shorter than what was recorded. A result recorded as a stub -- a tool
        that offloaded its own output -- is kept whole as that stub in our copy.
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


def _summarised(state: Any, count: int) -> tuple[int, list[Any]]:
    """Where the summariser's view of the conversation starts, and the summary ahead of
    it, as `_apply_event_to_messages` builds that view."""
    event = state.get(SUMMARIZATION_EVENT_KEY)
    if not isinstance(event, Mapping):
        return 0, []
    cutoff, summary = event.get("cutoff_index"), event.get("summary_message")
    if not isinstance(cutoff, int) or summary is None:
        return 0, []
    return min(cutoff, count), [summary]


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


def _item(message: Any, at: int, order: int, clear: Callable[[Any], Any]) -> _Item:
    return _Item(at=at, order=order, clear=clear, frees=_tokens(message) - _tokens(clear(message)))


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
    """What replaces one call's file text with `cleared` in the message holding it.

    The call is written twice, and both copies change. `tool_calls` is what
    langchain-anthropic sends, and the `tool_use` block in a reply's content is what
    langchain counts in its place; langchain-openai sends a `function_call` block
    instead of the call. Changing only one sent the text, or counted it, all the same.
    """

    def clear(message: AIMessage) -> AIMessage:
        calls = [
            {**call, "args": {**call["args"], **cleared}} if call["id"] == call_id else call
            for call in message.tool_calls
        ]
        args = next(call["args"] for call in calls if call["id"] == call_id)
        content = message.content
        if isinstance(content, list):
            content = [_block_cleared(block, call_id, args) for block in content]
        return message.model_copy(update={"tool_calls": calls, "content": content})

    return clear


def _block_cleared(block: Any, call_id: str, args: Mapping[str, Any]) -> Any:
    if not isinstance(block, dict):
        return block
    if block.get("type") == "tool_use" and block.get("id") == call_id:
        return {**{k: v for k, v in block.items() if k != "partial_json"}, "input": dict(args)}
    if block.get("type") == "function_call" and block.get("call_id") == call_id:
        return {**block, "arguments": json.dumps(args)}
    return block

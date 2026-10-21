"""Every tool result a graph kingfisher built hands the model, recorded as it arrives."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from deepagents.middleware.filesystem import NUM_CHARS_PER_TOKEN
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.core.access import Audience, jointly, listed, stated, unlisted
from kingfisher.core.agent.prompting import FILE_TOOLS, WRITE_TODOS
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.result import MAIN, WORKING, LedgerEntry, Listed

from .recorded import arguments, stamp, summary
from .results import answer_in, answered

if TYPE_CHECKING:
    from kingfisher.core.session.ledger import Ledger

_log = logging.getLogger(__name__)

#: The parent's `task` call a subagent runs inside, or `None` in the agent the caller asked.
#: Set around that call and read by every recorder inside it, on langgraph's threads too.
RUN: ContextVar[str | None] = ContextVar("kingfisher_ledger_run", default=None)

#: What a row would bury the rest of the ledger to say: the file tools, whose reads and
#: writes are a file already, and `write_todos`, one list the conversation carries. Not
#: `execute`: what a command did is what a later turn wants to read back.
UNRECORDED = frozenset({*FILE_TOOLS, WRITE_TODOS})

#: Above how many characters a result is shown as a preview rather than whole. Read off
#: deepagents, whose `FilesystemMiddleware` offloaded at exactly this length until
#: kingfisher turned that off in `standing_middleware`: the same results are previewed as
#: before, so what changed is who writes the file and where, not which calls are affected.
#: Characters, not the UTF-8 bytes a row's `size` counts -- bytes exceed characters for
#: anything but ASCII, and previewing on them would cut a result deepagents left whole.
PREVIEW_ABOVE = NUM_CHARS_PER_TOKEN * 20_000

#: How much of a previewed result the model is shown, split between its head and its tail.
#: Enough to judge the shape of an answer and read its end, at a twentieth of the length
#: that triggered the preview; the whole of it is one `read_file` away, and the last line
#: says where.
PREVIEW_CHARS = 4_000

#: Said where lines were left out, in deepagents' own words: a reader who has seen one
#: before should not have to learn a second spelling.
OMITTED = "... [{lines} lines truncated] ..."


@dataclass(frozen=True)
class Audiences:
    """Who one definition offers each of its tools to: whoever opens it, held to the
    tool's own lock."""

    #: The definition's own audience, its datazone included.
    default: Audience
    #: Each tool's own audience by the name a call dispatches on, where it states one.
    #: Without it a result is stamped wider than the tool admits.
    own: Mapping[str, Audience] = field(default_factory=dict)

    @classmethod
    def of(
        cls, definition: object | None, *, own: Mapping[str, Audience] | None = None
    ) -> Audiences:
        return cls(default=stated(definition).audience, own=own or {})

    def offered(self, tool: str) -> Audience:
        if (own := self.own.get(tool)) is None:
            return self.default
        return jointly((self.default, own))


@contextmanager
def _running(call: Mapping[str, Any]) -> Iterator[None]:
    """Name the run a top-level `task` call starts, for as long as it runs.

    Only where none is named yet: everything inside a subagent, a nested helper's
    calls included, belongs to the run of the call that started it.
    """
    if call.get("name") != TASK_TOOL or RUN.get() is not None:
        yield
        return
    token = RUN.set(call.get("id") or "")
    try:
        yield
    finally:
        RUN.reset(token)


class RecorderMiddleware(AgentMiddleware):
    """Record each successful tool result, keep it as a file in the session, and end
    what the model is handed with the result's id.

    Ahead of the guards in what `standing_middleware` returns, `GuardedCallsMiddleware` among
    them, so it records the arguments the model wrote rather than the host paths they
    become. Sitting inside
    deepagents' eviction and seeing what a deployment's middleware left do not depend on it.
    """

    def __init__(
        self,
        ledger: Ledger,
        backend: Any,
        audiences: Audiences | None,
        tool_results_route: str,
    ) -> None:
        super().__init__()
        self._ledger = ledger
        #: The turn's backend itself: the file tools' rules deny the folder this writes.
        self._backend = backend
        #: `None` where no definition carries a lock.
        self._audiences = audiences
        #: Where a result is written: this layout's `/scratchpad/tool_results/`, which the
        #: backend routes under the same name.
        self._tool_results_route = tool_results_route

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        with _running(request.tool_call):
            result = handler(request)
        answer = answer_in(result, request.tool_call.get("id"))
        if answer is None or (made := self._recorded(request.tool_call, answer)) is None:
            return result
        entry, content, text = made
        try:
            answers = self._backend.upload_files([(entry.path, content)])
        except Exception as exc:  # noqa: BLE001 -- a save must never fail the call
            answers = exc
        self._unkept(entry, answers)
        return _stamped(result, answer, entry.id, _shown(answer, entry, text))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        with _running(request.tool_call):
            result = await handler(request)
        answer = answer_in(result, request.tool_call.get("id"))
        if answer is None or (made := self._recorded(request.tool_call, answer)) is None:
            return result
        entry, content, text = made
        try:
            answers = await self._backend.aupload_files([(entry.path, content)])
        except Exception as exc:  # noqa: BLE001 -- a save must never fail the call
            answers = exc
        self._unkept(entry, answers)
        return _stamped(result, answer, entry.id, _shown(answer, entry, text))

    def _recorded(
        self, call: Mapping[str, Any], answer: ToolMessage
    ) -> tuple[LedgerEntry, bytes, str] | None:
        """The entry for one call, the bytes its file holds and the result as text, or
        `None` to record nothing."""
        tool = str(call.get("name") or "")
        if tool in UNRECORDED:
            return None
        # A failed call has nothing worth finding again, and the conversation shows it.
        if answer.status == "error":
            return None
        text = str(answer.text)
        # The text alone: the ledger finds deepagents' copy of an offloaded result by this
        # length, so what the text leaves out is said in the summary instead.
        content = text.encode("utf-8")
        call_id = str(call.get("id") or "")
        run = RUN.get()
        args = arguments(call.get("args") or {})
        access = self._access(tool, run or call_id)
        entry = self._ledger.add(
            lambda number: LedgerEntry(
                id=number,
                turn=self._ledger.turn,
                call=call_id,
                run=run,
                role=MAIN if run is None else WORKING,
                tool=tool,
                args=args,
                access=access,
                size=len(content),
                path=f"{self._tool_results_route}{number}-{_segment(tool)}{_suffix(text)}",
                summary=summary(tool, args, text, answer.content_blocks),
            )
        )
        return entry, content, text

    def _access(self, tool: str, inside: str) -> Listed | None:
        """The audience this result was produced under, as a row holds it.

        `inside` is the run a `task` answer's subagent ran as. That answer can repeat
        anything the run read, so it is reached by whoever reaches all of it.
        """
        if self._audiences is None:
            return None
        offered = self._audiences.offered(tool)
        if tool != TASK_TOOL:
            return listed(offered)
        read = (unlisted(one) for one in self._ledger.access_in(inside))
        return listed(jointly((offered, *read)))

    def _unkept(self, entry: LedgerEntry, answers: Any) -> None:
        """Take the path off an entry if the backend would not keep its file.

        Reported on the row and in the log, never raised: the tool has run, and a failed
        call here is one the model may make again, running the tool twice.
        """
        if isinstance(answers, Exception):
            why = f"{type(answers).__name__}: {answers}"
        elif answers and answers[0].error:
            why = str(answers[0].error)
        else:
            return
        _log.warning(
            "the result of %s %s was not kept at %s: %s", entry.tool, entry.id, entry.path, why
        )
        self._ledger.unkept(entry)


def _preview(text: str, path: str) -> str:
    """A long result as its head and its tail, with a last line saying where the whole is.

    Lines rather than characters: a result cut mid-line reads as corrupt, and the head and
    tail of a table or a log are what a reader judges it by.
    """
    half = PREVIEW_CHARS // 2
    lines = text.splitlines()
    head: list[str] = []
    tail: list[str] = []
    for line in lines:
        if sum(len(one) + 1 for one in head) + len(line) > half:
            break
        head.append(line)
    for line in reversed(lines[len(head) :]):
        if sum(len(one) + 1 for one in tail) + len(line) > half:
            break
        tail.append(line)
    tail.reverse()
    omitted = len(lines) - len(head) - len(tail)
    middle = [OMITTED.format(lines=omitted)] if omitted > 0 else []
    return "\n".join([*head, *middle, *tail, f"full result: {path}"])


def _shown(answer: ToolMessage, entry: LedgerEntry, text: str) -> Any | None:
    """The content the model is handed for this result, or `None` to hand it whole.

    The content and not a message: `answered` finds the original by identity, so a copy
    made here would not be found and the result would go back unchanged.

    A result made of blocks keeps every block that is not text -- an attachment is not
    something a preview can stand in for -- and its text becomes one preview block.
    """
    if len(text) <= PREVIEW_ABOVE or entry.path is None:
        return None
    shown = _preview(text, entry.path)
    content = answer.content
    if isinstance(content, str):
        return shown
    # In place, where the first text block was: a caption before an image reads as its
    # caption, and moving the text to the end would say something the tool did not.
    def _text(one: Any) -> bool:
        return isinstance(one, dict) and one.get("type") == "text"

    at = next((n for n, one in enumerate(content) if _text(one)), len(content))
    rest = [one for n, one in enumerate(content) if not _text(one) and n > at]
    return [
        *[one for n, one in enumerate(content) if not _text(one) and n < at],
        {"type": "text", "text": shown},
        *rest,
    ]


def _stamped(result: Any, answer: ToolMessage, number: str, shown: Any | None = None) -> Any:
    """The result with its id as a last line, in what the model is handed.

    Copied, not changed in place: a tool may hand back a message it still holds. A list of
    blocks gains one starting on a new line, because the transcript and `.text` join blocks
    with nothing between them. `shown` stands in for the content where the whole of it is
    too long to show; `answer` stays the one found by identity either way.
    """
    mark = stamp(number)
    content = answer.content if shown is None else shown
    marked = answer.model_copy(
        update={
            "content": f"{content}{mark}"
            if isinstance(content, str)
            else [*content, {"type": "text", "text": mark}]
        }
    )
    return answered(result, answer, marked)


#: A tool's name may come from an MCP server or a module author, and the file name
#: has to be one path segment however it was spelled.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")


def _segment(tool: str) -> str:
    return _UNSAFE.sub("_", tool).strip("_")[:64] or "tool"


def _suffix(text: str) -> str:
    try:
        json.loads(text)
    except ValueError:
        return ".txt"
    return ".json"

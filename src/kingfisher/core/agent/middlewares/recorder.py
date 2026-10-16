"""Every tool result a graph kingfisher built hands the model, recorded as it arrives."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from kingfisher.core.access.source_ids import Audience, jointly, listed, stated, unlisted
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.definitions.tools.spec import split_reference
from kingfisher.core.layout import TOOL_RESULTS_ROUTE
from kingfisher.core.result import MAIN, WORKING, LedgerEntry, Listed

if TYPE_CHECKING:
    from kingfisher.core.session.ledger import Ledger

_log = logging.getLogger(__name__)

#: The parent's `task` call a subagent is running inside, or `None` in the agent the
#: caller asked. Set by the agent's recorder around that call and read by every
#: recorder inside the subagent's graph, including on the threads langgraph runs
#: calls made together on -- measured, and recorded in `findings.md`.
RUN: ContextVar[str | None] = ContextVar("kingfisher_ledger_run", default=None)

#: What a row would bury the rest of the ledger to say. deepagents' file tools, whose
#: reads and writes are a file already; and `write_todos`, which rewrites one list the
#: conversation already carries -- three updates and one real search made three of the
#: four delta lines a todo list, each repeating it in `args` as well. `execute` is not
#: among them: what a command did is exactly what a later turn wants to read back.
UNRECORDED = frozenset({
    "ls", "glob", "grep", "read_file", "write_file", "edit_file", "delete", "write_todos",
})

#: How much of one argument, and of a result's first line, a row keeps.
ARG_LIMIT = 60
LINE_LIMIT = 100


@dataclass(frozen=True)
class Audiences:
    """Who one definition offers each of its tools to."""

    default: Audience
    #: By the name a call dispatches on, which is how a tool reaches a recorder.
    tools: Mapping[str, Audience]
    #: Each tool's own audience, by the same name, where it states one. Without it a
    #: result is stamped at whatever the definition offered the tool at, and a later
    #: caller the tool itself shuts out is shown what it found.
    own: Mapping[str, Audience] = field(default_factory=dict)

    @classmethod
    def of(
        cls, definition: object | None, *, own: Mapping[str, Audience] | None = None
    ) -> Audiences:
        """The definition's own audience, and each `tools:` entry's where it wrote one."""
        said = stated(definition)
        return cls(
            default=said.audience,
            tools={split_reference(name)[1]: who for name, who in said.of("tools").items()},
            own=own or {},
        )

    def offered(self, tool: str) -> Audience:
        granted = self.tools.get(tool, self.default)
        if (own := self.own.get(tool)) is None:
            return granted
        return jointly((granted, own))


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


class Recorder(AgentMiddleware):
    """Record each successful tool result, keep it as a file in the session, and end
    what the model is handed with the result's id.

    First in what `tool_guards` returns, for one thing: it is ahead of
    `WorkspaceToolPaths`, so it records the arguments the model wrote rather than the
    host paths they become. Moved last,
    `test_the_arguments_recorded_are_the_ones_the_model_wrote` is the only test in the
    suite that notices.

    Two properties read like further reasons for this position and are not. Being
    inside deepagents' eviction -- so a long result is seen whole, and the stub the
    eviction leaves shows the id in its tail -- comes from being custom middleware at
    all: deepagents lands every custom entry after the last of its own core ones, and
    the eviction is `FilesystemMiddleware`'s. Recording what a deployment's middleware
    left comes from that middleware being appended after everything here, which
    `test_what_a_deployments_middleware_left_is_recorded` holds. Both would survive
    this moving.
    """

    def __init__(
        self,
        ledger: Ledger,
        backend: Any,
        audiences: Audiences | None,
        tool_results_route: str = TOOL_RESULTS_ROUTE,
    ) -> None:
        super().__init__()
        self._ledger = ledger
        #: The turn's backend under the turn's rules, as a tool's own save goes through.
        self._backend = backend
        #: `None` where no `source_ids.yaml` is in force.
        self._audiences = audiences
        #: Where a result is written: this layout's `/scratchpad/tool_results/`, which
        #: the backend routes under the same name. The default is the module's.
        self._tool_results_route = tool_results_route

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        with _running(request.tool_call):
            result = handler(request)
        answer = _answer_in(result, request.tool_call.get("id"))
        if answer is None or (made := self._recorded(request.tool_call, answer)) is None:
            return result
        entry, content = made
        try:
            answers = self._backend.upload_files([(entry.path, content)])
        except Exception as exc:  # noqa: BLE001 -- a save must never fail the call
            answers = exc
        self._unkept(entry, answers)
        return _stamped(result, answer, entry.id)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        with _running(request.tool_call):
            result = await handler(request)
        answer = _answer_in(result, request.tool_call.get("id"))
        if answer is None or (made := self._recorded(request.tool_call, answer)) is None:
            return result
        entry, content = made
        try:
            answers = await self._backend.aupload_files([(entry.path, content)])
        except Exception as exc:  # noqa: BLE001 -- a save must never fail the call
            answers = exc
        self._unkept(entry, answers)
        return _stamped(result, answer, entry.id)

    def _recorded(
        self, call: Mapping[str, Any], answer: ToolMessage
    ) -> tuple[LedgerEntry, bytes] | None:
        """The entry for one call and the bytes its file holds, or `None` to record nothing."""
        tool = str(call.get("name") or "")
        if tool in UNRECORDED:
            return None
        # A failed call has nothing worth finding again, and the conversation shows it.
        if answer.status == "error":
            return None
        text = str(answer.text)
        content = text.encode("utf-8")
        call_id = str(call.get("id") or "")
        run = RUN.get()
        args = _arguments(call.get("args") or {})
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
                summary=_summary(tool, args, text),
            )
        )
        return entry, content

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

        Reported on the row and in the log, and never raised: the tool has run and
        the model is about to read its result, and a failed call here would be one the
        model may make again, running the tool twice.
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


def _answer_in(result: Any, call_id: Any) -> ToolMessage | None:
    """The message that answers this call, inside the `Command` `task` returns too."""
    if isinstance(result, ToolMessage):
        return result
    if not isinstance(result, Command) or not isinstance(result.update, Mapping):
        return None
    found = [one for one in result.update.get("messages") or () if isinstance(one, ToolMessage)]
    return next((one for one in found if one.tool_call_id == call_id), None)


def _stamped(result: Any, answer: ToolMessage, number: str) -> Any:
    """The result with its id as a last line, in what the model is handed.

    Copied rather than changed in place: a tool may hand back a message it still
    holds, and the id would go wherever else that message goes. A list of blocks gains
    a block whose text starts on a new line, because the transcript and `.text` join
    blocks with nothing between them, and the bare id would read as the end of the
    result's last line.
    """
    mark = f"\n[{number}]"
    content = answer.content
    marked = answer.model_copy(
        update={
            "content": f"{content}{mark}"
            if isinstance(content, str)
            else [*content, {"type": "text", "text": mark}]
        }
    )
    if result is answer:
        return marked
    messages = [marked if one is answer else one for one in result.update["messages"]]
    return replace(result, update={**result.update, "messages": messages})


def _cut(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _arguments(args: Mapping[str, Any]) -> str:
    """Each argument as `name=value`. `repr` keeps a value on one line and a string
    in its quotes, so `path='/inputs/a b.csv'` reads as one argument.
    """
    return " ".join(f"{name}={_cut(repr(value), ARG_LIMIT)}" for name, value in args.items())


def _summary(tool: str, args: str, text: str) -> str:
    """The tool, its arguments and the first line of what it returned."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    called = f"{tool} {args}" if args else tool
    return f"{called} -- {_cut(first, LINE_LIMIT)}" if first else called


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

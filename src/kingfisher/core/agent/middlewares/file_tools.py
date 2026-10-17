"""deepagents' file tools as a model is handed them: what they say and what they are called
with, changed from outside, as `ShellPaths` changes `execute`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

READ_FILE = "read_file"
GREP = "grep"

#: The lines `read_file` returns when the model names no limit: under deepagents'
#: 80,000-character cut for code of ordinary width, so most files come back in one call.
READ_LINES = 1_000

#: deepagents' advice to page, which its own 100-line default made necessary.
PAGE = (
    f"reads up to {DEFAULT_READ_LIMIT} lines starting from the beginning of the file. Use "
    "`offset`/`limit` to page through large files instead of reading them whole."
)

#: What replaces it.
WHOLE = (
    f"reads up to {READ_LINES:,} lines from the start of the file; read a file whole when "
    "it fits, and use `offset`/`limit` only beyond that."
)

#: deepagents' advice on several strings, untrue once `grep` splits `|` itself.
SEPARATELY = (
    'To match any of several strings, run a separate grep for each; `grep(pattern="foo|bar")` '
    'searches for the literal text "foo|bar", and'
)

#: What replaces it.
JOINED = (
    'To match any of several strings, join them with `|`: `grep(pattern="foo|bar")` searches '
    'for "foo" and for "bar", each literally, while'
)


@cache
def _shown(schema: Any) -> dict[str, Any]:
    """`schema` as the model is shown it, with `READ_LINES` as `limit`'s default."""
    shown = schema.model_json_schema()
    shown["properties"]["limit"]["default"] = READ_LINES
    return shown


def _replaced(description: str, theirs: str, ours: str, appended: str) -> str:
    # Appended when deepagents has reworded its line, so ours still arrives and a test
    # fails on the advice left beside it.
    if theirs in description:
        return description.replace(theirs, ours)
    return f"{description}\n- {appended}"


def _offered(tool: Any) -> Any:
    if not isinstance(tool, BaseTool):
        return tool
    if tool.name == GREP:
        return tool.model_copy(
            update={"description": _replaced(tool.description, SEPARATELY, JOINED, JOINED)}
        )
    if tool.name != READ_FILE or not hasattr(tool.args_schema, "model_json_schema"):
        return tool
    # Only what the model is shown: the call is run by deepagents' own tool, whose
    # default `_called` fills in.
    described = _replaced(tool.description, PAGE, WHOLE, f"By default it {WHOLE}")
    return tool.model_copy(
        update={"description": described, "args_schema": _shown(tool.args_schema)}
    )


def alternatives(pattern: str) -> list[str] | None:
    """The strings a `grep` pattern joins with `|`, or `None` where it is one string.

    Not split where a part is empty, so `| a |`, a table cell, is still searched as written.
    """
    parts = pattern.split("|")
    return parts if len(parts) > 1 and all(parts) else None


def _parted(call: dict[str, Any], part: str, n: int) -> dict[str, Any]:
    return {**call, "id": f"{call['id']}-{n}", "args": {**call["args"], "pattern": part}}


def _joined(call: dict[str, Any], parts: list[str], answers: list[Any]) -> Any:
    """One answer for the call the model made, each part's under a line naming it."""
    if not all(isinstance(one, ToolMessage) for one in answers):
        return answers[0]
    said = [f"Searched `{call['args']['pattern']}` as separate literal parts."]
    said += [f"matches for `{part}`:\n{one.text}" for part, one in zip(parts, answers, strict=True)]
    failed = all(one.status == "error" for one in answers)
    return ToolMessage(
        content="\n\n".join(said),
        tool_call_id=call["id"],
        name=call.get("name"),
        status="error" if failed else "success",
    )


class FileTools(AgentMiddleware):
    """Have `read_file` read 1,000 lines unless told otherwise, and `grep` search each string
    of `a|b`, and say so.

    At deepagents' 100 lines, a third of every call a turn made was `read_file` paging, most
    naming a limit of 100 because the description did: so the description changes with the
    default, and a limit the model names is left as it is. `grep` is literal, and models
    wrote `a|b` anyway, nearly always finding nothing and searching again.
    """

    def _told(self, request: Any) -> Any:
        tools = [_offered(tool) for tool in request.tools]
        if all(new is old for new, old in zip(tools, request.tools, strict=True)):
            return request
        return request.override(tools=tools)

    def _called(self, request: Any) -> Any:
        call = request.tool_call
        if call.get("name") != READ_FILE or "limit" in (call.get("args") or {}):
            return request
        return request.override(tool_call={**call, "args": {**call["args"], "limit": READ_LINES}})

    def _split(self, request: Any) -> list[str] | None:
        call = request.tool_call
        if call.get("name") != GREP:
            return None
        return alternatives(str((call.get("args") or {}).get("pattern", "")))

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._told(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._told(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if (parts := self._split(request)) is None:
            return handler(self._called(request))
        call = request.tool_call
        answers = [
            handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return _joined(call, parts, answers)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if (parts := self._split(request)) is None:
            return await handler(self._called(request))
        call = request.tool_call
        answers = [
            await handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return _joined(call, parts, answers)

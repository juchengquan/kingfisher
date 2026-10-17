"""deepagents' file tools as a model is handed them: what they say and what they are called
with, changed from outside, as `ShellPaths` changes `execute`."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT
from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

READ_FILE = "read_file"

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


@cache
def _shown(schema: Any) -> dict[str, Any]:
    """`schema` as the model is shown it, with `READ_LINES` as `limit`'s default."""
    shown = schema.model_json_schema()
    shown["properties"]["limit"]["default"] = READ_LINES
    return shown


def _whole(description: str) -> str:
    # Appended when deepagents has reworded its line, so the advice still arrives and a test
    # fails on the paging advice left beside it.
    if PAGE in description:
        return description.replace(PAGE, WHOLE)
    return f"{description}\n- By default it {WHOLE}"


def _offered(tool: Any) -> Any:
    if not (isinstance(tool, BaseTool) and tool.name == READ_FILE):
        return tool
    schema = tool.args_schema
    if not hasattr(schema, "model_json_schema"):
        return tool
    # Only what the model is shown: the call is run by deepagents' own tool, whose
    # default `_called` fills in.
    return tool.model_copy(
        update={"description": _whole(tool.description), "args_schema": _shown(schema)}
    )


class FileTools(AgentMiddleware):
    """Have `read_file` read 1,000 lines unless told otherwise, and say so.

    At deepagents' 100, a third of every call a turn made was `read_file` paging, and most
    of those named a limit of 100 because the description did: so the description changes
    with the default, and a limit the model names is left as it is.
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

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._told(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._told(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._called(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._called(request))

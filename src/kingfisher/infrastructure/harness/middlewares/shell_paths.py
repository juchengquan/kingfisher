"""Telling whoever holds the shell how a file-tool path is spelled there."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from kingfisher.layout import INPUTS, SESSION_DIRS

EXECUTE = "execute"

#: deepagents' advice in `execute`'s description, wrong here: the only absolute paths a
#: model knows are the file tools', and the shell reads `/inputs` from the host's root.
ABSOLUTE = (
    "- Use absolute paths and avoid `cd` so the working directory stays stable; use the "
    "optional timeout to override the default."
)

#: What replaces it. In the description rather than the system prompt because a
#: subagent is built with its own prompt and none of `system.md`, and the description
#: reaches every graph holding the shell.
RULE = (
    "- The shell starts in the session root, the directory the file tools call `/`. A "
    "file-tool path is a shell path without its leading slash -- "
    f"`/{INPUTS}/data.csv` is `{INPUTS}/data.csv` -- on the command line and inside "
    "any script the command runs. Avoid `cd`, so that stays true; use the optional "
    "timeout to override the default."
)

#: A session folder spelt as the file tools spell it, and not as the tail of a longer
#: path: `/inputs` in `/Users/me/sessions/s/inputs` is a host path, and a right one.
_VIRTUAL = re.compile(
    rf"(?<![\w.~/-])/(?:{'|'.join(map(re.escape, SESSION_DIRS))})(?=[/\s'\"`:,;)]|$)"
    r"[^\s'\"`:,;)]*"
)

#: What a shell, `python` and the usual tools say of a path they could not use. A
#: refusal counts as a miss: a file-tool path the fence stops is still the wrong path.
_MISSING = (
    "No such file or directory",
    "can't open file",
    "cannot access",
    "does not exist",
    "not found",
    "Permission denied",
    "Operation not permitted",
    "Read-only file system",
)


def missed(output: str) -> str | None:
    """The first file-tool path the shell reported missing in `output`, if any.

    Both on one line, so a path the command printed and an unrelated miss elsewhere in
    its output are not read as one.
    """
    for line in output.splitlines():
        if any(said in line for said in _MISSING) and (found := _VIRTUAL.search(line)):
            return found.group()
    return None


def hint(path: str) -> str:
    """The correction appended to a shell result that missed `path`."""
    return (
        f"`{path}` is a file-tool path, which the shell reads from the host's root. The "
        f"shell starts in the session root, so there it is `{path.lstrip('/')}` -- in "
        "the command and in any script it runs."
    )


def _described(tool: Any) -> Any:
    if isinstance(tool, BaseTool) and tool.name == EXECUTE:
        return tool.model_copy(update={"description": _ruled(tool.description)})
    if isinstance(tool, dict) and tool.get("name") == EXECUTE:
        return {**tool, "description": _ruled(tool.get("description") or "")}
    return tool


def _ruled(description: str) -> str:
    # Appended when deepagents has reworded its line, so the rule still arrives;
    # `test_every_graph_holding_the_shell_is_told_its_paths_rather_than_absolute_ones`
    # then fails on the advice left beside it, which is the signal to look.
    if ABSOLUTE in description:
        return description.replace(ABSOLUTE, RULE)
    return f"{description}\n\n{RULE}"


class ShellPaths(AgentMiddleware):
    """State the shell's path rule in `execute`'s description, and again on a miss."""

    def _told(self, request: Any) -> Any:
        tools = [_described(tool) for tool in request.tools]
        if all(new is old for new, old in zip(tools, request.tools, strict=True)):
            return request
        return request.override(tools=tools)

    def _corrected(self, request: Any, result: Any) -> Any:
        if request.tool_call.get("name") != EXECUTE or not isinstance(result, ToolMessage):
            return result
        if not isinstance(result.content, str) or (path := missed(result.content)) is None:
            return result
        return result.model_copy(update={"content": f"{result.content}\n\n{hint(path)}"})

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._told(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._told(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._corrected(request, handler(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return self._corrected(request, await handler(request))

"""Telling the shell tool which spelling of a session path is its own."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool

from kingfisher.layout import DATA, DERIVED, SCRATCH

#: The sentence of deepagents' `execute` description that teaches the wrong spelling.
#: "Absolute" reads as a leading slash, and in the shell a leading slash is the host's
#: root: told this beside the tool, the agent passed `/data/<name>` to the shell in most
#: runs of the smoke task, whatever `system.md` said about dropping the slash.
ABSOLUTE_PATHS = "Use absolute paths and avoid `cd` so the working directory stays stable; "

#: What replaces it. Ends as the sentence it replaces does, because the rest of that
#: line -- the timeout -- is deepagents' and stays.
SHELL_PATHS = (
    "Every command starts in the session root, so a session path is its file-tool path "
    f"without the leading slash: `{DATA}/<name>`, `{DERIVED}/<name>`, `{SCRATCH}/<name>`. "
    "A path starting with `/` is the host's root, where none of them exist. Avoid `cd`, "
    "since those paths are relative to where the command started; "
)


def _respelled(tool: Any) -> Any:
    """The shell tool with `SHELL_PATHS` in place of `ABSOLUTE_PATHS`; anything else as is."""
    if not isinstance(tool, BaseTool) or tool.name != "execute":
        return tool
    if ABSOLUTE_PATHS not in tool.description:
        return tool
    return tool.model_copy(
        update={"description": tool.description.replace(ABSOLUTE_PATHS, SHELL_PATHS)}
    )


# Rewritten per model call rather than once, because deepagents writes this description
# per call too -- it picks a variant by which search tools the model can see -- and this
# has to see the one it picked. A sentence that has moved is left alone rather than
# guessed at, and `test_the_shell_is_told_its_own_spelling` goes red when that happens.
class ShellPathSpelling(AgentMiddleware):
    """Tell the shell tool which spelling of a session path is its own."""

    def _respelled(self, request: Any) -> Any:
        return request.override(tools=[_respelled(tool) for tool in request.tools])

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._respelled(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._respelled(request))

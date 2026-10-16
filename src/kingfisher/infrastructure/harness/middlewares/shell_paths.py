"""Telling whoever holds the shell how a file-tool path is spelled there, in the words
the shell gives of itself."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from kingfisher.infrastructure.prompting import ShellPrompt, execute_line, kingfishers_shell
from kingfisher.layout import DEFAULT_LAYOUT, SESSION_DIRS, Layout

EXECUTE = "execute"

#: deepagents' advice in `execute`'s description, wrong on kingfisher's own shell: the
#: only absolute paths a model knows are the file tools', and that shell reads `/inputs`
#: from the host's root.
ABSOLUTE = (
    "- Use absolute paths and avoid `cd` so the working directory stays stable; use the "
    "optional timeout to override the default."
)


#: How deepagents opens the section it appends to the system prompt on every model call
#: where the backend executes, giving each routed folder's path on this host. It is a
#: block of the system message of its own, which is what lets it be taken out whole.
MAPPINGS = "## Shell paths vs. virtual paths"


def _virtual(session_dirs: tuple[str, ...]) -> re.Pattern[str]:
    """A regex matching one of this layout's session folders spelt as the file tools
    spell it, and not as the tail of a longer path: `/inputs` in
    `/Users/me/sessions/s/inputs` is a host path, and a right one.
    """
    return re.compile(
        rf"(?<![\w.~/-])/(?:{'|'.join(map(re.escape, session_dirs))})(?=[/\s'\"`:,;)]|$)"
        r"[^\s'\"`:,;)]*"
    )


#: The default-layout matcher, which a test importing `missed` reads; a renamed layout
#: builds its own on the instance.
_VIRTUAL = _virtual(SESSION_DIRS)

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


def missed(output: str, virtual: re.Pattern[str] = _VIRTUAL) -> str | None:
    """The first file-tool path the shell reported missing in `output`, if any.

    Both on one line, so a path the command printed and an unrelated miss elsewhere in
    its output are not read as one. `virtual` defaults to the default layout's matcher;
    `ShellPaths` passes the configured layout's.
    """
    for line in output.splitlines():
        if any(said in line for said in _MISSING) and (found := virtual.search(line)):
            return found.group()
    return None


def hint(path: str) -> str:
    """The correction appended to a shell result that missed `path`."""
    return (
        f"`{path}` is a file-tool path, which the shell reads from the host's root. The "
        f"shell starts in the session root, so there it is `{path.lstrip('/')}` -- in "
        "the command and in any script it runs."
    )


def _described(tool: Any, rule: str) -> Any:
    if isinstance(tool, BaseTool) and tool.name == EXECUTE:
        return tool.model_copy(update={"description": _ruled(tool.description, rule)})
    if isinstance(tool, dict) and tool.get("name") == EXECUTE:
        return {**tool, "description": _ruled(tool.get("description") or "", rule)}
    return tool


def _ruled(description: str, rule: str) -> str:
    # Appended when deepagents has reworded its line, so the rule still arrives;
    # `test_every_graph_holding_the_shell_is_told_its_paths_rather_than_absolute_ones`
    # then fails on the advice left beside it, which is the signal to look.
    if ABSOLUTE in description:
        return description.replace(ABSOLUTE, rule)
    return f"{description}\n\n{rule}"


def _is_mappings(block: Any) -> bool:
    return block.get("type") == "text" and str(block.get("text", "")).lstrip().startswith(MAPPINGS)


def _unmapped(request: Any) -> Any:
    """`request` without deepagents' section of host paths.

    By block and not by cutting text: a merged section is then left whole, host paths
    and all, rather than the prose around it losing its tail with nothing to show for
    it. The parts cannot merge today -- deepagents appends this section with
    `append_to_system_message`, and builds `FilesystemMiddleware` with no system prompt
    of its own -- so the distinction is held by
    `test_a_mappings_section_merged_into_other_prose_is_left_whole` and not by
    `test_deepagents_host_paths_reach_only_a_shell_on_this_host`, which passes whichever
    way the section goes.
    """
    system = request.system_message
    if system is None:
        return request
    blocks = list(system.content_blocks)
    kept = [block for block in blocks if not _is_mappings(block)]
    if len(kept) == len(blocks):
        return request
    return request.override(system_message=SystemMessage(content_blocks=kept))


class ShellPaths(AgentMiddleware):
    """Put the shell's own line in `execute`'s description, on kingfisher's own shell say
    how a missed path is spelt there, and keep deepagents' host paths from a shell that
    is not on this host.

    The line goes in the description rather than the system prompt because a subagent is
    built with its own prompt and none of `system.md`, and the description reaches every
    graph holding the shell.
    """

    def __init__(
        self,
        layout: Layout = DEFAULT_LAYOUT,
        shell: ShellPrompt | None = None,
        *,
        here: bool = True,
    ) -> None:
        super().__init__()
        # Whether the shell runs on this host. Where it does not, deepagents' section
        # names paths it does not have, and `shell` is the only word on the subject.
        self._here = here
        # From the layout, so the line names the configured inputs leaf and the
        # miss-matcher recognises the configured session folders.
        self._rule = execute_line(shell, layout)
        # Kingfisher's own shell only. The correction spells the path the way a shell
        # starting in the session root reads it, and no other shell is known to.
        self._corrects = shell is kingfishers_shell()
        self._virtual = _virtual(layout.session_dirs)

    def _told(self, request: Any) -> Any:
        if self._rule is not None:
            tools = [_described(tool, self._rule) for tool in request.tools]
            if not all(new is old for new, old in zip(tools, request.tools, strict=True)):
                request = request.override(tools=tools)
        return request if self._here else _unmapped(request)

    def _corrected(self, request: Any, result: Any) -> Any:
        if not self._corrects:
            return result
        if request.tool_call.get("name") != EXECUTE or not isinstance(result, ToolMessage):
            return result
        if not isinstance(result.content, str):
            return result
        if (path := missed(result.content, self._virtual)) is None:
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

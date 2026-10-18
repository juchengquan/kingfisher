"""deepagents' built-in tools as a model is handed them: what `execute`, `read_file` and
`grep` say, what they are called with, and what the system message says of them, changed
from outside."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable
from functools import cache
from typing import Any

from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT, FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from kingfisher.core.agent.prompting import EXECUTE, ShellPrompt, execute_line
from kingfisher.core.definitions.skills import READ_FILE
from kingfisher.core.layout import DEFAULT_LAYOUT, SESSION_DIRS, Layout

WRITE_TODOS = "write_todos"

#: deepagents' advice in `execute`'s description, wrong on kingfisher's own shell: the
#: only absolute paths a model knows are the file tools', and that shell reads `/inputs`
#: from the host's root.
ABSOLUTE = (
    "- Use absolute paths and avoid `cd` so the working directory stays stable; use the "
    "optional timeout to override the default."
)


#: deepagents' longest `timeout`, which kingfisher leaves at its default.
_LONGEST = (
    inspect.signature(FilesystemMiddleware.__init__).parameters["max_execute_timeout"].default
)

#: Said on every shell: models passed milliseconds, 5000 to 40000, and each was refused.
TIMEOUT = f"- `timeout` is in seconds, {_LONGEST:,} at most."


#: How deepagents opens the section of host paths it appends to the system prompt where the
#: backend executes. It is a block of its own, so it can be taken out whole.
MAPPINGS = "## Shell paths vs. virtual paths"

#: How `TodoListMiddleware` opens what it appends, a block of its own too.
TODOS = WRITE_TODOS_SYSTEM_PROMPT.lstrip().splitlines()[0]


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
    its output are not read as one.
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


def _described(tool: Any, rule: str | None) -> Any:
    if isinstance(tool, BaseTool) and tool.name == EXECUTE:
        return tool.model_copy(update={"description": _timed(_ruled(tool.description, rule))})
    if isinstance(tool, dict) and tool.get("name") == EXECUTE:
        return {**tool, "description": _timed(_ruled(tool.get("description") or "", rule))}
    return tool


def _timed(description: str) -> str:
    return description if TIMEOUT in description else f"{description}\n{TIMEOUT}"


def _ruled(description: str, rule: str | None) -> str:
    if rule is None:
        return description
    # Appended when deepagents has reworded its line, so the rule still arrives and a test
    # fails on the advice left beside it.
    if ABSOLUTE in description:
        return description.replace(ABSOLUTE, rule)
    return f"{description}\n\n{rule}"


def _opens(block: Any, headings: tuple[str, ...]) -> bool:
    return block.get("type") == "text" and str(block.get("text", "")).lstrip().startswith(
        headings
    )


def _without(request: Any, headings: tuple[str, ...]) -> Any:
    """`request` without the system-message sections opening with one of `headings`.

    By block, not by cutting text: a section merged into other prose is left whole rather
    than the prose around it losing its tail.
    """
    system = request.system_message
    if system is None or not headings:
        return request
    blocks = list(system.content_blocks)
    kept = [block for block in blocks if not _opens(block, headings)]
    if len(kept) == len(blocks):
        return request
    return request.override(system_message=SystemMessage(content_blocks=kept))


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


def _limited(request: Any) -> Any:
    """`request` with `READ_LINES` as the limit of a `read_file` call that names none."""
    call = request.tool_call
    if call.get("name") != READ_FILE or "limit" in (call.get("args") or {}):
        return request
    return request.override(tool_call={**call, "args": {**call["args"], "limit": READ_LINES}})


def _split(request: Any) -> list[str] | None:
    call = request.tool_call
    if call.get("name") != GREP:
        return None
    return alternatives(str((call.get("args") or {}).get("pattern", "")))


class BuiltinToolsMiddleware(AgentMiddleware):
    """Put the shell's own line in `execute`'s description, on kingfisher's own shell say
    how a missed path is spelt there, and keep deepagents' host paths from a shell that
    is not on this host; have `read_file` read 1,000 lines unless told otherwise, and
    `grep` search each string of `a|b`, and say so; and say nothing of the shell or of
    `write_todos` to a graph not holding it.

    The shell's line goes in the description, not the system prompt: a subagent gets none
    of `system.md`, and the description reaches every graph holding the shell. At
    deepagents' 100 lines, a third of every call a turn made was `read_file` paging, most
    naming a limit of 100 because the description did: so the description changes with
    the default, and a limit the model names is left as it is. `grep` is literal, and
    models wrote `a|b` anyway, nearly always finding nothing and searching again. The
    prose deepagents and langchain add of a tool arrives whether or not the graph holds
    it, and a model told of a tool calls it, each call spent on a refusal.
    """

    def __init__(
        self,
        layout: Layout = DEFAULT_LAYOUT,
        shell: ShellPrompt | None = None,
        *,
        here: bool = True,
    ) -> None:
        super().__init__()
        # Whether the shell runs on this host; where not, deepagents' section names paths it
        # does not have.
        self._here = here
        self._rule = execute_line(shell, layout)
        # Only where the shell starts in the session root: the correction spells paths as it
        # reads them.
        self._corrects = shell is not None and shell.starts_in_session
        self._virtual = _virtual(layout.session_dirs)

    def _told(self, request: Any) -> Any:
        tools = [_offered(_described(tool, self._rule)) for tool in request.tools]
        if not all(new is old for new, old in zip(tools, request.tools, strict=True)):
            request = request.override(tools=tools)
        # Inside `GrantedOnlyMiddleware`, so the request holds only what was granted.
        held = {
            tool.get("name") if isinstance(tool, dict) else getattr(tool, "name", None)
            for tool in request.tools
        }
        untold = []
        if not (self._here and EXECUTE in held):
            untold.append(MAPPINGS)
        if WRITE_TODOS not in held:
            untold.append(TODOS)
        return _without(request, tuple(untold))

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
        if (parts := _split(request)) is None:
            return self._corrected(request, handler(_limited(request)))
        call = request.tool_call
        answers = [
            handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return _joined(call, parts, answers)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if (parts := _split(request)) is None:
            return self._corrected(request, await handler(_limited(request)))
        call = request.tool_call
        answers = [
            await handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return _joined(call, parts, answers)

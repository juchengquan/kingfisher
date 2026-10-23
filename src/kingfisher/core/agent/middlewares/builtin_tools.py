"""deepagents' built-in tools as a model is handed them: what they say, what `read_file`
and `grep` are called with, and what the system message says of them, changed from
outside."""

from __future__ import annotations

import inspect
import re
from collections.abc import Awaitable, Callable, Collection, Iterator
from functools import cache
from pathlib import PurePosixPath
from typing import Any

from deepagents.backends.utils import _get_backend_read_file_type, validate_path
from deepagents.middleware.filesystem import DEFAULT_READ_LIMIT, FilesystemMiddleware
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.todo import WRITE_TODOS_SYSTEM_PROMPT
from langchain_core.messages import SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from kingfisher.core.agent.backend import folders_of, miscased, strays
from kingfisher.core.agent.prompting import (
    DELETE,
    EDIT_FILE,
    EXECUTE,
    GLOB,
    GREP,
    LS,
    WRITE_FILE,
    WRITE_TODOS,
    ShellPrompt,
    execute_line,
    miscased_path,
    stray_save,
    what_you_make_goes,
)
from kingfisher.core.definitions.skills import READ_FILE
from kingfisher.core.layout import Layout

from .narrowing import tool_name
from .results import failed

#: deepagents' advice in `execute`'s description, wrong on kingfisher's own shell: the
#: only absolute paths a model knows are the file tools', and that shell reads `/inputs`
#: from the host's root.
ABSOLUTE = (
    "- Use absolute paths and avoid `cd` so the working directory stays stable; use the "
    "optional timeout to override the default."
)


#: deepagents' longest `timeout`, which `create_deep_agent` offers no way to lower.
_LONGEST = (
    inspect.signature(FilesystemMiddleware.__init__).parameters["max_execute_timeout"].default
)


def longest_command(execution_timeout_s: int) -> int:
    """The longest `timeout` a model may give `execute`.

    The setting, not deepagents' hour: `claim_stale_after` counts on no command outliving
    it, and one asked to run past the turn's deadline outlived the session's claim.
    """
    return min(execution_timeout_s, _LONGEST)


def timeout_line(longest: int) -> str:
    """Said on every shell: models passed milliseconds, 5000 to 40000, and each was refused."""
    return f"- `timeout` is in seconds, {longest:,} at most, which is also the default."


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


def missed(output: str, virtual: re.Pattern[str]) -> str | None:
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
        f'shell exports the session root, so there it is `"$KINGFISHER_SESSION_ROOT{path}"` '
        '-- in the command, and in a script as `os.environ["KINGFISHER_SESSION_ROOT"]`.'
    )


#: What a fence says of a write to a folder it keeps read-only. Each is the kernel's word,
#: not kingfisher's: `sandbox-exec` denies the operation, the Linux fence denies the
#: permission -- measured on the Linux job, in `tests/linux/test_fence_escapes.py` -- and
#: bubblewrap mounts the folder read-only, which no job runs, so that one follows `_MISSING`
#: rather than a measurement. Narrower than `_MISSING` on purpose: a file that is merely
#: absent is not a folder that is kept, and the note would send the model somewhere else.
_REFUSED = ("Operation not permitted", "Permission denied", "Read-only file system")

#: A path a message names: anything carrying a slash, up to the punctuation around it. The
#: slash is what tells a path from a word -- without it `grep: inputs: ...` and a command
#: that merely printed the word `inputs` are the same line.
_TOKEN = re.compile(r"[^\s'\"`:,;()]*/[^\s'\"`:,;()]*")


def _refused_paths(output: str, root: str) -> Iterator[PurePosixPath]:
    """Every path a fence's refusal names in `output`, where the shell ran it.

    The shell's working directory *is* the session, so a relative path is resolved
    against `root` rather than skipped -- `echo x > inputs/f` is the shorter command and
    the one a model writes.

    Both on one line, as `missed` is, so a path a command printed and an unrelated refusal
    elsewhere in its output are not read as one.
    """
    here = PurePosixPath(root)
    for line in output.splitlines():
        if not any(said in line for said in _REFUSED):
            continue
        for token in _TOKEN.findall(line):
            named = PurePosixPath(token)
            yield named if named.is_absolute() else here / named


def kept_under(output: str, root: str, folders: Collection[str]) -> str | None:
    """The first folder of `folders` a fence refused a write under in `output`, or `None`.

    Against `root` and not the folder name alone, because a fence refuses everything
    outside the session too: a `python3` whose cache file sits in the host's temp folder
    reports `Operation not permitted` about a path ending in a name of its own, and
    blaming a session folder there would send the model to correct a path that was never
    the problem.
    """
    here = PurePosixPath(root)
    kept = [(folder, here / folder) for folder in folders]
    for where in _refused_paths(output, root):
        for folder, under in kept:
            if where.is_relative_to(under):
                return folder
    return None


def strayed_under(output: str, root: str, folders: Collection[str]) -> str | None:
    """The first path a fence refused in `output` that no folder of the session holds, as
    the file tools spell it, or `None`.

    The session's top is the likeliest refusal of all, being where the shell starts, and
    `kept_under` cannot answer it: no folder is named, so there is nothing to look under.
    Only inside the session, because a refusal anywhere else is not a write that went to
    the wrong folder -- the host's own files are refused for being the host's.

    A path with no slash in it is no part of this: `/bin/sh: out.csv: Operation not
    permitted` names the file as the shell wrote it, and reading a bare word as a path
    would read `cat: shadow: Permission denied` -- a refused *read* -- as a write that
    wants somewhere else to go. The section the shell says of itself carries that case.
    """
    here = PurePosixPath(root)
    for where in _refused_paths(output, root):
        if not where.is_relative_to(here):
            continue
        # The root itself is somewhere to write *in*, not a path a write lands at.
        if (under := where.relative_to(here)).parts and strays(f"/{under}", frozenset(folders)):
            return f"/{under}"
    return None


def _not_yours(what: str, layout: Layout) -> str:
    """The one wording for a write the fence refused, `what` being what it refused."""
    return (
        f"{what} is not yours to write: the shell is refused there, as the file "
        f"tools are. {what_you_make_goes(layout.outputs_route, layout.scratch_route)}"
    )


def kept_note(folder: str, layout: Layout) -> str:
    """The note appended to a shell result a fence refused for a kept folder.

    The folder as the file tools spell it, which is the name the prompt gave it: what the
    shell reported is a host path, and the point of the note is that the two are one place.
    """
    return _not_yours(f"`/{folder}/`", layout)


def stray_note(where: str, layout: Layout) -> str:
    """The note for a refused path no folder of the session holds, `where` as the file
    tools spell it.

    The same sentence a kept folder gets, because it is the same mistake one step out:
    a file tool refuses a save there too, which `stray_save` says in its own words.
    """
    return _not_yours(f"`{where}`", layout)


def _found_nothing(tool: str, said: str) -> bool:
    """Whether `read_file`, `glob` or `grep` said it found nothing, as deepagents words it.

    `read_file` only as an error: a result file's own text may say "not found".
    """
    if tool == READ_FILE:
        return said.startswith("Error") and "not found" in said
    return "No files found" in said or "No matches found" in said


def expired_note(rounds: int) -> str:
    """Appended to a search of the saved results that found nothing."""
    return (
        f"A saved result older than {rounds} rounds of this conversation has expired and "
        "its file is gone; to see it again, call the tool that made it again."
    )


def _described(tool: Any, rule: str | None, timed: str) -> Any:
    if isinstance(tool, BaseTool) and tool.name == EXECUTE:
        said = _timed(_ruled(tool.description, rule), timed)
        return tool.model_copy(update={"description": said})
    if isinstance(tool, dict) and tool.get("name") == EXECUTE:
        return {**tool, "description": _timed(_ruled(tool.get("description") or "", rule), timed)}
    return tool


def _timed(description: str, timed: str) -> str:
    return description if timed in description else f"{description}\n{timed}"


def _overlong(request: Any, longest: int) -> ToolMessage | None:
    """The refusal of an `execute` call asking for longer than `longest`, as deepagents'
    own would read; `None` for any other call."""
    call = request.tool_call
    if call.get("name") != EXECUTE:
        return None
    asked = (call.get("args") or {}).get("timeout")
    if asked is None:
        return None
    try:
        seconds = float(asked)
    except (TypeError, ValueError):
        # Left to the tool's own schema, which refuses it.
        return None
    if seconds <= longest:
        return None
    return ToolMessage(
        content=f"Error: timeout {asked}s exceeds maximum allowed ({longest}s).",
        name=EXECUTE,
        tool_call_id=call["id"],
        status="error",
    )


def _unreadable(request: Any) -> ToolMessage | None:
    """The refusal of a `read_file` call on a file no model here takes as it is; `None` for
    any other call.

    Audio and video become one JSON string of base64 before any middleware sees the
    result, so only refusing the call stops them; a `.pptx` is counted by the summarizer
    whichever model then drops it.
    """
    call = request.tool_call
    if call.get("name") != READ_FILE:
        return None
    path = (call.get("args") or {}).get("file_path")
    if not isinstance(path, str):
        return None
    kind = _get_backend_read_file_type(path)
    suffix = PurePosixPath(path).suffix.lower()
    if kind in ("audio", "video"):
        what, reads = kind, kind
    elif kind == "file" and suffix != ".pdf":
        what, reads = f"a {suffix} file", f"{suffix} files"
    else:
        return None
    return failed(
        call,
        f"Error: {path} is {what}, which can't be handed to the model here. If you hold a "
        f"tool that can read {reads}, call it on {path}; otherwise say that this part "
        "could not be done.",
    )


#: Where each file tool takes the path it acts on.
_PATH_ARGUMENT = {
    READ_FILE: "file_path", WRITE_FILE: "file_path", EDIT_FILE: "file_path",
    DELETE: "file_path", LS: "path", GLOB: "path", GREP: "path",
}


def _miscased(request: Any, folders: frozenset[str], layout: Layout) -> ToolMessage | None:
    """The refusal of a file tool's path that spells a folder of the session in other
    capitals; `None` for any other call."""
    call = request.tool_call
    argument = _PATH_ARGUMENT.get(call.get("name"))
    path = (call.get("args") or {}).get(argument) if argument else None
    if not isinstance(path, str):
        return None
    try:
        where = validate_path(path)
    except ValueError:
        return None
    meant = miscased(where, layout, folders)
    return None if meant is None else failed(call, f"Error: {miscased_path(where, meant)}")


def _stray(request: Any, folders: frozenset[str], layout: Layout) -> ToolMessage | None:
    """The refusal of a `write_file` or `edit_file` outside every folder of the session;
    `None` for any other call."""
    call = request.tool_call
    if call.get("name") not in (WRITE_FILE, EDIT_FILE):
        return None
    path = (call.get("args") or {}).get("file_path")
    if not isinstance(path, str):
        return None
    try:
        where = validate_path(path)
    except ValueError:
        # Left to the tool, which says what is wrong with the path.
        return None
    if not strays(where, folders):
        return None
    return failed(call, f"Error: {stray_save(where, layout)}")


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

#: deepagents' sentence on where a long result is kept, naming a folder only its answer to
#: a refusal for length writes, with a stub naming the path: `standing_middleware` turns
#: its offloading off, so every other result is under this layout's own folder. Left to
#: say nothing rather than reworded, because the section above already tells the model
#: where results are and says it once.
OFFLOADED_ELSEWHERE = (
    "Offloaded large tool results live under the artifacts root (`/large_tool_results/` by "
    "default); grep that directory to search them when you do not know the exact path."
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
        said = _replaced(tool.description, SEPARATELY, JOINED, JOINED)
        return tool.model_copy(
            update={"description": said.replace(OFFLOADED_ELSEWHERE, "").rstrip()}
        )
    if tool.name != READ_FILE or not hasattr(tool.args_schema, "model_json_schema"):
        return tool
    # Only what the model is shown: the call is run by deepagents' own tool, whose
    # default `_limited` fills in.
    described = _replaced(tool.description, PAGE, WHOLE, f"By default it {WHOLE}")
    return tool.model_copy(
        update={"description": described, "args_schema": _shown(tool.args_schema)}
    )


#: deepagents' advice on searching to the shell's holder, by whether it holds `grep` and
#: `glob`. deepagents picks one from the tools it sees, which is every built-in: it runs
#: outside kingfisher's middleware.
_SEARCH = {
    (True, True): "You MUST avoid using search commands like find and grep. Instead use the "
    "grep, glob tools to search. ",
    (True, False): "You MUST avoid using shell grep for searches. Instead use the grep tool to "
    "search text. ",
    (False, True): "You MUST avoid using shell find for searches. Instead use the glob tool to "
    "find files. ",
    (False, False): "",
}
_CAT = "Use read_file rather than cat/head/tail."
_FIND = "\n    - execute(command=\"find . -name '*.py'\")  # Use glob tool instead"
_GREPPED = "\n    - execute(command=\"grep -r 'pattern' .\")  # Use grep tool instead"


def _searching(held: Collection[str | None]) -> str:
    """`execute`'s line on searching and reading, naming only the tools `held`."""
    grep, glob = GREP in held, GLOB in held
    said = (_SEARCH[grep, glob] + (_CAT if READ_FILE in held else "")).rstrip()
    if not said:
        return ""
    return f"- {said}{_FIND if glob else ''}{_GREPPED if grep else ''}"


#: deepagents' line in `execute`'s description, with its examples, as said to a holder of
#: `grep`, `glob` and `read_file`.
SEARCHING = _searching((GREP, GLOB, READ_FILE))


def _before(*names: str) -> str:
    """`ls`'s advice to list before using `names`, in deepagents' words."""
    if not names:
        return ""
    tools = "tools" if len(names) > 1 else "tool"
    return f"You should almost ALWAYS use this tool before using the {' or '.join(names)} {tools}."


#: deepagents' advice in `ls`'s description.
BEFORE = _before(READ_FILE, EDIT_FILE)

#: deepagents' pointer from `grep` to the shell.
REGEX = "- If you genuinely need regex, use the execute tool with `rg '<regex>'` instead."

#: deepagents' advice in `write_file`'s description.
PREFER_EDIT = (
    "- Prefer to edit existing files (with the edit_file tool) over creating new ones when "
    "possible."
)

#: deepagents' claim in `edit_file`'s description, untrue on every graph: its `edit_file`
#: checks only the path and permissions before it edits.
MUST_READ = "- You must read the file before editing; this tool errors otherwise."

#: What replaces it where `read_file` is held.
READ_FIRST = "- Read the file before editing, so `old_string` matches it exactly."

#: deepagents' advice on indentation, which speaks of `read_file`'s output and its header.
FROM_READ = (
    "- Preserve the exact source indentation from the read output, and never include the "
    "read status header in old_string or new_string."
)

#: What replaces it where `read_file` is not held: the indentation still has to match.
EXACT_INDENT = "- Preserve the file's exact indentation in old_string and new_string."


def _held_lines(held: Collection[str | None]) -> dict[str, tuple[tuple[str, str], ...]]:
    """Each built-in's deepagents lines naming another tool, and what replaces each where
    the tools `held` are: nothing, where it would name none of them."""
    reads = READ_FILE in held
    return {
        EXECUTE: ((SEARCHING, _searching(held)),),
        GREP: ((REGEX, REGEX if EXECUTE in held else ""),),
        LS: ((BEFORE, _before(*(name for name in (READ_FILE, EDIT_FILE) if name in held))),),
        WRITE_FILE: ((PREFER_EDIT, PREFER_EDIT if EDIT_FILE in held else ""),),
        EDIT_FILE: (
            (MUST_READ, READ_FIRST if reads else ""),
            (FROM_READ, FROM_READ if reads else EXACT_INDENT),
        ),
    }


def _naming_held(tool: Any, lines: dict[str, tuple[tuple[str, str], ...]]) -> Any:
    if not isinstance(tool, BaseTool) or tool.name not in lines:
        return tool
    said = tool.description
    # Unlike `_replaced`, nothing is appended where deepagents reworded a line: none of
    # these says what kingfisher changed, and a test fails on the line left.
    for theirs, ours in lines[tool.name]:
        said = said.replace(f"\n{theirs}", f"\n{ours}" if ours else "")
    return tool if said == tool.description else tool.model_copy(update={"description": said})


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
    """Put the shell's own line in `execute`'s description, on a shell exporting the
    session root say how a missed path is spelt there, and keep deepagents' host paths
    from a shell that is not on this host; have `read_file` read 1,000 lines unless told
    otherwise, and `grep` search each string of `a|b`, and say so; say nothing of the shell or of
    `write_todos` to a graph not holding it; have each built-in's description name only
    the tools the graph holds; and refuse a command asked to run longer than `longest`,
    a `read_file` of audio, video or slides, which no model here is handed as they are,
    a path spelling one of the session's folders in other capitals, and a save outside
    every folder of the session.

    The shell's line goes in the description, not the system prompt: a subagent gets none
    of `system.md`, and the description reaches every graph holding the shell. At
    deepagents' 100 lines, a third of every call a turn made was `read_file` paging, most
    naming a limit of 100 because the description did: so the description changes with
    the default, and a limit the model names is left as it is. `grep` is literal, and
    models wrote `a|b` anyway, nearly always finding nothing and searching again. The
    prose deepagents and langchain add of a tool arrives whether or not the graph holds
    it, and a model told of a tool calls it, each call spent on a refusal.
    """

    def __init__(  # noqa: PLR0913 -- one keyword per thing said of the shell and the tools
        self,
        layout: Layout,
        shell: ShellPrompt | None = None,
        *,
        longest: int,
        root: str | None = None,
        here: bool = True,
        expires_after: int | None = None,
        folders: frozenset[str] | None = None,
    ) -> None:
        super().__init__()
        self._longest = longest
        self._layout = layout
        self._folders = folders_of(layout) if folders is None else folders
        # The folder a ledger row names, as the file tools spell it, where results expire.
        self._expiring = (
            () if expires_after is None else (layout.tool_results_route.rstrip("/"),)
        )
        self._expired = "" if expires_after is None else expired_note(expires_after)
        self._timed = timeout_line(longest)
        # Whether the shell runs on this host; where not, deepagents' section names paths it
        # does not have.
        self._here = here
        self._rule = execute_line(shell, layout)
        self._virtual = _virtual(layout.session_dirs)
        # Where the shell sees the session, or `None` where it does not say -- which is
        # also where neither correction has anything to name, so it gates both.
        self._root = root

    def _told(self, request: Any) -> Any:
        # Inside `GrantedOnlyMiddleware`, so the request holds only what was granted.
        held = {tool_name(tool) for tool in request.tools}
        lines = _held_lines(held)
        tools = [
            _offered(_described(_naming_held(tool, lines), self._rule, self._timed))
            for tool in request.tools
        ]
        if not all(new is old for new, old in zip(tools, request.tools, strict=True)):
            request = request.override(tools=tools)
        untold = []
        if not (self._here and EXECUTE in held):
            untold.append(MAPPINGS)
        if WRITE_TODOS not in held:
            untold.append(TODOS)
        return _without(request, tuple(untold))

    def _corrected(self, request: Any, result: Any) -> Any:
        if self._root is None:
            return result
        if request.tool_call.get("name") != EXECUTE or not isinstance(result, ToolMessage):
            return result
        if not isinstance(result.content, str):
            return result
        # The spelling first: where the path is the file tools' and not the shell's,
        # saying which folder is kept would answer a command that was never going to run.
        if (path := missed(result.content, self._virtual)) is not None:
            said = hint(path)
        elif folder := kept_under(
            result.content, self._root, self._layout.shell_read_only
        ):
            said = kept_note(folder, self._layout)
        elif strayed := strayed_under(result.content, self._root, self._folders):
            said = stray_note(strayed, self._layout)
        else:
            return result
        return result.model_copy(update={"content": f"{result.content}\n\n{said}"})

    def _explained(self, request: Any, result: Any) -> Any:
        """`result`, saying results expire where a file tool looked for one and found nothing.

        Said on the miss, not by rewriting the handles that name a removed file: a handle
        keeps its wording once written, and an id the model remembers names no file at all.
        """
        call = request.tool_call
        if not self._expiring or call.get("name") not in (READ_FILE, GLOB, GREP):
            return result
        if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
            return result
        said = [str(value) for value in (call.get("args") or {}).values()]
        if not any(route in value for value in said for route in self._expiring):
            return result
        if not _found_nothing(call["name"], result.content):
            return result
        return result.model_copy(update={"content": f"{result.content}\n\n{self._expired}"})

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._told(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._told(request))

    def _refused(self, request: Any) -> ToolMessage | None:
        return (
            _overlong(request, self._longest)
            or _unreadable(request)
            or _miscased(request, self._folders, self._layout)
            or _stray(request, self._folders, self._layout)
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if (refused := self._refused(request)) is not None:
            return refused
        if (parts := _split(request)) is None:
            return self._explained(request, self._corrected(request, handler(_limited(request))))
        call = request.tool_call
        answers = [
            handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return self._explained(request, _joined(call, parts, answers))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if (refused := self._refused(request)) is not None:
            return refused
        if (parts := _split(request)) is None:
            result = await handler(_limited(request))
            return self._explained(request, self._corrected(request, result))
        call = request.tool_call
        answers = [
            await handler(request.override(tool_call=_parted(call, part, n)))
            for n, part in enumerate(parts)
        ]
        return self._explained(request, _joined(call, parts, answers))

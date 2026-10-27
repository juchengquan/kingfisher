"""Assembling the system prompt from the markdown that ships with the package.

Nothing here may import more than `Config`, the layout, `READ_FILE` and the standard library:
`ShellPrompt` is exported, and a module importing deepagents would load the provider
SDKs behind it for anyone naming it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING

from kingfisher.core.config import ConfigError
from kingfisher.core.definitions.skills import READ_FILE
from kingfisher.core.interfaces import Instructions
from kingfisher.core.layout import DEFAULT_LAYOUT, Layout

if TYPE_CHECKING:
    from collections.abc import Collection

    from kingfisher.core.config import Config

#: A `{{leaf}}` placeholder in the shipped markdown, filled by name rather than by
#: replacing default paths: `/scratchpad` is a prefix of `/scratchpad/tool_results`.
#: Anything between doubled braces, so `{{ inputs }}` is refused rather than reaching
#: the model as braces; one pattern for `_fill` and `ShellPrompt`'s check, so a blank
#: either is wrong about is one the other reports.
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")

#: The template every skills index is written from, which `SkillsMiddleware` fills: its
#: `{skills_locations}`, `{skills_load_warnings}` and `{skills_list}` are deepagents' to
#: fill, and only braces it fills may be single in what this hands it.
SKILLS_FILE = "skills.md"

#: The variable a shell reaching the catalogue exports it as.
SKILLS_VAR = "KINGFISHER_SKILLS"

#: What the session's folders are for: the one part of the harness every subagent
#: kingfisher builds is handed too, from this one file, so the two cannot drift apart.
SESSION_FILE = "session.md"

#: The file tools' section, naming those the agent holds.
FILE_TOOLS_FILE = "file_tools.md"

#: The names of deepagents' file tools and shell and of langchain's checklist, spelt
#: nowhere else but `READ_FILE`, which lives with the definition readers that refuse by it.
LS = "ls"
WRITE_FILE = "write_file"
EDIT_FILE = "edit_file"
DELETE = "delete"
GLOB = "glob"
GREP = "grep"
EXECUTE = "execute"
WRITE_TODOS = "write_todos"

#: deepagents' file tools, in the order their section names them.
FILE_TOOLS = (LS, READ_FILE, WRITE_FILE, EDIT_FILE, DELETE, GLOB, GREP)

#: The tools anything in the prompt is said of. Nothing else in a grant changes the prompt,
#: so nothing else goes in `render_system_prompt`'s cache key.
_DESCRIBED = frozenset((*FILE_TOOLS, EXECUTE))

#: Where a deployment that wants results cited says the model may. Permission, not an
#: instruction: told to, the model hangs an id on every claim whether the reader wants one.
_CITING = "You can cite it by that id when you rely on that result."

#: What the agent's results are recorded as, and how one is seen again.
TOOL_RESULTS_FILE = "tool_results.md"

#: A sentence for each way to read a saved result back, and every tool it needs: `glob`
#: finds a file only `read_file` opens.
_READING = (
    (
        (READ_FILE,),
        "Read one back with `read_file`, a range of lines at a time, rather than calling "
        "the tool again.",
    ),
    (
        (GREP,),
        "Search them with `grep`, a result too long to show you whole included: every "
        "one is in the same folder, named after its id.",
    ),
    (
        (GLOB, READ_FILE),
        "To find an older result by its id, `glob` for `A-0042-*` in "
        "`/{{scratchpad}}/{{tool_results}}/`.",
    ),
)
_UNREAD = "None of your tools reads these files: to see a result again, call its tool again."
#: For one holding the shell, which may read them.
_AGAIN = "To see a result again, call its tool again."

#: How a cleared result is seen again, by whether `read_file` is held: a handle names its
#: file to that tool alone, and tells any other agent to call the tool again.
_RECOVERING = {
    True: "Read that file or call the tool again; a file may have changed since.",
    False: "To see a result whole again, call its tool again.",
}

#: The file tools memory is saved with, `edit_file` first: it changes the file in place,
#: where `write_file` replaces it whole. Not `execute`, which may run where `/memory` is not.
_SAVERS = (EDIT_FILE, WRITE_FILE)

#: What is said of a shell that says nothing, which is only what holds wherever a
#: shell runs. No `execute` line: deepagents' own stands.
_ELSEWHERE = ("shell_elsewhere.md", "shell_elsewhere_skills.md")

#: What a shell exporting the session root adds to its section and to `execute`'s line.
_ROOTED = ("shell_rooted.md", "shell_rooted_execute.md")

#: The variable a shell that knows where it sees the session folder exports it as.
SESSION_ROOT = "KINGFISHER_SESSION_ROOT"

#: A deployment's standing instructions for every agent and subagent but a compiled one,
#: written by people; `/memory/AGENTS.md` is the agent's own.
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"

#: The template `MemoryMiddleware` puts the memory file into, and the whole of what the agent
#: is told about memory: kingfisher's own, never re-synced with deepagents', whose says
#: memory outlives its session. Sent as written: deepagents strips HTML comments from the
#: memory file, not from this.
MEMORY = "memory.md"

#: What the memory block says of saving, by whether the agent holds a tool to save with.
#: The rule is worded as what saving does, since that is what makes it kingfisher's to say.
_SAVING = {
    True: (
        "Save to it with `{saver}`, one entry per line, written `- ` and the text; "
        "kingfisher numbers each as `- [m1]`, and an entry is changed or removed by its "
        "line. What is saved is sent on every later call and handed back with the turn's "
        "files, so never save credentials or details that identify the user -- their name, "
        "email address, team or account ids -- even when they offer them."
    ),
    False: "It can be read here but not added to, so do not try to save to it.",
}

#: How long the memory block says memory lasts, by whether a `MemoryStore` keeps it for
#: the user. Said only where true: a promise of later conversations with no store had the
#: agent writing for a reader who never arrives.
_KEPT = {
    False: (
        "Like `/{outputs}`, it is kept for later turns of this conversation and reported "
        "back when the turn ends."
    ),
    True: (
        "It is this user's memory, kept for their later conversations as well as later "
        "turns of this one, and reported back when the turn ends."
    ),
}


def _prompt_text(name: str) -> str:
    prompts = resources.files("kingfisher.core.agent.prompts")
    return prompts.joinpath(name).read_text(encoding="utf-8")


def _names(layout: Layout) -> dict[str, str]:
    """Each `{{leaf}}` a prompt may name, and this layout's name for it."""
    return {
        "inputs": layout.inputs,
        "outputs": layout.outputs,
        "memory": layout.memory,
        "scratchpad": layout.scratchpad,
        "tool_results": layout.tool_results,
    }


_LEAVES = frozenset(_names(DEFAULT_LAYOUT))


def _fill(text: str, layout: Layout, source: str, **more: str) -> str:
    """`text`, with every `{{leaf}}` replaced by the layout's name for it, and every
    blank in `more` by its value."""
    names = {**_names(layout), **more}

    def _one(match: re.Match[str]) -> str:
        leaf = match.group(1)
        if leaf not in names:  # pragma: no cover -- guards a bad edit to the markdown
            msg = f"{source} names an unknown layout leaf {{{{{leaf}}}}}; expected one of {names}"
            raise ValueError(msg)
        return names[leaf]

    return _PLACEHOLDER.sub(_one, text)


def _filled(name: str, layout: Layout, **more: str) -> str:
    """One shipped prompt, with every `{{leaf}}` replaced by the layout's name for it."""
    return _fill(_prompt_text(name), layout, name, **more)


@dataclass(frozen=True)
class ShellPrompt:
    """What the agent is told about the shell that runs `execute`, said by that shell.

    `prompt` is the agent's section on the shell. `skills` follows the skills section,
    so it is shown only where skills are on. `execute` replaces deepagents' "Use
    absolute paths" line in the tool's description, which is all a subagent is told.

    A part left `None` is said the neutral way -- for `execute`, deepagents' own line
    stays -- and `""` says nothing. Each may name a layout leaf as `{{inputs}}`,
    `{{outputs}}`, `{{memory}}`, `{{scratchpad}}` or `{{tool_results}}`.
    """

    prompt: str | None = None
    skills: str | None = None
    execute: str | None = None

    def __post_init__(self) -> None:
        # At construction, not at render on the first turn: a typo is heard at startup.
        for part in ("prompt", "skills", "execute"):
            text = getattr(self, part)
            unknown = sorted({m.group(1) for m in _PLACEHOLDER.finditer(text or "")} - _LEAVES)
            if unknown:
                named = ", ".join(f"{{{{{leaf}}}}}" for leaf in unknown)
                known = ", ".join(f"{{{{{leaf}}}}}" for leaf in sorted(_LEAVES))
                msg = (
                    f"ShellPrompt {part} names {named}, which no layout fills; it may "
                    f"name {known}"
                )
                raise ValueError(msg)


@lru_cache(maxsize=1)
def _neutral() -> ShellPrompt:
    prompt, skills = (_prompt_text(name).strip() for name in _ELSEWHERE)
    return ShellPrompt(prompt=prompt, skills=skills)


@lru_cache(maxsize=8)
def rooted(shell: ShellPrompt | None) -> ShellPrompt:
    """`shell`, its section and its `execute` line also telling the agent of
    `$KINGFISHER_SESSION_ROOT`, for a shell that exports it.

    The `execute` line, because it is all a subagent is told; where `shell` left it
    `None`, the sentence takes the place of deepagents' "Use absolute paths" line.
    """
    said = shell or ShellPrompt()
    prompt, execute = (_prompt_text(name).strip() for name in _ROOTED)
    return replace(
        said,
        prompt=_adding(prompt, said.prompt if said.prompt is not None else _neutral().prompt),
        execute=_adding(execute, said.execute),
    )


def _adding(sentence: str, text: str | None) -> str:
    """`text`, followed by `sentence`, unless `text` is `""` or already names the variable."""
    # `""` is how a deployment says nothing; a part naming the variable has said it, as
    # kingfisher's own shell does, and would be told it twice.
    if text is None:
        return sentence
    if not text or SESSION_ROOT in text:
        return text
    return f"{text}\n\n{sentence}"


def _said(shell: ShellPrompt | None, part: str, layout: Layout) -> str:
    """One part of what `shell` says of itself, or the neutral wording where it said none."""
    said = getattr(shell, part, None)
    text = getattr(_neutral(), part) if said is None else said
    return _fill(text or "", layout, f"ShellPrompt {part}")


def execute_line(shell: ShellPrompt | None, layout: Layout) -> str | None:
    """The line that replaces deepagents' advice in `execute`'s description, or `None`
    to leave that advice as it is.
    """
    said = None if shell is None else shell.execute
    return None if said is None else _fill(said, layout, "ShellPrompt execute")


def _held(tools: Collection[str] | None) -> tuple[str, ...]:
    """The file tools in `tools`, `None` being every one, in the order their section
    names them."""
    return tuple(name for name in FILE_TOOLS if tools is None or name in tools)


def _saver(held: tuple[str, ...]) -> str | None:
    """The file tool memory is saved with, or `None` where `held` has neither."""
    return next((name for name in _SAVERS if name in held), None)


def memory_guidelines(
    tools: Collection[str] | None, layout: Layout, *, users: bool = False
) -> str:
    """The template `MemoryMiddleware` puts the memory file into, `{agent_memory}` and all,
    for an agent granted `tools`, `None` being everything. `users` where the memory is the
    user's, kept by a `MemoryStore`, rather than this session's."""
    held = _held(tools)
    evidence = (
        "evidence from `read_file` and other tools"
        if READ_FILE in held
        else "evidence from your tools"
    )
    saver = _saver(held)
    saving = _SAVING[saver is not None].format(saver=saver)
    kept = _KEPT[users].format(outputs=layout.outputs)
    return _filled(MEMORY, layout, kept=kept, saving=saving, evidence=evidence)


def memory_writer_prompt(layout: Layout, *, entries: int, characters: int) -> str:
    """What the memory writer is told, with the limits its answer is held to."""
    return _filled(
        "memory_writer.md", layout, entries=str(entries), characters=f"{characters:,}"
    )


def _file_tools(held: tuple[str, ...], layout: Layout) -> str:
    """The file tools' section, naming the ones in `held`, or nothing for none."""
    if not held:
        return ""
    named = ", ".join(f"`{name}`" for name in held)
    return _filled(FILE_TOOLS_FILE, layout, file_tools=named).strip()


def _expiring(rounds: int | None) -> str:
    """Said where saved results expire, since the rest promises every one is kept."""
    if rounds is None:
        return ""
    return (
        f"A result older than {rounds} rounds of this conversation expires: its file is "
        "removed, so to see it again, call its tool again."
    )


def _tool_results(
    held: tuple[str, ...], layout: Layout, *, shelled: bool, rounds: int | None, cites: bool
) -> str:
    """What the agent's results are recorded as, and how one is seen again, naming only the
    tools in `held`."""
    told = [said for needs, said in _READING if all(name in held for name in needs)]
    unread = _AGAIN if shelled else _UNREAD
    return _filled(
        TOOL_RESULTS_FILE,
        layout,
        citing=_CITING if cites else "",
        reading=_fill("\n".join(told) or unread, layout, TOOL_RESULTS_FILE),
        expiring=_expiring(rounds),
        recovering=_RECOVERING[READ_FILE in held],
    ).strip()


#: Enough for a fleet: `ahead` carries the running agent's line, so there is a key per
#: agent a process serves. `layout` and `shell` are in the key because two layouts or
#: two shells must not be handed one prompt.
@lru_cache(maxsize=32)
def render_system_prompt(  # noqa: PLR0913 -- one keyword per thing the prompt describes
    *,
    cite_results: bool = True,
    keep_results_rounds: int | None = None,
    ahead: str = "",
    layout: Layout,
    shell: ShellPrompt | None = None,
    tools: frozenset[str] | None = None,
) -> str:
    """The prompt: `ahead`, the client's own text, then the harness for the capabilities
    that are actually wired.

    `shell` is what the shell running `execute` says of itself, and `None` is one that
    says nothing. `tools` is what the agent was granted, and `None` is everything: a
    tool it does not hold is not described, since a model told of one calls it.
    """
    held = _held(tools)
    shelled = tools is None or EXECUTE in tools
    harness = "\n\n".join(
        part
        for part in (
            session_section(layout),
            _file_tools(held, layout),
            _said(shell, "prompt", layout) if shelled else "",
            _tool_results(
                held, layout, shelled=shelled, rounds=keep_results_rounds, cites=cite_results
            ),
        )
        if part
    )
    # A blank left empty leaves a run of blank lines behind.
    return re.sub(r"\n{3,}", "\n\n", _in_order(ahead, harness)).strip() + "\n"


def _in_order(*parts: str) -> str:
    """Prompt parts in the order given, separated the one way this codebase separates them,
    an empty one left out."""
    return "\n\n---\n\n".join(part.strip() for part in parts if part.strip())


class InstructionsFile(Instructions):
    """Kingfisher's own `Instructions`: `INSTRUCTIONS.md` in the definitions folder.

    With what people write rather than in the workspace sessions make, so the macOS
    fence keeps the shell from editing it.
    """

    def text(self, cfg: Config) -> str:
        path = cfg.authored_files[INSTRUCTIONS_FILE]
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def refuse_a_workspace_prompt(cfg: Config) -> None:
    """Refuse a `PROMPT.md` left in the workspace, which nothing reads any more.

    Ignored, its house rules would stop reaching every agent with nothing said.
    """
    stale = cfg.retired_prompt
    if stale.is_file():
        msg = (
            f"{stale} is read by nothing now: move what it says to "
            f"{cfg.authored_files[INSTRUCTIONS_FILE]}, beside the definitions, and delete it"
        )
        raise ConfigError(msg)


def skills_index(layout: Layout, shell: ShellPrompt | None, *, shelled: bool) -> str:
    """The template a graph's skills index is written from: what the catalogue is, and
    where the graph holds the shell, what that shell says of reaching it."""
    said = _said(shell, "skills", layout) if shelled else ""
    # deepagents fills the template with `str.format`, so a brace the shell wrote is doubled.
    told = said.replace("{", "{{").replace("}", "}}")
    return _filled(SKILLS_FILE, layout, shell=f"\n\n{told}" if told else "")


def session_section(layout: Layout) -> str:
    """What the session's folders are for, in this layout's names."""
    return _filled(SESSION_FILE, layout).strip()


def what_you_make_goes(outputs_route: str, scratch_route: str) -> str:
    """Where the agent's own files go, worded once for every refusal that offers it.

    Three refuse a write and offer this: a deny rule's, a fence's -- which `builtin_tools`
    reads out of the shell's own output -- and a save outside the session's folders. A
    model reading three wordings for one rule reads three rules. Each folder with what it
    is for, because choosing between them is the whole use of the sentence: `/outputs` is
    handed back and `/scratchpad` is not.

    Here rather than beside the first of the three: this module already holds the prose a
    model reads on a misstep, and it is the one both others may import -- it takes nothing
    but `Config`, the layout and `READ_FILE`, which is why nothing importing it loads the
    provider SDKs.
    """
    return (
        f"Save what you make under `{outputs_route}<name>` to hand it back, or under "
        f"`{scratch_route}<name>` to work on it."
    )


def stray_save(where: str, layout: Layout) -> str:
    """Why a save at `where` is refused, naming where it belongs in this layout."""
    return (
        f"{where} is outside the session's folders, so a file saved there is neither kept "
        f"nor handed back. {what_you_make_goes(layout.outputs_route, layout.scratch_route)}"
    )


def miscased_path(where: str, meant: str) -> str:
    """Why `where` is refused for spelling a folder of the session in other capitals."""
    return (
        f"{where} spells a folder of this session in other capitals, and a path here is "
        f"matched exactly as written: it is {meant}."
    )


def subagent_prompt(prompt: str, instructions: str, layout: Layout) -> str:
    """A subagent's prompt, in the agent's order: its own, the deployment's instructions,
    then the session's folders.

    Left to guess, a subagent saves to `/outputs` whatever the layout calls it, and nothing
    reports what lands there. The rest of the harness stays the agent's: `decisions.md` says why.
    """
    return _in_order(prompt, instructions, session_section(layout))


def system_prompt(
    cfg: Config | None = None,
    agent: str = "",
    shell: ShellPrompt | None = None,
    instructions: str = "",
    tools: Collection[str] | None = None,
) -> str:
    """The assembled prompt: the agent's own line, then the deployment's instructions, then
    the harness.

    `shell` is what the backend's shell says of itself, which only the backend knows.
    `tools` is the agent's allowlist, `None` where nothing narrows it.
    """
    return render_system_prompt(
        cite_results=cfg.cite_results if cfg else True,
        keep_results_rounds=cfg.keep_results_rounds if cfg else None,
        ahead=_in_order(agent, instructions),
        layout=cfg.layout if cfg else DEFAULT_LAYOUT,
        shell=shell,
        tools=None if tools is None else _DESCRIBED.intersection(tools),
    )


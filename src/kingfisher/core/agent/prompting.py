"""Assembling the system prompt from the markdown that ships with the package.

Nothing here may import more than `Config`, the layout and the standard library:
`ShellPrompt` is exported, and a module importing deepagents would load the provider
SDKs behind it for anyone naming it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING

from kingfisher.core.config import ConfigError
from kingfisher.core.layout import DEFAULT_LAYOUT, Layout

if TYPE_CHECKING:
    from kingfisher.core.config import Config

BASE_PROMPT = "system.md"

#: A `{{leaf}}` placeholder in the shipped markdown, filled by name rather than by
#: replacing default paths: `/scratchpad` is a prefix of `/scratchpad/tool_results`.
#: Anything between doubled braces, so `{{ inputs }}` is refused rather than reaching
#: the model as braces; one pattern for `_fill` and `ShellPrompt`'s check, so a blank
#: either is wrong about is one the other reports.
_PLACEHOLDER = re.compile(r"\{\{([^{}]*)\}\}")

#: Where optional capability sections are spliced in. An HTML comment, so
#: `system.md` stays a plain Markdown document that renders and edits normally.
CAPABILITY_MARKER = "<!-- capabilities -->"

CAPABILITY_FILES = {
    "skills": "capability_skills.md",
    "memory": "capability_memory.md",
}

#: Where the shell's own section is spliced in. The rest of `system.md` says nothing
#: about the shell, because what is true of one depends on what runs it.
SHELL_MARKER = "<!-- shell -->"

#: What is said of a shell that says nothing, which is only what holds wherever a
#: shell runs. No `execute` line: deepagents' own stands.
_ELSEWHERE = ("shell_elsewhere.md", "shell_elsewhere_skills.md")

#: A deployment's standing instructions for every agent and subagent, written by
#: people; `/memory/AGENTS.md` is the agent's own.
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"

#: deepagents' `MEMORY_SYSTEM_PROMPT` with its lifetime claims corrected, so not to be
#: re-synced on an upgrade: `/memory` is deleted with its session. Sent as written:
#: deepagents strips HTML comments from the memory file, not from this.
MEMORY_GUIDELINES = "memory_guidelines.md"


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


def _fill(text: str, layout: Layout, source: str) -> str:
    """`text`, with every `{{leaf}}` replaced by the layout's name for it."""
    names = _names(layout)

    def _one(match: re.Match[str]) -> str:
        leaf = match.group(1)
        if leaf not in names:  # pragma: no cover -- guards a bad edit to the markdown
            msg = f"{source} names an unknown layout leaf {{{{{leaf}}}}}; expected one of {names}"
            raise ValueError(msg)
        return names[leaf]

    return _PLACEHOLDER.sub(_one, text)


def _filled(name: str, layout: Layout) -> str:
    """One shipped prompt, with every `{{leaf}}` replaced by the layout's name for it."""
    return _fill(_prompt_text(name), layout, name)


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
    #: Whether the shell starts in the session folder, so a file-tool path less its
    #: leading slash reaches the file; only then is a missed command told that spelling.
    starts_in_session: bool = False

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


def _said(shell: ShellPrompt | None, part: str, layout: Layout) -> str:
    """One part of what `shell` says of itself, or the neutral wording where it said none."""
    said = getattr(shell, part, None)
    text = getattr(_neutral(), part) if said is None else said
    return _fill(text or "", layout, f"ShellPrompt {part}")


def execute_line(shell: ShellPrompt | None, layout: Layout = DEFAULT_LAYOUT) -> str | None:
    """The line that replaces deepagents' advice in `execute`'s description, or `None`
    to leave that advice as it is.
    """
    said = None if shell is None else shell.execute
    return None if said is None else _fill(said, layout, "ShellPrompt execute")


def memory_guidelines() -> str:
    """The template `MemoryMiddleware` puts the memory file into, `{agent_memory}` and all."""
    return _prompt_text(MEMORY_GUIDELINES)


#: Enough for a fleet: `extra` carries the running agent's line, so there is a key per
#: agent a process serves. `layout` and `shell` are in the key because two layouts or
#: two shells must not be handed one prompt.
@lru_cache(maxsize=32)
def render_system_prompt(
    *,
    skills_enabled: bool = False,
    memory_enabled: bool = False,
    extra: str = "",
    layout: Layout = DEFAULT_LAYOUT,
    shell: ShellPrompt | None = None,
) -> str:
    """Assemble the prompt for the capabilities that are actually wired.

    `shell` is what the shell running `execute` says of itself, and `None` is one that
    says nothing.
    """
    base = _filled(BASE_PROMPT, layout)
    for marker in (CAPABILITY_MARKER, SHELL_MARKER):
        if marker not in base:  # pragma: no cover -- guards a silent edit
            msg = f"{BASE_PROMPT} is missing the {marker} marker"
            raise ValueError(msg)

    parts: list[str] = []
    if skills_enabled:
        parts.append(_filled(CAPABILITY_FILES["skills"], layout).strip())
        parts.append(_said(shell, "skills", layout))
    if memory_enabled:
        parts.append(_filled(CAPABILITY_FILES["memory"], layout).strip())
    sections = "\n\n".join(part for part in parts if part)

    assembled = _joined(
        base.replace(SHELL_MARKER, _said(shell, "prompt", layout)).replace(
            CAPABILITY_MARKER, sections
        ),
        extra,
    )
    # Removing a marker leaves a run of blank lines behind.
    return re.sub(r"\n{3,}", "\n\n", assembled).strip() + "\n"


def _joined(prompt: str, extra: str) -> str:
    """Two prompt parts, separated the one way this codebase separates them."""
    if not extra.strip():
        return prompt
    return f"{prompt.strip()}\n\n---\n\n{extra.strip()}"


class InstructionsFile:
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
    stale = cfg.workspace / "PROMPT.md"
    if stale.is_file():
        msg = (
            f"{stale} is read by nothing now: move what it says to "
            f"{cfg.authored_files[INSTRUCTIONS_FILE]}, beside the definitions, and delete it"
        )
        raise ConfigError(msg)


def behind_instructions(prompt: str, instructions: str) -> str:
    """`prompt`, behind the deployment's instructions if it has any.

    Empty instructions leave `prompt` exactly as it was: `_joined` collapses an empty
    *trailing* part, and this one leads.
    """
    return _joined(instructions, prompt) if instructions.strip() else prompt


def system_prompt(
    cfg: Config | None = None,
    agent: str = "",
    shell: ShellPrompt | None = None,
    instructions: str = "",
) -> str:
    """The assembled prompt: the harness, then the deployment's instructions, then the
    agent's own line.

    `shell` is what the backend's shell says of itself, which only the backend knows.
    """
    return render_system_prompt(
        skills_enabled=bool(cfg and cfg.skills_enabled),
        memory_enabled=bool(cfg and cfg.memory_enabled),
        extra=behind_instructions(agent, instructions),
        layout=cfg.layout if cfg else DEFAULT_LAYOUT,
        shell=shell,
    )

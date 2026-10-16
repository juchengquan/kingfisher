"""Assembling the system prompt from the markdown that ships with the package.

Split out of `assembly.py`, and not for tidiness. Nothing here needs more than `Config`
and the standard library, and reaching any of it -- `ShellPrompt`, which `kingfisher`
exports, or `system_prompt`, which `assembly.py` and the prompt's tests reach -- used to
cost **764 ms and 3,107 modules**, because touching any name in `assembly.py` runs that
module's `from deepagents import ...`, and Python cannot import one name from a module
without executing all of it. Measured after the split: about 10 ms and 113 modules.

Almost none of that was deepagents, which is 21 ms of its own code. It was openai (281
ms), anthropic (239 ms) and google (134 ms) -- the last one for a provider kingfisher
has no path to at all. Their type modules are pydantic classes, and defining a model
compiles a validator at import time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
from typing import TYPE_CHECKING, Protocol

from kingfisher.config import ConfigError
from kingfisher.layout import DEFAULT_LAYOUT, Layout

if TYPE_CHECKING:
    from kingfisher.config import Config

BASE_PROMPT = "system.md"

#: `kingfisher.layout` and nothing heavier: the module stays clear of deepagents and the
#: provider SDKs its docstring is about, and `Layout` carries only strings.

#: A `{{leaf}}` placeholder in the shipped markdown, filled with the layout's name for
#: that leaf. The prompt is prose with paths woven through it, so a renamed layout is
#: spliced in by name here rather than by replacing one default path string with
#: another -- `/scratchpad` is a prefix of `/scratchpad/tool_results`, and a blind
#: replace would reach the wrong one. The fixed sub-leaves (`large_tool_results`,
#: `conversation_history`) stay literal: only their parent scratchpad moves.
#: Anything between a doubled brace, not just a name that could be a leaf. `(\w+)`
#: saw neither a space, a hyphen nor a dot, so `{{ inputs }}`, `{{tool-results}}` and
#: `{{inputs.dir}}` were refused by nothing *and* filled by nothing: they reached the
#: model as braces, in a prompt a deployment had customised and been told nothing
#: about. One pattern for both readers on purpose -- the refusal below and `_fill`
#: above -- so a blank either is wrong about is a blank the other reports.
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

#: What kingfisher's own shell says of itself: its section, its paragraph under skills,
#: and its line in `execute`'s description.
_HERE = ("shell_here.md", "shell_here_skills.md", "shell_here_execute.md")
#: What is said of a shell that says nothing, which is only what holds wherever a
#: shell runs. No `execute` line: deepagents' own stands.
_ELSEWHERE = ("shell_elsewhere.md", "shell_elsewhere_skills.md")

#: A deployment's standing instructions for every agent and subagent, beside its
#: definitions. Distinct from `/memory/AGENTS.md`, which the agent writes: this is
#: what people write.
INSTRUCTIONS_FILE = "INSTRUCTIONS.md"

#: deepagents' `MEMORY_SYSTEM_PROMPT` with its lifetime claims corrected, and not to
#: be re-synced from it on an upgrade: the library's own says memory lasts into
#: "future conversations", and `/memory` is deleted with its session. Sent as
#: written: deepagents strips HTML comments from the memory file, not from this.
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
    """`text`, with every `{{leaf}}` replaced by the layout's name for it.

    An unknown placeholder is an edit naming a leaf the layout does not have, caught
    here rather than reaching the model as `{{typo}}`.
    """
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

    Three parts, because three readers see different ones. `prompt` is the agent's
    section on the shell. `skills` follows the skills section, so it is shown only
    where skills are on. `execute` replaces deepagents' "Use absolute paths" line in
    the tool's description, which is all a subagent is told about the shell.

    A part left `None` is said the neutral way, claiming only what holds wherever a
    shell runs -- for `execute`, deepagents' own line stays -- and `""` says nothing
    there. Each may name a layout leaf as `{{inputs}}`, `{{outputs}}`, `{{memory}}`,
    `{{scratchpad}}` or `{{tool_results}}`, filled with this deployment's name for it.
    """

    prompt: str | None = None
    skills: str | None = None
    execute: str | None = None

    def __post_init__(self) -> None:
        # Here rather than when the prompt is rendered, which is a deployment's first
        # turn: a blank no layout fills is a typo, and the start is where to hear of it.
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
def kingfishers_shell() -> ShellPrompt:
    """What kingfisher's own shell says of itself.

    It runs on this host in the session root, with `TMPDIR` the scratchpad and
    `KINGFISHER_SKILLS` the catalogue -- true of no other shell, a runner a deployment
    supplies included: that is handed the command and nothing else, and starts wherever
    it was started. One instance, which `ShellPaths` recognises by identity.
    """
    prompt, skills, execute = (_prompt_text(name).strip() for name in _HERE)
    return ShellPrompt(prompt=prompt, skills=skills, execute=execute)


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


#: Enough for a fleet of agents rather than for one deployment. The cache is
#: keyed on the assembled `extra`, which used to be one workspace's `PROMPT.md`
#: and is now that plus whichever agent is running -- so the number of distinct
#: keys is the number of agents a process serves, not one. `layout` joins the key
#: because the paths woven through the prompt are its names: a fleet serving two
#: layouts must not be handed one layout's prompt for the other's agent. `shell` joins
#: it for the same reason: two backends' shells may say different things of themselves.
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
    # Removing the marker leaves a run of blank lines behind; collapse them so
    # the prefix sent on every step stays tidy.
    return re.sub(r"\n{3,}", "\n\n", assembled).strip() + "\n"


def _joined(prompt: str, extra: str) -> str:
    """Two prompt parts, separated the one way this codebase separates them."""
    if not extra.strip():
        return prompt
    return f"{prompt.strip()}\n\n---\n\n{extra.strip()}"


class Instructions(Protocol):
    """A deployment's standing text for every agent and subagent it runs.

    Asked once per agent built, so a change reaches the next turn without a restart,
    and read after kingfisher's own prompt and before the agent's: it is the most
    general of the additions, and the agent's own line the most specific. Empty for
    none. What kingfisher tells every agent is not here and not replaceable.
    """

    def text(self, cfg: Config) -> str:
        """The instructions, or an empty string for none."""
        ...


class InstructionsFile:
    """Kingfisher's own `Instructions`: `INSTRUCTIONS.md` in the definitions folder.

    Beside `models.yaml` and `source_ids.yaml`, because it is what people write and the
    workspace is what sessions make -- and so it is one of the files the macOS fence
    keeps the shell from editing.
    """

    def text(self, cfg: Config) -> str:
        path = cfg.authored_files[INSTRUCTIONS_FILE]
        return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def refuse_a_workspace_prompt(cfg: Config) -> None:
    """Refuse a `PROMPT.md` left in the workspace, which nothing reads any more.

    Ignored, the house rules it held would stop reaching every agent with nothing said:
    the turn runs, and runs differently.
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

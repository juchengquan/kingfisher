"""Assembling the system prompt from the markdown that ships with the package.

Split out of `agent.py`, and not for tidiness. `system_prompt` is a public export, it
needs nothing but `Config` and the standard library, and it used to cost **764 ms and
3,107 modules** to reach -- because touching any name in `agent.py` runs that module's
`from deepagents import ...`, and Python cannot import one name from a module without
executing all of it. Measured after the split: about 7 ms and 90 modules.

Almost none of that was deepagents, which is 21 ms of its own code. It was openai (281
ms), anthropic (239 ms) and google (134 ms) -- the last one for a provider kingfisher
has no path to at all. Their type modules are pydantic classes, and defining a model
compiles a validator at import time.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

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
_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")

#: Where optional capability sections are spliced in. An HTML comment, so
#: `system.md` stays a plain Markdown document that renders and edits normally.
CAPABILITY_MARKER = "<!-- capabilities -->"

CAPABILITY_FILES = {
    "skills": "capability_skills.md",
    "memory": "capability_memory.md",
}

#: Optional, user-authored, per-workspace prompt additions. Distinct from
#: `/memory/AGENTS.md`, which the agent writes: this file is yours.
USER_PROMPT_FILE = "PROMPT.md"

#: deepagents' `MEMORY_SYSTEM_PROMPT` with its lifetime claims corrected, and not to
#: be re-synced from it on an upgrade: the library's own says memory lasts into
#: "future conversations", and `/memory` is deleted with its session. Sent as
#: written: deepagents strips HTML comments from the memory file, not from this.
MEMORY_GUIDELINES = "memory_guidelines.md"


def _prompt_text(name: str) -> str:
    return resources.files("kingfisher.prompts").joinpath(name).read_text(encoding="utf-8")


def _filled(name: str, layout: Layout) -> str:
    """One shipped prompt, with every `{{leaf}}` replaced by the layout's name for it.

    An unknown placeholder is an edit naming a leaf the layout does not have, caught
    here rather than reaching the model as `{{typo}}`.
    """
    names = {
        "inputs": layout.inputs,
        "outputs": layout.outputs,
        "memory": layout.memory,
        "scratchpad": layout.scratchpad,
        "tool_results": layout.tool_results,
    }

    def _one(match: re.Match[str]) -> str:
        leaf = match.group(1)
        if leaf not in names:  # pragma: no cover -- guards a bad edit to the markdown
            msg = f"{name} names an unknown layout leaf {{{{{leaf}}}}}; expected one of {names}"
            raise ValueError(msg)
        return names[leaf]

    return _PLACEHOLDER.sub(_one, _prompt_text(name))


def memory_guidelines() -> str:
    """The template `MemoryMiddleware` puts the memory file into, `{agent_memory}` and all."""
    return _prompt_text(MEMORY_GUIDELINES)


#: Enough for a fleet of agents rather than for one deployment. The cache is
#: keyed on the assembled `extra`, which used to be one workspace's `PROMPT.md`
#: and is now that plus whichever agent is running -- so the number of distinct
#: keys is the number of agents a process serves, not one. `layout` joins the key
#: because the paths woven through the prompt are its names: a fleet serving two
#: layouts must not be handed one layout's prompt for the other's agent.
@lru_cache(maxsize=32)
def render_system_prompt(
    *,
    skills_enabled: bool = False,
    memory_enabled: bool = False,
    extra: str = "",
    layout: Layout = DEFAULT_LAYOUT,
) -> str:
    """Assemble the prompt for the capabilities that are actually wired."""
    base = _filled(BASE_PROMPT, layout)
    if CAPABILITY_MARKER not in base:  # pragma: no cover -- guards a silent edit
        msg = f"{BASE_PROMPT} is missing the {CAPABILITY_MARKER} marker"
        raise ValueError(msg)

    enabled = [
        name
        for name, on in (("skills", skills_enabled), ("memory", memory_enabled))
        if on
    ]
    sections = "\n\n".join(_filled(CAPABILITY_FILES[name], layout).strip() for name in enabled)

    assembled = _joined(base.replace(CAPABILITY_MARKER, sections), extra)
    # Removing the marker leaves a run of blank lines behind; collapse them so
    # the prefix sent on every step stays tidy.
    return re.sub(r"\n{3,}", "\n\n", assembled).strip() + "\n"


def _joined(prompt: str, extra: str) -> str:
    """Two prompt parts, separated the one way this codebase separates them."""
    if not extra.strip():
        return prompt
    return f"{prompt.strip()}\n\n---\n\n{extra.strip()}"


def user_prompt(workspace: Path | None) -> str:
    """Read the workspace's optional `PROMPT.md`, if the user wrote one."""
    if workspace is None:
        return ""
    path = Path(workspace) / USER_PROMPT_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def with_user_prompt(prompt: str, workspace: Path | None) -> str:
    """`prompt`, plus whatever the workspace put in its own `PROMPT.md`."""
    return _joined(prompt, user_prompt(workspace))


def behind_deployment(prompt: str, cfg: Config | None) -> str:
    """`prompt`, behind this deployment's house-rules prompt if it set one.

    First, because it is the most general of the additions -- a standing instruction
    for every agent -- and the agent's own line and the workspace's `PROMPT.md` are the
    more specific ones that follow it. Empty when none is set, which leaves `prompt`
    exactly as it was: `_joined` collapses an empty *trailing* part, and this one leads.
    """
    deployment = cfg.deployment_prompt if cfg else ""
    return _joined(deployment, prompt) if deployment.strip() else prompt


def system_prompt(cfg: Config | None = None, agent: str = "") -> str:
    """The assembled prompt: the harness, then this deployment, this workspace, this agent."""
    return render_system_prompt(
        skills_enabled=bool(cfg and cfg.skills_enabled),
        memory_enabled=bool(cfg and cfg.memory_enabled),
        extra=behind_deployment(_joined(user_prompt(cfg.workspace if cfg else None), agent), cfg),
        layout=cfg.layout if cfg else DEFAULT_LAYOUT,
    )

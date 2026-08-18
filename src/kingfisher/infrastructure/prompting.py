"""Assembling the system prompt from the markdown that ships with the package.

Split out of `agent.py`, and not for tidiness. `system_prompt` is a public
export, it needs nothing but `Config` and the standard library, and it used to
cost **764 ms and 3,107 modules** to reach -- because touching any name in
`agent.py` runs that module's `from deepagents import ...`, and Python cannot
import one name from a module without executing all of it. Measured after the
split: about 7 ms and 90 modules.

Almost none of that was deepagents, which is 21 ms of its own code. It was
openai (281 ms), anthropic (239 ms) and google (134 ms) -- the last one for a
provider kingfisher has no path to at all. Their type modules are pydantic
classes, and defining a model compiles a validator at import time.

So the rule for this module is the point of it: **it imports nothing foreign,
and nothing that imports anything foreign.** `test_a_light_export_stays_light`
holds it to that.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingfisher.config import Config

BASE_PROMPT = "system.md"

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


def _prompt_text(name: str) -> str:
    return resources.files("kingfisher.prompts").joinpath(name).read_text(encoding="utf-8")


#: Enough for a fleet of agents rather than for one deployment. The cache is
#: keyed on the assembled `extra`, which used to be one workspace's `PROMPT.md`
#: and is now that plus whichever agent is running -- so the number of distinct
#: keys is the number of agents a process serves, not one.
@lru_cache(maxsize=32)
def render_system_prompt(
    *,
    skills_enabled: bool = False,
    memory_enabled: bool = False,
    extra: str = "",
) -> str:
    """Assemble the prompt for the capabilities that are actually wired.

    Plain Markdown files concatenated at one marker rather than a template
    language: the prompt is a document you read and edit, and template syntax
    fights that. The assembly logic lives here, where it is testable.

    The flags gate prompt sections *and* the corresponding middleware from a
    single source, so the agent is never told about a `/skills` directory it
    has no tool to load, or a memory file that was never mounted.

    Nothing task-specific belongs in these files. Domain instructions come from
    the task, from skills, or from the workspace's own `PROMPT.md` — a general
    agent's base prompt should read the same whatever the project is about.
    """
    base = _prompt_text(BASE_PROMPT)
    if CAPABILITY_MARKER not in base:  # pragma: no cover -- guards a silent edit
        msg = f"{BASE_PROMPT} is missing the {CAPABILITY_MARKER} marker"
        raise ValueError(msg)

    enabled = [
        name
        for name, on in (("skills", skills_enabled), ("memory", memory_enabled))
        if on
    ]
    sections = "\n\n".join(_prompt_text(CAPABILITY_FILES[name]).strip() for name in enabled)

    assembled = _joined(base.replace(CAPABILITY_MARKER, sections), extra)
    # Removing the marker leaves a run of blank lines behind; collapse them so
    # the prefix sent on every step stays tidy.
    return re.sub(r"\n{3,}", "\n\n", assembled).strip() + "\n"


def _joined(prompt: str, extra: str) -> str:
    """Two prompt parts, separated the one way this codebase separates them.

    A function because it is applied twice: to the main agent's assembled
    prompt, and to a delegate's own. Written out at both would be one rule with
    two spellings, which is how a separator comes to differ by a newline.
    """
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
    """`prompt`, plus whatever the workspace put in its own `PROMPT.md`.

    For a delegate, which gets none of the assembled system prompt -- and
    should not: that document is the harness describing itself, and a delegate
    already has its own procedure. `PROMPT.md` is the other thing, the part a
    *workspace* wrote about its own work, and it applied to the main agent and
    silently not to anything the main agent delegated to.

    Appended rather than prepended, so it reads as a qualifier on the
    procedure, which is the order the main agent's prompt uses too.
    """
    return _joined(prompt, user_prompt(workspace))


def system_prompt(cfg: Config | None = None, agent: str = "") -> str:
    """The assembled prompt: the harness, then this workspace, then this agent.

    Most specific last, which is the order every other join here uses. `agent`
    is an agent definition's own `system_prompt`, and it is *added* rather than
    substituted -- an agent without the harness document is not leaner, it is
    one holding tools nobody told it about.

    Empty for a build with no agent, which is what every caller passed before
    agents existed and what a test that builds a bare graph still passes.
    """
    return render_system_prompt(
        skills_enabled=bool(cfg and cfg.skills_enabled),
        memory_enabled=bool(cfg and cfg.memory_enabled),
        extra=_joined(user_prompt(cfg.workspace if cfg else None), agent),
    )

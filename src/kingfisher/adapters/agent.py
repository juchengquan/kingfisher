"""Agent assembly — the composition root.

Construction stays free of side effects that a test would have to clean up, and
every dependency is injectable, so wiring can be exercised with a fake model and
no network, no database, and no sweeping.
"""

from __future__ import annotations

import re
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.adapters.backend import build_backend
from kingfisher.adapters.models import build_model
from kingfisher.domain.config import Config

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

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

SKILLS_SOURCES = ["/skills/"]
MEMORY_SOURCES = ["/memory/AGENTS.md"]

# `/data` holds the only artifacts git cannot restore, because inputs are never
# committed. `FilesystemOperation` is just read|write and `delete` maps to
# write, so this single rule covers write_file, edit_file and delete.
#
# It does not cover `execute` — filesystem permissions are applied by
# FilesystemMiddleware at the tool level, and the shell bypasses them entirely,
# which is why `protect_data()` drops the write bits underneath this.
DATA_IS_READ_ONLY = FilesystemPermission(
    operations=["write"],
    paths=["/data/**"],
    mode="deny",
)


def _prompt_text(name: str) -> str:
    return resources.files("kingfisher.prompts").joinpath(name).read_text(encoding="utf-8")


@lru_cache(maxsize=8)
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

    assembled = base.replace(CAPABILITY_MARKER, sections)
    if extra.strip():
        assembled = f"{assembled.strip()}\n\n---\n\n{extra.strip()}"
    # Removing the marker leaves a run of blank lines behind; collapse them so
    # the prefix sent on every step stays tidy.
    return re.sub(r"\n{3,}", "\n\n", assembled).strip() + "\n"


def user_prompt(workspace: Path | None) -> str:
    """Read the workspace's optional `PROMPT.md`, if the user wrote one."""
    if workspace is None:
        return ""
    path = Path(workspace) / USER_PROMPT_FILE
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def system_prompt(cfg: Config | None = None) -> str:
    """The assembled prompt for this workspace."""
    return render_system_prompt(
        skills_enabled=bool(cfg and cfg.skills_enabled),
        memory_enabled=bool(cfg and cfg.memory_enabled),
        extra=user_prompt(cfg.workspace if cfg else None),
    )


def build_agent(
    cfg: Config,
    *,
    model: Any | None = None,
    backend: Any | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent.

    deepagents 0.7.6 ships no planning tool, so `TodoListMiddleware` is added
    explicitly. Its default prompt fragment is kept rather than trimmed: it
    already carries anti-overuse guidance and a finishing convention that would
    only be rewritten worse.
    """
    extras: dict[str, Any] = {}
    if cfg.skills_enabled:
        extras["skills"] = SKILLS_SOURCES
    if cfg.memory_enabled:
        extras["memory"] = MEMORY_SOURCES

    return create_deep_agent(
        model=model if model is not None else build_model(cfg),
        backend=backend if backend is not None else build_backend(cfg),
        system_prompt=system_prompt(cfg),
        middleware=[TodoListMiddleware()],
        permissions=[DATA_IS_READ_ONLY],
        checkpointer=checkpointer,
        **extras,
    )

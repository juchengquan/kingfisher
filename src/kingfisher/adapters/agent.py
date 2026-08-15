"""Agent assembly — the composition root.

Construction stays free of side effects that a test would have to clean up, and
every dependency is injectable, so wiring can be exercised with a fake model and
no network, no database, and no sweeping.
"""

from __future__ import annotations

import re
from dataclasses import replace
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.adapters.backend import build_backend
from kingfisher.adapters.models import build_model
from kingfisher.adapters.scoping import HostPathGuard, ScopedSkills, ToolAllowlist
from kingfisher.adapters.subagent_store import load_all
from kingfisher.config import Config
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.subagent import SubagentSpec

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

#: For a request that declined memory a deployment did wire. Reads are denied
#: rather than the prompt rewritten: the prompt is the cached prefix.
MEMORY_IS_DENIED = FilesystemPermission(
    operations=["read"],
    paths=["/memory/**"],
    mode="deny",
)


class CapabilityError(ValueError):
    """A request named a tool, skill or subagent the workspace does not offer."""


def _available_skills(workspace: Path) -> tuple[str, ...]:
    """Skill names the workspace defines, by directory name."""
    directory = Path(workspace) / "skills"
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.name for p in directory.iterdir() if (p / "SKILL.md").is_file()))


def registered_tools(graph: Any) -> tuple[str, ...]:
    """Tool names the compiled agent can actually dispatch.

    Derived from the graph rather than listed here, because a hardcoded list
    would drift the first time deepagents adds or renames a tool. The path into
    the tool node is not a public contract, so a shape we do not recognise
    yields `()` — which callers read as "cannot check" — rather than raising and
    taking down every build over an introspection detail.
    """
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    return tuple(sorted(by_name)) if isinstance(by_name, dict) else ()


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


def _as_subagent(spec: SubagentSpec, cfg: Config) -> dict[str, Any]:
    """Translate kingfisher's definition into deepagents' `SubAgent`.

    Every field maps directly except `tools`. deepagents' `SubAgent.tools` is a
    sequence of tool *objects* it will register, not a selection from the ones
    the parent already has — handing it names raises inside `ToolNode`. The
    objects are built from the backend deep inside `create_deep_agent` and are
    not reachable here, so the restriction is applied the same way a request's
    own tool restriction is: a `ToolAllowlist` on the subagent's middleware,
    which selects by name and refuses anything else.
    """
    subagent: dict[str, Any] = {
        "name": spec.name,
        "description": spec.description,
        "system_prompt": spec.system_prompt,
    }
    if spec.tools is not None:
        subagent["middleware"] = [ToolAllowlist(spec.tools)]

    # A *name* here would be resolved by deepagents' `init_chat_model`, which
    # infers its own provider and reads credentials from the environment --
    # around the provider table, the configured base_url, and the api_style
    # this deployment chose. It also re-enables the profile behaviour that
    # `adapters.models` exists to avoid. So we build the instance ourselves.
    #
    # `role_models` wins over the definition: which model a role runs on is an
    # operator's cost decision, and it should not require editing content.
    if (model_id := cfg.role_models.get(spec.name, spec.model)) is not None:
        subagent["model"] = build_model(replace(cfg, model=model_id))
    return subagent


def _skill_denials(
    activated: tuple[str, ...], available: tuple[str, ...]
) -> list[FilesystemPermission]:
    """Deny reads of skills this request did not activate.

    The listing filter only stops the agent being *told*; this stops the file
    tools reading it anyway. Neither stops `execute`, which bypasses tool-level
    permissions entirely — so this is a real boundary only for a request that
    did not activate the shell.
    """
    allowed = set(activated)
    return [
        FilesystemPermission(operations=["read"], paths=[f"/skills/{name}/**"], mode="deny")
        for name in available
        if name not in allowed
    ]


def build_agent(
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
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
    capabilities = capabilities or Capabilities()
    resolved_backend = backend if backend is not None else build_backend(cfg)
    # Unconditional: the backend rejects host paths on every run, so the
    # thing that turns that rejection into a correction must always be here.
    middleware: list[Any] = [TodoListMiddleware(), HostPathGuard()]
    permissions = [DATA_IS_READ_ONLY]
    extras: dict[str, Any] = {}

    # Two axes, and this is where they meet: `cfg` says what is wired, the
    # request says what it wants of that. Narrowing can only subtract --
    # `memory=True` against a deployment that wired none stays off.
    if cfg.memory_enabled and capabilities.memory is not False:
        extras["memory"] = MEMORY_SOURCES
    elif cfg.memory_enabled:
        # Wired but declined. The prompt still describes memory, because it is
        # the cached prefix and must not vary per request; this stops the file
        # being read anyway. deepagents puts memory behind its own cache
        # breakpoint, so dropping the block leaves the prefix cached.
        permissions.append(MEMORY_IS_DENIED)

    if cfg.skills_enabled:
        if capabilities.skills is None:
            extras["skills"] = SKILLS_SOURCES
        else:
            available = _available_skills(cfg.workspace)
            unknown = tuple(s for s in capabilities.skills if s not in available)
            if unknown:
                msg = f"unknown skill(s): {', '.join(unknown)}; workspace offers {available}"
                raise CapabilityError(msg)
            # Supplied as middleware rather than via `skills=`: passing that
            # argument makes deepagents construct its own SkillsMiddleware,
            # leaving no way to substitute a filtered one.
            middleware.append(
                ScopedSkills(
                    allowed=capabilities.skills,
                    backend=resolved_backend,
                    sources=SKILLS_SOURCES,
                )
            )
            permissions.extend(_skill_denials(capabilities.skills, available))

    if capabilities.subagents is not None:
        defined = load_all(cfg.workspace)
        unknown = tuple(n for n in capabilities.subagents if n not in defined)
        if unknown:
            msg = f"unknown subagent(s): {', '.join(unknown)}; workspace defines {tuple(defined)}"
            raise CapabilityError(msg)
        extras["subagents"] = [_as_subagent(defined[n], cfg) for n in capabilities.subagents]

    if capabilities.tools is not None:
        middleware.append(ToolAllowlist(capabilities.tools))

    graph = create_deep_agent(
        model=model if model is not None else build_model(cfg),
        backend=resolved_backend,
        system_prompt=system_prompt(cfg),
        middleware=middleware,
        permissions=permissions,
        checkpointer=checkpointer,
        **extras,
    )

    # Tool names are checked here rather than before construction, because the
    # registered set is a property of the assembled graph. A typo would
    # otherwise narrow the allowlist in silence -- the same "quietly less than
    # you asked for" failure that skills and subagents already refuse.
    if (
        capabilities.tools is not None
        and (known := registered_tools(graph))
        and (unknown := tuple(t for t in capabilities.tools if t not in known))
    ):
        msg = f"unknown tool(s): {', '.join(unknown)}; this agent offers {known}"
        raise CapabilityError(msg)

    return graph

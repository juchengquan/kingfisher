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

from kingfisher.config import Config
from kingfisher.domain import skill
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.subagent import DIRECTORY as SUBAGENT_DIRECTORY
from kingfisher.domain.subagent import SubagentSpec
from kingfisher.infrastructure import skill_store
from kingfisher.infrastructure.backend import build_backend
from kingfisher.infrastructure.models import build_model
from kingfisher.infrastructure.scoping import HostPathGuard, ScopedSkills, ToolAllowlist
from kingfisher.infrastructure.subagent_store import load_all
from kingfisher.infrastructure.tool_store import load_tools, tool_name

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from langgraph.graph.state import CompiledStateGraph

BASE_PROMPT = "system.md"

#: The role every delegate runs as, for `Config.role_models`. One of `ROLES`,
#: which is what `from_env` populates -- a delegate's own name is not.
SUBAGENT_ROLE = "subagent"

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

#: The catalogue first, then this session's uploads. deepagents loads sources
#: in order and lets a later one override an earlier, which is exactly what
#: `uploads` refuses to allow -- a collision is rejected before it can happen,
#: so the ordering here never decides anything.
SKILLS_SOURCES = [("/skills/", "Catalogue"), ("/skills/uploaded/", "Uploaded")]
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


def _uploaded_skills(session_dir: Path) -> Path:
    """Where this session's own skills were unpacked."""
    return Path(session_dir) / skill.DIRECTORY / skill.UPLOADED


def _uploaded_subagents(session_dir: Path) -> Path:
    """Where this session's own subagents were unpacked."""
    return Path(session_dir) / SUBAGENT_DIRECTORY


def _available_skills(cfg: Config, session_dir: Path | None) -> tuple[str, ...]:
    """Every skill this request may activate: the catalogue, plus its own.

    One flat set, because `capabilities.skills` names skills and not sources.
    They cannot collide — `uploads` rejects an upload that shares a catalogue
    name — so merging loses nothing.
    """
    names = set(skill_store.names(cfg.skills_dir))
    if session_dir is not None:
        names |= set(skill_store.names(_uploaded_skills(session_dir)))
    return tuple(sorted(names))


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


def _subagent_skills(
    spec: SubagentSpec, available: tuple[str, ...], activated: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Which skills a delegate is told about, or `None` for none.

    Two different refusals, and the difference is the same one `build_agent`
    already draws for a request. A name nothing defines is a mistake in the
    definition and raises. A name that exists but this request did not activate
    is not a mistake -- it is a caller narrower than the definition -- so it is
    dropped, exactly as `Capabilities.intersect` drops it for the parent. A
    delegate cannot reach past the request that summoned it.
    """
    if spec.skills is None:
        return None
    unknown = tuple(name for name in spec.skills if name not in available)
    if unknown:
        msg = (
            f"subagent {spec.name!r} names unknown skill(s): {', '.join(unknown)}; "
            f"this request offers {available}"
        )
        raise CapabilityError(msg)
    if activated is None:
        return spec.skills
    return tuple(name for name in spec.skills if name in activated)



def _subagent_middleware(
    spec: SubagentSpec,
    registry: Mapping[str, Callable[[], Any]],
    allowed: tuple[str, ...] | None,
) -> list[Any]:
    """Build the middleware a definition asked for, or refuse to.

    Two refusals, and both raise -- neither is the "caller was narrower" case
    that quietly drops a skill. A name nothing registered is a mistake in the
    definition. A name the deployment registered but did not *grant* is an
    escalation attempt or a misconfiguration, and running with silently less
    middleware than the definition specified could mean running without the
    rate limit or the audit hook it was written to have.

    Checked identically for a catalogue definition and an uploaded one.
    `Capabilities.including` widens skills and subagents for an upload because
    those are the caller's own text; a middleware name selects code the
    deployment wrote, so an upload gets no such exemption.
    """
    if not spec.middleware:
        return []

    unknown = tuple(name for name in spec.middleware if name not in registry)
    if unknown:
        msg = (
            f"subagent {spec.name!r} names unregistered middleware: {', '.join(unknown)}; "
            f"this deployment registered {tuple(registry)}"
        )
        raise CapabilityError(msg)

    if allowed is not None:
        ungranted = tuple(name for name in spec.middleware if name not in allowed)
        if ungranted:
            msg = (
                f"subagent {spec.name!r} names middleware this request may not use: "
                f"{', '.join(ungranted)}; permitted {allowed}"
            )
            raise CapabilityError(msg)

    return [registry[name]() for name in spec.middleware]



def _subagent_endpoint(
    spec: SubagentSpec, cfg: Config, allowed: tuple[str, ...] | None
) -> tuple[str | None, str | None]:
    """The (provider, model) a delegate runs as, or refuse to choose one.

    They move together. Overriding only the model, against a definition that
    pins `provider: openai`, would send a MiniMax model name to OpenAI -- a 404
    if you are lucky and a wrong-model run if you are not. Which endpoint runs
    which model should not be settled by two people who cannot see each other's
    half, so a half-override against a pinned provider is refused.

    An operator who overrides both has said what they mean and wins, which is
    the point of the override existing at all.
    """
    model_override = cfg.role_models.get(SUBAGENT_ROLE)
    provider_override = cfg.role_providers.get(SUBAGENT_ROLE)

    if model_override is not None and provider_override is None and spec.provider is not None:
        msg = (
            f"subagent {spec.name!r} pins provider {spec.provider!r}, but an operator "
            f"overrode only its model; set KINGFISHER_PROVIDER_SUBAGENT too, or neither"
        )
        raise CapabilityError(msg)

    provider = provider_override if provider_override is not None else spec.provider
    model = model_override if model_override is not None else spec.model

    if provider is not None and allowed is not None and provider not in allowed:
        msg = (
            f"subagent {spec.name!r} names endpoint {provider!r}, which this request "
            f"may not use; permitted {allowed}"
        )
        raise CapabilityError(msg)

    return provider, model


def _as_subagent(  # noqa: PLR0913 -- one parameter per thing a definition may
    # narrow, each resolved by its own rule above. Bundling them would hide
    # which of those rules applied to a given delegate.
    spec: SubagentSpec,
    cfg: Config,
    *,
    providers: tuple[str, ...] | None = None,
    backend: Any = None,
    skills: tuple[str, ...] | None = None,
    extra_middleware: list[Any] | None = None,
) -> dict[str, Any]:
    """Translate kingfisher's definition into deepagents' `SubAgent`.

    Every field maps directly except `tools`, `skills` and `middleware`.
    deepagents' `SubAgent.tools` is a sequence of tool *objects* it will
    register, not a selection from the ones the parent already has — handing it
    names raises inside `ToolNode`. The
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
    middleware: list[Any] = []
    if spec.tools is not None:
        middleware.append(ToolAllowlist(spec.tools))
    # A subagent inherits none of its parent's middleware, so an index it is
    # not given is an index it has no idea exists. `SubAgent.skills` would take
    # source *paths*; this selects by name, which is what a definition writes.
    if skills is not None and backend is not None:
        middleware.append(
            ScopedSkills(allowed=skills, backend=backend, sources=SKILLS_SOURCES)
        )
    # Last, so a deployment's middleware sees the tool and skill scoping
    # kingfisher applied rather than running ahead of it.
    middleware.extend(extra_middleware or [])
    if middleware:
        subagent["middleware"] = middleware

    # A *name* here would be resolved by deepagents' `init_chat_model`, which
    # infers its own provider and reads credentials from the environment --
    # around the provider table, the configured base_url, and the api_style
    # this deployment chose. It also re-enables the profile behaviour that
    # `infrastructure.models` exists to avoid. So we build the instance ourselves.
    #
    # `role_models` wins over the definition: which model a role runs on is an
    # operator's cost decision, and it should not require editing content.
    #
    # Keyed by *role*, not by this subagent's name. `from_env` populates
    # `role_models` from `KINGFISHER_MODEL_MAIN`, `_SUBAGENT` and `_SUMMARIZER`,
    # so a lookup by name only ever matched a delegate literally called one of
    # those -- the override above was documented, tested nowhere, and fired for
    # nothing. Per-delegate overrides would need `ROLES` to become unbounded and
    # its names to come from workspace content, which is a different decision.
    provider, model_id = _subagent_endpoint(spec, cfg, providers)
    if model_id is not None or provider is not None:
        # `replace` rather than a build_model parameter: an endpoint is exactly
        # the three Config fields a model is built from, so swapping them says
        # "build as if this deployment were pointed there" with nothing else
        # changed.
        endpoint = cfg.endpoint_for(provider)
        subagent["model"] = build_model(
            replace(
                cfg,
                model=model_id if model_id is not None else cfg.model,
                api_style=endpoint.api_style,
                base_url=endpoint.base_url,
                api_key=endpoint.api_key,
            )
        )
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


def _backend_for(cfg: Config, session_dir: Path | None, backend: Any | None) -> Any:
    """The filesystem an agent sees: rooted at a session, or supplied ready-made.

    Neither is a wiring mistake rather than a default worth guessing at. There
    is no sensible fallback: an agent rooted at the workspace instead of at a
    session would write one caller's files into a directory every other caller
    can read.
    """
    if backend is not None:
        return backend
    if session_dir is not None:
        return build_backend(cfg, session_dir)
    msg = "build_agent needs either a session_dir to root a backend at, or a backend"
    raise ValueError(msg)


def _with_workspace_tools(
    cfg: Config, assemble: Callable[[tuple[Any, ...]], CompiledStateGraph]
) -> CompiledStateGraph:
    """Assemble the agent, adding whatever tools the workspace defines.

    Assembled twice when there are any, and only then. `tools_by_name` is a
    dict, so a workspace tool called `read_file` would take the name in silence
    and the real one would simply stop existing -- the same "quietly different
    from what you asked for" failure the capability checks refuse elsewhere.
    The built-in set is a property of an assembled graph and cannot be listed
    without building one, and ~30ms against a model call of seconds is a cheap
    price for not guessing at it.
    """
    graph = assemble(())
    workspace_tools = load_tools(cfg.tools_dir)
    if not workspace_tools:
        return graph

    builtin = set(registered_tools(graph))
    shadowed = tuple(sorted(n for t in workspace_tools if (n := tool_name(t)) in builtin))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {cfg.tools_dir}"
        )
        raise CapabilityError(msg)
    return assemble(workspace_tools)


def build_agent(  # noqa: PLR0913 -- the composition root; each argument is one
    # injectable collaborator, and folding them into a parameter object would
    # hide exactly what a test is allowed to substitute.
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
    session_dir: Path | None = None,
    middleware_registry: Mapping[str, Callable[[], Any]] | None = None,
    model: Any | None = None,
    backend: Any | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent.

    `session_dir` is where the backend roots, so an agent belongs to one
    session and cannot be reused across them.

    deepagents 0.7.6 ships no planning tool, so `TodoListMiddleware` is added
    explicitly. Its default prompt fragment is kept rather than trimmed: it
    already carries anti-overuse guidance and a finishing convention that would
    only be rewritten worse.
    """
    capabilities = capabilities or Capabilities()
    resolved_backend = _backend_for(cfg, session_dir, backend)
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
            available = _available_skills(cfg, session_dir)
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
        # The catalogue plus this session's own. They cannot collide: `uploads`
        # rejects an upload sharing a catalogue name before it is written.
        defined = dict(load_all(cfg.subagents_dir))
        if session_dir is not None:
            defined |= load_all(_uploaded_subagents(session_dir))
        unknown = tuple(n for n in capabilities.subagents if n not in defined)
        if unknown:
            msg = f"unknown subagent(s): {', '.join(unknown)}; this request offers {tuple(defined)}"
            raise CapabilityError(msg)
        offered = _available_skills(cfg, session_dir)
        registry = middleware_registry or {}
        extras["subagents"] = [
            _as_subagent(
                defined[n],
                cfg,
                backend=resolved_backend,
                providers=capabilities.providers,
                skills=_subagent_skills(defined[n], offered, capabilities.skills),
                extra_middleware=_subagent_middleware(
                    defined[n], registry, capabilities.middleware
                ),
            )
            for n in capabilities.subagents
        ]

    if capabilities.tools is not None:
        middleware.append(ToolAllowlist(capabilities.tools))

    def assemble(extra_tools: tuple[Any, ...]) -> CompiledStateGraph:
        return create_deep_agent(
            model=model if model is not None else build_model(cfg),
            backend=resolved_backend,
            system_prompt=system_prompt(cfg),
            middleware=middleware,
            permissions=permissions,
            checkpointer=checkpointer,
            tools=list(extra_tools) or None,
            **extras,
        )

    graph = _with_workspace_tools(cfg, assemble)

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

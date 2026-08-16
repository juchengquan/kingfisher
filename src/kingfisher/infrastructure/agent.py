"""Agent assembly: kingfisher's configuration and grants into a deepagents graph.

Not the composition root, though it said so for a while — `Kingfisher.__init__`
is, and says so too. That one chooses a deployment's collaborators: which
config, which session directories, which thread store, which middleware
registry. This one is the deepagents adapter, and the only reason it cannot
live a layer up is that assembling the graph means naming deepagents' own
types.

Construction stays free of side effects that a test would have to clean up, and
every dependency is injectable, so wiring can be exercised with a fake model and
no network, no database, and no sweeping.

Two jobs it used to do live beside it now, because at 657 lines it was doing
four. `prompting` assembles the system prompt -- moved out because it needs
nothing foreign, and sharing a file with `create_deep_agent` cost every consumer
of `system_prompt` 764ms and three provider SDKs. `delegation` resolves what a
delegate runs with. Neither calls anything here; `build_agent` is the only
caller of either.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.config import Config
from kingfisher.domain import skill
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.domain.subagent import DIRECTORY as SUBAGENT_DIRECTORY
from kingfisher.infrastructure import skill_store
from kingfisher.infrastructure.backend import (
    MEMORY_SOURCES,
    SKILLS_SOURCES,
    build_backend,
)
from kingfisher.infrastructure.delegation import (
    as_subagent,
    subagent_middleware,
    subagent_skills,
)
from kingfisher.infrastructure.models import build_model
from kingfisher.infrastructure.prompting import system_prompt
from kingfisher.infrastructure.scoping import (
    DeclaredDelegatesOnly,
    HostPathGuard,
    ScopedSkills,
    ToolAllowlist,
)
from kingfisher.infrastructure.subagent_store import load_all
from kingfisher.infrastructure.tool_store import load_tools, tool_name

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from langgraph.graph.state import CompiledStateGraph


#: Delegation, wherever it is dispatched from.
TASK_TOOL = "task"


#: For a request that declined memory a deployment did wire. Reads are denied
#: rather than the prompt rewritten: the prompt is the cached prefix.
MEMORY_IS_DENIED = FilesystemPermission(
    operations=["read"],
    paths=["/memory/**"],
    mode="deny",
)


def _uploaded_skills(session_dir: Path) -> Path:
    """Where this session's own skills were unpacked."""
    return Path(session_dir) / skill.DIRECTORY / skill.UPLOADED


def _uploaded_subagents(session_dir: Path) -> Path:
    """Where this session's own subagents were unpacked."""
    return Path(session_dir) / SUBAGENT_DIRECTORY


def available_skills(cfg: Config, session_dir: Path | None) -> tuple[str, ...]:
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


def _interpreter(cfg: Config, capabilities: Capabilities) -> Any:
    """The JavaScript sandbox, if this deployment wired one.

    `ptc` is the request's own tool grant, unchanged. A caller that withheld
    `execute` cannot reach it from inside the sandbox either; a caller that
    granted it is not escalating by using it from code. That is the same rule
    the parent and its delegates follow -- restrictions attach to narrowing,
    and a caller who narrowed nothing has nothing to escape from.

    `None` rather than an empty tuple when the request is unrestricted: the
    library reads `None` as "no allowlist", and an empty tuple would mean the
    opposite of what an unrestricted request asked for.

    `mode="thread"` because a thread here is a kingfisher session -- the
    checkpointer already keys on the same id, so REPL state and conversation
    state expire together rather than one outliving the other.

    Dispatching subagents from code needs the *async* path. `task()` inside the
    REPL awaits, so a sync `SqliteSaver` raises `does not support async
    methods` partway through a workflow that has already run. Use `arun` or
    `astream` with `async_checkpointer(cfg)`; everything else here works on
    either. Undocumented upstream, and found by running it.
    """
    # Deferred so that shipping the sandbox by default costs nothing to the runs
    # that never enable it. Measured, because the saving is smaller than it
    # looks: importing this standalone takes ~0.85s, but nearly all of that is
    # deepagents and langchain, which are loaded already. On top of kingfisher it
    # is ~15ms and ~6MB of resident memory -- worth deferring, not worth
    # restructuring anything else around.
    from langchain_quickjs import CodeInterpreterMiddleware  # noqa: PLC0415

    granted = capabilities.tools
    return CodeInterpreterMiddleware(
        # `task` is refused here by the library: it is always the top-level
        # `task()` global, and routing it through `tools.*` as well would give
        # two dispatch paths, the second losing `responseSchema`. Delegation is
        # governed by `subagents=` below instead.
        ptc=[t for t in granted if t != TASK_TOOL] if granted is not None else None,
        # Dispatch from code follows the same grant as dispatch from a tool
        # call. Left at its default this would let a request that withheld
        # `task` delegate anyway, from inside the sandbox -- a hole of exactly
        # the shape the delegate ceiling exists to close.
        subagents=granted is None or TASK_TOOL in granted,
        mode="thread",
        timeout=float(cfg.timeout_s),
    )


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
            available = available_skills(cfg, session_dir)
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
        offered = available_skills(cfg, session_dir)
        registry = middleware_registry or {}
        extras["subagents"] = [
            as_subagent(
                defined[n],
                cfg,
                backend=resolved_backend,
                providers=capabilities.providers,
                tools=capabilities.tools,
                skills=subagent_skills(defined[n], offered, capabilities.skills),
                extra_middleware=subagent_middleware(
                    defined[n], registry, capabilities.middleware
                ),
            )
            for n in capabilities.subagents
        ]

    if cfg.interpreter_enabled:
        middleware.append(_interpreter(cfg, capabilities))

    if capabilities.tools is not None:
        middleware.append(ToolAllowlist(capabilities.tools))
        # deepagents supplies a `general-purpose` delegate with "the same
        # capabilities as the main agent" and none of our middleware, present
        # whenever `task` is. Supplying one by the same name *replaces* it --
        # the specs are keyed by name -- so it keeps working and arrives with
        # the caller's ceiling on it, rather than being withheld.
        #
        # Their spec, our middleware: the description and prompt are tuned and
        # there is no reason to reinvent either.
        supplied = list(extras.get("subagents", ()))
        supplied.append(
            {**GENERAL_PURPOSE_SUBAGENT, "middleware": [ToolAllowlist(capabilities.tools)]}
        )
        extras["subagents"] = supplied
        # Backstop. Only these names are reachable, so a delegate deepagents
        # adds in some future version does not silently arrive unrestricted.
        reachable = tuple(spec["name"] for spec in supplied)
        middleware.append(DeclaredDelegatesOnly(reachable))

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

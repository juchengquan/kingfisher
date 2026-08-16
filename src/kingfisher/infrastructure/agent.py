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
from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError, narrowed
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
    from collections.abc import Callable, Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from kingfisher.domain.subagent import SubagentSpec


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


def available_skills(
    cfg: Config, session_dir: Path | None, *, catalogue: Mapping[str, Path] | None = None
) -> tuple[str, ...]:
    """Every skill this request may activate: the catalogue, plus its own.

    One flat set, because `capabilities.skills` names skills and not sources.
    They cannot collide — `uploads` rejects an upload that shares a catalogue
    name — so merging loses nothing.

    `catalogue` says where the shared half is read from, falling back to `cfg`.
    The session's own half never varies with it: uploads land under the session
    by definition, and a deployment relocating its catalogue does not move them.
    """
    names = set(skill_store.names((catalogue or cfg.catalogue_roots)["skills"]))
    if session_dir is not None:
        names |= set(skill_store.names(_uploaded_skills(session_dir)))
    return tuple(sorted(names))


def defined_subagents(
    cfg: Config, session_dir: Path | None, *, catalogue: Mapping[str, Path] | None = None
) -> dict[str, SubagentSpec]:
    """Every subagent this request may activate: the catalogue, plus its own.

    They cannot collide -- `uploads` rejects an upload sharing a catalogue name
    before it is written -- so the union loses nothing.

    A function because two callers need the same answer: `build_agent`, which
    wants the specs, and the service, which wants only the names so it can say
    which of them a request did not grant. Written out at both, the rule about
    what a session adds to the catalogue would exist twice.
    """
    defined = dict(load_all((catalogue or cfg.catalogue_roots)["subagents"]))
    if session_dir is not None:
        defined |= load_all(_uploaded_subagents(session_dir))
    return defined


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


# `/data` holds what a caller supplied and nothing else has a copy of: it is
# never re-derivable from the workspace, and kingfisher versions nothing.
# `FilesystemOperation` is just read|write and `delete` maps to write, so this
# single rule covers write_file, edit_file and delete.
#
# It does not cover `execute` — filesystem permissions are applied by
# FilesystemMiddleware at the tool level, and the shell bypasses them entirely,
# which is why `protect_data()` drops the write bits underneath this.
DATA_IS_READ_ONLY = FilesystemPermission(
    operations=["write"],
    paths=["/data/**"],
    mode="deny",
)


def _interpreter(cfg: Config, permitted: tuple[str, ...] | None) -> Any:
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

    # `None` here is the library's "no allowlist", which is what an
    # unrestricted request resolves to. A request that granted no tools gets an
    # empty list, which is the opposite and has to stay distinguishable.
    ptc: list[Any] | None = (
        None if permitted is None else [t for t in permitted if t != TASK_TOOL]
    )
    return CodeInterpreterMiddleware(
        # `task` is refused here by the library: it is always the top-level
        # `task()` global, and routing it through `tools.*` as well would give
        # two dispatch paths, the second losing `responseSchema`. Delegation is
        # governed by `subagents=` below instead.
        ptc=ptc,
        # Dispatch from code follows the same grant as dispatch from a tool
        # call. Left at its default this would let a request that withheld
        # `task` delegate anyway, from inside the sandbox -- a hole of exactly
        # the shape the delegate ceiling exists to close.
        subagents=permitted is None or TASK_TOOL in permitted,
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


def _backend_for(
    cfg: Config, session_dir: Path | None, backend: Any | None, catalogue: Mapping[str, Path]
) -> Any:
    """The filesystem an agent sees: rooted at a session, or supplied ready-made.

    Neither is a wiring mistake rather than a default worth guessing at. There
    is no sensible fallback: an agent rooted at the workspace instead of at a
    session would write one caller's files into a directory every other caller
    can read.

    A ready-made backend is taken as it is, catalogue included. It was built by
    whoever passed it, and re-routing `/skills/` underneath them would be a
    second answer to a question they already answered.
    """
    if backend is not None:
        return backend
    if session_dir is not None:
        return build_backend(cfg, session_dir, catalogue=catalogue)
    msg = "build_agent needs either a session_dir to root a backend at, or a backend"
    raise ValueError(msg)





def workspace_tool_names(
    cfg: Config, *, catalogue: Mapping[str, Path] | None = None
) -> tuple[str, ...]:
    """The tools this workspace defines, by name, without assembling anything.

    Knowable off disk, unlike the built-in set. That asymmetry is why the two
    axes resolve differently and why only one of them needs a probe.
    """
    directory = (catalogue or cfg.catalogue_roots)["tools"]
    return tuple(sorted(n for tool in load_tools(directory) if (n := tool_name(tool))))


def _workspace_tool_names(
    workspace_tools: Sequence[Any], *, builtin: tuple[str, ...], directory: Path
) -> tuple[str, ...]:
    """What the workspace defines, refusing anything that shadows a built-in.

    `tools_by_name` is a dict, so a workspace tool called `read_file` would take
    the name in silence and the real one would simply stop existing -- the same
    "quietly different from what you asked for" failure the capability checks
    refuse elsewhere. It matters more now that the two are granted separately: a
    shadowed name would be permitted by one axis and enforced as the other.

    Takes the directory rather than the `Config` it used to derive it from: the
    only use is naming the place to go and rename them, and that place is now
    whatever the catalogue says it is.
    """
    names = tuple(sorted(n for tool in workspace_tools if (n := tool_name(tool))))
    shadowed = tuple(n for n in names if n in set(builtin))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {directory}"
        )
        raise CapabilityError(msg)
    return names


def _refuse_unknown_tools(
    capabilities: Capabilities, *, builtin: tuple[str, ...], workspace: tuple[str, ...]
) -> None:
    """A name on the wrong axis, or on neither.

    Naming a built-in in `tools` is the mistake the split creates, so it is
    worth its own sentence: the name exists, and saying "unknown tool" about a
    tool that plainly exists would send someone looking in the wrong place.
    """
    for asked, own, other, here, there in (
        (capabilities.tools, workspace, builtin, "tools", "builtin_tools"),
        (capabilities.builtin_tools, builtin, workspace, "builtin_tools", "tools"),
    ):
        if asked in (ALL, None):
            continue
        if misplaced := tuple(n for n in asked if n in set(other)):
            msg = (
                f"{', '.join(misplaced)} is not a {here[:-1]} of this workspace; "
                f"it is a {there[:-1].replace('_', ' ')} -- name it in {there}"
            )
            raise CapabilityError(msg)
        if unknown := tuple(n for n in asked if n not in set(own)):
            msg = f"unknown {here[:-1]}(s): {', '.join(unknown)}; this agent offers {own}"
            raise CapabilityError(msg)

def _permitted_tools(
    capabilities: Capabilities,
    *,
    builtin: tuple[str, ...],
    workspace: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Every tool name this request may call, or `None` for no restriction.

    Two axes, one allowlist. The middleware filters a single flat tool list by
    name, so the two grants meet here as a union -- but they are resolved apart,
    against their own offered set, which is the whole point of splitting them.
    `tools=("http_fetch",)` no longer costs a caller `read_file`.

    `None` back means the request narrowed neither, so no allowlist is added at
    all -- which is not the same as an empty one, and the difference is what
    `ToolAllowlist` would enforce.
    """
    if capabilities.builtin_tools == ALL and capabilities.tools == ALL:
        return None
    granted_builtin = narrowed(capabilities.builtin_tools, by=builtin) or ()
    granted_workspace = narrowed(capabilities.tools, by=workspace) or ()
    return (*granted_builtin, *granted_workspace)

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



def _resolve_tools(
    tools_dir: Path,
    capabilities: Capabilities,
    workspace_tools: Sequence[Any],
    assemble: Callable[[tuple[Any, ...]], CompiledStateGraph],
) -> tuple[str, ...] | None:
    """Every tool name this request may call, or `None` for no restriction.

    Costs a throwaway assembly, and only when it has to. Both offered sets must
    be known to resolve `ALL` on either axis, and only one can be read off disk:
    the built-in set is a property of an assembled graph. A request that narrows
    neither axis, in a workspace that defines no tools, skips it entirely.

    The same probe the shadow check has always needed, doing three jobs now
    rather than one.
    """
    if not workspace_tools and capabilities.builtin_tools == ALL and capabilities.tools == ALL:
        return None

    builtin = registered_tools(assemble(()))
    workspace = _workspace_tool_names(workspace_tools, builtin=builtin, directory=tools_dir)
    _refuse_unknown_tools(capabilities, builtin=builtin, workspace=workspace)
    return _permitted_tools(capabilities, builtin=builtin, workspace=workspace)

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
    catalogue: Mapping[str, Path] | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent.

    `session_dir` is where the backend roots, so an agent belongs to one
    session and cannot be reused across them.

    `catalogue` is where the shared skills, subagents and tools are read from,
    as `{"skills": …, "subagents": …, "tools": …}`. Omitted, it is derived from
    `cfg`, which is what it has always been -- the same fallback `model=` takes,
    and for the same reason: derive from `cfg`, or raise, but never invent. A
    deployment staging its definitions somewhere else resolves them once at
    construction and passes the result down.

    deepagents 0.7.6 ships no planning tool, so `TodoListMiddleware` is added
    explicitly. Its default prompt fragment is kept rather than trimmed: it
    already carries anti-overuse guidance and a finishing convention that would
    only be rewritten worse.
    """
    capabilities = capabilities or Capabilities()
    roots = catalogue or cfg.catalogue_roots
    resolved_backend = _backend_for(cfg, session_dir, backend, roots)
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
        if capabilities.skills == ALL:
            extras["skills"] = SKILLS_SOURCES
        elif capabilities.skills is None:
            pass  # none: no index, and no deny rules to write for one
        else:
            available = available_skills(cfg, session_dir, catalogue=roots)
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

    interpreter_at: int | None = None
    if cfg.interpreter_enabled:
        # Unrestricted for now: the probe below has to see `eval` to count it
        # among the built-ins, and the grant is not resolved until after it.
        interpreter_at = len(middleware)
        middleware.append(_interpreter(cfg, None))

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

    workspace_tools = load_tools(roots["tools"])
    permitted = _resolve_tools(roots["tools"], capabilities, workspace_tools, assemble)

    if interpreter_at is not None and permitted is not None:
        # Re-wired now that the union is known. It had to be in place for the
        # probe -- `eval` is a tool, so a request naming it needs it in the
        # enumerated set -- but unrestricted, since the grant was not resolved
        # yet. A caller that withheld the shell must not reach it from code.
        middleware[interpreter_at] = _interpreter(cfg, permitted)

    if capabilities.subagents is not None:
        defined = defined_subagents(cfg, session_dir)
        # `ALL` is every subagent the workspace defines, resolved here because
        # here is where "what it defines" is known.
        activated = tuple(defined) if capabilities.subagents == ALL else capabilities.subagents
        unknown = tuple(n for n in activated if n not in defined)
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
                tools=permitted if permitted is not None else ALL,
                skills=subagent_skills(defined[n], offered, capabilities.skills),
                extra_middleware=subagent_middleware(
                    defined[n], registry, capabilities.middleware
                ),
            )
            for n in activated
        ]

    if permitted is not None:
        middleware.append(ToolAllowlist(permitted))
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
            {**GENERAL_PURPOSE_SUBAGENT, "middleware": [ToolAllowlist(permitted)]}
        )
        extras["subagents"] = supplied
        # Backstop. Only these names are reachable, so a delegate deepagents
        # adds in some future version does not silently arrive unrestricted.
        reachable = tuple(spec["name"] for spec in supplied)
        middleware.append(DeclaredDelegatesOnly(reachable))

    return assemble(tuple(workspace_tools))

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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.config import Config
from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    narrowed,
    refuse_ungranted_models,
)
from kingfisher.domain.ports import CommandRunner
from kingfisher.domain.subagent import RunOn
from kingfisher.domain.tool import Found, Offering
from kingfisher.infrastructure.catalogue import Definitions, source_of
from kingfisher.infrastructure.harness.activation import (
    _activated_subagents,
    _private_skills,
    _skill_denials,
    activatable_skills,
    available_skills,
)
from kingfisher.infrastructure.harness.backend import (
    MEMORY_SOURCES,
    HostPathGuard,
    WorkspaceToolErrors,
    WorkspaceToolPaths,
    build_backend,
    skills_sources,
)
from kingfisher.infrastructure.harness.delegation import (
    TASK_TOOL,
    as_subagent,
    model_object,
    subagent_helpers,
    subagent_skills,
)
from kingfisher.infrastructure.harness.interpreter import _interpreter
from kingfisher.infrastructure.harness.middleware import (
    MiddlewareFactory,
    declared_middleware,
)
from kingfisher.infrastructure.harness.models import build_model
from kingfisher.infrastructure.harness.narrowing import (
    DeclaredDelegatesOnly,
    NarrowedSkills,
    ToolAllowlist,
)
from kingfisher.infrastructure.prompting import system_prompt

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph





#: For a request that declined memory a deployment did wire. Reads are denied
#: rather than the prompt rewritten: the prompt is the cached prefix.
MEMORY_IS_DENIED = FilesystemPermission(
    operations=["read"],
    paths=["/memory/**"],
    mode="deny",
)












#: One entry of a deployment's middleware registry.
#:
#: `Callable` is imported at run time for this line, rather than under
#: `TYPE_CHECKING` with `Mapping` and `Sequence`: this is a module-level
#: assignment rather than an annotation, and `from __future__ import
#: annotations` makes annotations strings while doing nothing for a value.
#:
#: Two shapes, and the ellipsis is the honest way to say so. An entry may be a
#: zero-argument factory, which is what a registry has held since before
#: settings existed; or a class, which `_instantiate` calls with its own
def registered_tools(graph: Any) -> tuple[str, ...] | None:
    """Tool names the compiled agent can actually dispatch, or `None` if unreadable.

    Derived from the graph rather than listed here, because a hardcoded list
    would drift the first time deepagents adds or renames a tool. The path into
    the tool node is not a public contract, so a shape we do not recognise is
    reported rather than raised: taking down every build over an introspection
    detail would be the worse trade, and a rename upstream is meant to fail
    `test_a_real_build_is_readable` instead.

    It used to answer `()` for both "no tools" and "cannot read this", and said
    so -- callers "read [it] as *cannot check*". They were the same answer
    because nothing needed them apart: every graph here is one `build_agent`
    made, and those always dispatch something.

    A compiled subagent is the first graph kingfisher will be handed rather than
    have built, and there the difference is the whole point -- a listing that
    prints "no tools" for a graph it could not read has stated a fact it does
    not have. So `()` now means none, and `None` means unreadable.

    Telling them apart takes a second look, because the obvious one does not
    work: `create_agent(model, tools=[])` compiles to `['__start__', 'model']`
    with **no tool node at all**, which is exactly the shape of a hand-written
    graph that dispatches nothing. Measured, not assumed. What separates them is
    the `model` node -- an agent graph keeps one whether or not it has tools, so
    a tool node missing from *that* shape is a definite none, and anything else
    is a shape with no answer in it.
    """
    nodes = getattr(graph, "nodes", None)
    if not hasattr(nodes, "get"):
        return None
    by_name = getattr(getattr(nodes.get("tools"), "bound", None), "tools_by_name", None)
    if isinstance(by_name, dict):
        return tuple(sorted(by_name))
    return () if "model" in nodes else None


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


# The catalogue is *instructions the agent follows*, which makes it the one
# route where a write outlasts the request that made it. `/memory` and
# `/derived` belong to a session and go when it does; a skill belongs to the
# deployment, and `KINGFISHER_SKILLS_DIR` exists so several deployments can
# share one reviewed set -- so a skill edited during one request is read by
# every later request, in every deployment pointing at that directory.
#
# Measured before adding, because `/data` had a rule and this did not:
# `backend.write("/skills/demo/PWNED.md", ...)` and `backend.edit(...)` both
# succeeded against the catalogue on disk. Nothing depended on it -- a request's
# own skills are written host-side by `uploads`, never through a file tool.
#
# `/skills/uploaded/**` is covered too, and deliberately. It is a session's own
# half rather than the deployment's, but kingfisher writes it host-side for the
# same reason, and an agent able to rewrite an uploaded skill could rewrite the
# instructions it was about to follow.
#
# Same `write` operation as above, so it covers write_file, edit_file and
# delete. It does not cover `execute` either -- which is why
# `confinement.resolve` denies writes to the same directory in the sandbox
# profile. Both halves are needed and neither is sufficient: the profile is
# macOS-only and can be switched off, and this one never sees the shell.
SKILLS_ARE_READ_ONLY = FilesystemPermission(
    operations=["write"],
    paths=["/skills/**"],
    mode="deny",
)






def _backend_for(
    cfg: Config,
    session_dir: Path | None,
    backend: Any | None,
    catalogue: Definitions,
    runner: CommandRunner | None = None,
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
        return build_backend(cfg, session_dir, catalogue=catalogue, runner=runner)
    msg = "build_agent needs either a session_dir to root a backend at, or a backend"
    raise ValueError(msg)


def workspace_tool_names(
    cfg: Config, *, catalogue: Definitions | None = None
) -> tuple[str, ...]:
    """The tools this workspace defines, as a grant would write them.

    Knowable off disk, unlike the built-in set. That asymmetry is why the two
    axes resolve differently and why only one of them needs a probe.

    Written forms rather than bare names, because a bare list said `fetch,
    fetch` once two files could each define one -- which read as a workspace
    with a stutter rather than two tools a grant has to choose between.
    """
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return tuple(sorted(Offering.of(found).workspace))


def _refuse_shadowed(
    walked: Sequence[Found], *, builtin: tuple[str, ...], where: str
) -> None:
    """What the workspace defines, refusing anything that shadows a built-in.

    `tools_by_name` is a dict, so a workspace tool called `read_file` would take
    the name in silence and the real one would simply stop existing -- the same
    "quietly different from what you asked for" failure the capability checks
    refuse elsewhere. It matters more now that the two are granted separately: a
    shadowed name would be permitted by one axis and enforced as the other.

    Takes where they came from as text rather than the `Config` it used to
    derive a directory from: the only use is naming the place to go and rename
    them, and a catalogue that is not a directory can still say where it is.
    """
    shadowed = tuple(sorted({one.name for one in walked} & set(builtin)))
    if shadowed:
        msg = (
            f"workspace tool(s) {', '.join(shadowed)} would replace a built-in of "
            f"the same name; rename them in {where}"
        )
        raise CapabilityError(msg)


@dataclass(frozen=True)
class _ToolSurface:
    """The tool picture one build works from, resolved once.

    Four values rather than one flat allowlist, because a *delegate* narrows
    the two axes separately and cannot do that from their union: `tools:
    [http_fetch]` must cost it no built-in, which is only expressible while the
    halves are still apart. `permitted` puts them back together for the
    parent's own allowlist, which is one flat list by the time it reaches
    `ToolAllowlist`.

    The default is the skipped probe: nothing narrowed, nothing offered known,
    and `permitted` `None` for "no allowlist at all". Safe because the probe is
    only skipped when no definition names a tool either, so the `ALL`s below
    are never asked to enumerate anything.
    """

    #: What this build has to offer, or `None` when the probe was skipped.
    #:
    #: `None` rather than an empty `Offering`, because the two mean opposite
    #: things: nothing offered would narrow every grant to nothing, while a
    #: skipped probe means nothing was ever narrowed. The probe is only skipped
    #: when no definition names a tool either, so nothing below is ever asked to
    #: enumerate what it does not know.
    offering: Offering | None = None
    #: What the request asked for. Held so the grants can be *derived* rather
    #: than stored beside what they came from -- which is what this dataclass
    #: used to do, and it needed an `unrestricted` flag to compensate, because a
    #: stored `ALL` could no longer say whether everything was granted or
    #: nothing was narrowed. `permitted` answers that from the request directly.
    asked: Capabilities = field(default_factory=Capabilities)
    #: The built tool *objects*, by name, taken off the probe graph. Needed
    #: only for a helper: `SubAgentMiddleware` registers what a spec carries,
    #: and these are constructed from the backend inside `create_deep_agent`
    #: where nothing here can reach them -- except off an assembled graph,
    #: which is what the probe already is.
    objects: Mapping[str, Any] = field(default_factory=dict)
    #: Every workspace tool with the file it came from, kept because a grant no
    #: longer resolves to a name. Two files may each define a `fetch`, so what
    #: an agent registers has to be chosen as *objects* -- a name would pick one
    #: of the two out of a dictionary and lose the other before any narrowing
    #: ran.
    found: tuple[Found, ...] = ()

    @property
    def carried(self) -> tuple[Any, ...]:
        """The tool objects this agent registers: granted, minus the ambiguous.

        A delegate names which `fetch` it wants and gets it. The agent holding
        the grant cannot name anything -- it dispatches by name -- so a pair it
        cannot tell apart is left out and `ambiguous` says which.
        """
        if self.offering is None:
            return tuple(one.tool for one in self.found)
        return tuple(
            one.tool for one in self.offering.carried(self.granted_workspace, self.found)
        )

    @property
    def ambiguous(self) -> tuple[str, ...]:
        """Names granted to this run that only a delegate can ask for."""
        if self.offering is None:
            return ()
        return self.offering.ambiguous(self.granted_workspace, self.found)

    @property
    def offers(self) -> Offering:
        """The offering, or an empty one for the callers that only read names."""
        return self.offering or Offering()

    @property
    def permitted(self) -> tuple[str, ...] | None:
        """The parent's allowlist, or `None` for no restriction at all."""
        if self.offering is None:
            return None
        return self.offering.permitted(self.asked.builtin_tools, self.asked.tools)

    @property
    def granted_builtin(self) -> Selection:
        """The request's built-in grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        return narrowed(self.asked.builtin_tools, by=self.offering.builtin) or ()

    @property
    def granted_workspace(self) -> Selection:
        """The request's workspace grant, resolved against what was offered."""
        if self.offering is None:
            return ALL
        # `spelt` first, for the reason it exists: `narrowed` is set membership
        # and would drop a long-form grant without a word.
        return narrowed(self.offering.spelt(self.asked.tools), by=self.offering.workspace) or ()




def _private_tools(catalogue: Definitions, name: str) -> tuple[Found, ...]:
    """The tools a delegate brings itself, or none.

    A lookup rather than a walk: `Definitions.bundled_tools` imported these at
    startup, so a broken one has already failed by the time any of this runs and
    what is left here cannot raise.

    Empty for every delegate without a bundle, which is every delegate today --
    so a deployment that writes none pays a dictionary lookup per activated
    subagent and nothing else.
    """
    repository = catalogue.bundled_tools.get(name)
    return tuple(repository.found) if repository is not None else ()


def _tool_objects(graph: Any) -> Mapping[str, Any]:
    """The built tool objects a compiled graph dispatches, by name.

    `registered_tools` reads the same dict for its keys; a helper needs the
    values. `task` is excluded deliberately and not for tidiness: the harvested
    one is bound to *this* graph's delegate list, so handing it to a helper
    would let the helper reach every delegate the parent can. A delegate that
    may consult one gets a fresh `task` from its own `SubAgentMiddleware`.
    """
    node = getattr(graph, "nodes", {}).get("tools")
    by_name = getattr(getattr(node, "bound", None), "tools_by_name", None)
    if not isinstance(by_name, dict):
        return {}
    return {name: tool for name, tool in by_name.items() if name != TASK_TOOL}


def _wanted_endpoints(
    run_on: Mapping[str, RunOn] | None, activated: tuple[str, ...], granted: Selection
) -> Mapping[str, RunOn]:
    """Where this request wants delegates to run, once it may say so.

    Two refusals, both before anything is built and both raising rather than
    dropping. Elsewhere a narrower caller is quietly given less, because less
    is what they asked for; here the caller asked for the *cheap* model, and
    silently giving them the expensive one is the outcome nobody wants and
    nobody sees. Naming a delegate this request never activated is the same
    kind of mistake as naming an unknown tool.
    """
    wanted = dict(run_on or {})
    if stray := tuple(n for n in wanted if n not in activated):
        msg = (
            f"run_on names subagent(s) this request did not activate: "
            f"{', '.join(sorted(stray))}; it activated {activated}"
        )
        raise CapabilityError(msg)
    refuse_ungranted_models(
        (where.model for where in wanted.values()), granted=granted, subject="run_on"
    )
    return wanted




def _resolve_tools(
    # answers came from; folding them up would hide what each one is for.
    where: str,
    capabilities: Capabilities,
    workspace_tools: Sequence[Found],
    assemble: Callable[[tuple[Any, ...]], CompiledStateGraph],
    *,
    names_needed: bool = False,
) -> _ToolSurface:
    """What this request may call, and what this agent offers at all.

    Costs a throwaway assembly, and only when it has to. Both offered sets must
    be known to resolve `ALL` on either axis, and only one can be read off disk:
    the built-in set is a property of an assembled graph. A request that narrows
    neither axis, in a workspace that defines no tools, skips it entirely.

    `names_needed` is the fourth job: a *definition* naming tools has to be
    checked against what exists, and what exists includes the built-in set. A
    caller asks for it only when some activated definition actually names one.
    Measured on a build with one delegate: 1 compile and 12.6ms when it names
    no tools, 2 and 20.3ms when it does -- so the probe is the ~7.7ms, and the
    runs that never needed it still skip it.

    The same probe the shadow check has always needed, doing four jobs now
    rather than one.
    """
    unrestricted = capabilities.builtin_tools == ALL and capabilities.tools == ALL
    if not names_needed and not workspace_tools and unrestricted:
        return _ToolSurface()

    probe = assemble(())
    # Our own probe, so `None` is not reachable here; `or ()` keeps a shape
    # change upstream from becoming a crash at the one site that would.
    builtin = registered_tools(probe) or ()
    _refuse_shadowed(workspace_tools, builtin=builtin, where=where)
    offering = Offering.of(workspace_tools, builtin=builtin)
    offering.refuse_unknown(
        capabilities.builtin_tools, capabilities.tools, subject="this request"
    )
    return _ToolSurface(
        offering=offering,
        asked=capabilities,
        objects=_tool_objects(probe),
        found=tuple(workspace_tools),
    )


def _running(
    agent: AgentSpec | None, cfg: Config, endpoints: Selection, injected: Any
) -> Any:
    """The model instance this agent's graph is built on.

    It returned the model *id* beside the instance until `distinct` went. The id
    was what a delegate refusing to match its caller was measured against, and
    nothing tracks the caller chain any more -- `indistinct` still reports, and
    has always compared against the deployment's default rather than the
    caller.

    An injected model still wins. A caller handing one in has said the catalogue
    is not the subject -- but the *id* is still the agent's, because what the
    file asked for is what a delegate should be judged against either way.

    Lifted out of `build_agent` rather than left inline, and not only for the
    branch count: the composition root is one statement per thing attached to
    the graph, and this was three working out one value between them.
    """
    if agent is None:
        return injected or build_model(*cfg.models.resolve())
    mine = model_object(agent, cfg, endpoints=endpoints)
    return injected or mine or build_model(*cfg.models.resolve())


def build_agent(  # noqa: PLR0913, PLR0915, PLR0912 -- the composition root; each
    # branch is one collaborator being absent, counted a different way
    # is one injectable collaborator, and the body is the wiring itself: every
    # statement attaches one thing to the graph, so splitting it would move the
    # wiring somewhere a reader has to go and find rather than shortening it.
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
    session_dir: Path | None = None,
    middleware_registry: Mapping[str, MiddlewareFactory] | None = None,
    model: Any | None = None,
    backend: Any | None = None,
    runner: CommandRunner | None = None,
    checkpointer: Any | None = None,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
    workspace_tools: Sequence[Found] | None = None,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent.

    `session_dir` is where the backend roots, so an agent belongs to one
    session and cannot be reused across them.

    `catalogue` is where the shared agents, skills, subagents and tools are read
    from, as `{"agents": …, "skills": …, …}`. Omitted, it is derived from
    `cfg`, which is what it has always been -- the same fallback `model=` takes,
    and for the same reason: derive from `cfg`, or raise, but never invent. A
    deployment staging its definitions somewhere else resolves them once at
    construction and passes the result down.

    deepagents 0.7.6 ships no planning tool, so `TodoListMiddleware` is added
    explicitly. Its default prompt fragment is kept rather than trimmed: it
    already carries anti-overuse guidance and a finishing convention that would
    only be rewritten worse.
    """
    # The agent file is the baseline and the request only ever subtracts from
    # it. One lattice, applied in the one direction it already goes: what a
    # caller asks for cannot exceed what the definition declared.
    #
    # Written as an expression rather than an `if`, and not for the branch
    # count alone: `Capabilities().intersect(asked)` is not the identity -- it
    # would zero `models`, whose default is `None` -- so there is no neutral
    # left-hand side to fold this into.
    asked = capabilities or Capabilities()
    capabilities = agent.declares(held).intersect(asked) if agent is not None else asked
    roots = catalogue or Definitions.from_config(cfg)
    resolved_backend = _backend_for(cfg, session_dir, backend, roots, runner)
    # Unconditional: the backend rejects host paths on every run, so the
    # thing that turns that rejection into a correction must always be here.
    middleware: list[Any] = [TodoListMiddleware(), HostPathGuard()]
    permissions = [DATA_IS_READ_ONLY, SKILLS_ARE_READ_ONLY]
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
        registry = activatable_skills(cfg, session_dir, catalogue=roots)
        # One source per folder, so a skill below the top level is visible at
        # all -- and labelled the way the registry labelled it, because a label
        # is the first half of what a request grants.
        sources = skills_sources(registry.folders)
        if capabilities.skills == ALL:
            extras["skills"] = sources
        elif capabilities.skills is None:
            pass  # none: no index, and no deny rules to write for one
        else:
            # Each grant to the one skill it means. A bare name that two sources
            # both offer is refused here rather than resolved, because resolving
            # it is exactly the silent pick this exists to stop.
            activated = tuple(registry.resolve(one) for one in capabilities.skills)
            # Supplied as middleware rather than via `skills=`: passing that
            # argument makes deepagents construct its own SkillsMiddleware,
            # leaving no way to substitute a filtered one.
            middleware.append(
                NarrowedSkills(
                    allowed=activated,
                    backend=resolved_backend,
                    sources=sources,
                )
            )
            permissions.extend(_skill_denials(activated, registry))

    interpreter_at: int | None = None
    if cfg.interpreter_enabled:
        # Unrestricted for now: the probe below has to see `eval` to count it
        # among the built-ins, and the grant is not resolved until after it.
        interpreter_at = len(middleware)
        middleware.append(_interpreter(cfg, None))

    running = _running(agent, cfg, capabilities.endpoints, model)

    def assemble(extra_tools: tuple[Any, ...]) -> CompiledStateGraph:
        return create_deep_agent(
            model=running,
            backend=resolved_backend,
            system_prompt=system_prompt(cfg, agent.system_prompt if agent else ""),
            middleware=middleware,
            permissions=permissions,
            checkpointer=checkpointer,
            tools=list(extra_tools) or None,
            **extras,
        )

    # The catalogue walked these when the deployment was wired; a caller that
    # has already walked them itself -- `--list` -- still wins.
    walked = tuple(roots.tools.found if workspace_tools is None else workspace_tools)

    # Appended here rather than beside `HostPathGuard` above, because it needs
    # the names and they are not known until now. `assemble` closes over the
    # list, so anything added before it runs is in the built agent.
    #
    # Every walked tool, not the granted ones: a request that activated none of
    # them cannot reach one, and narrowing this to the grant would mean building
    # the guard from a set that is computed after it.
    if walked:
        middleware.append(WorkspaceToolErrors(frozenset(entry.name for entry in walked)))
        # And the same set gets its paths translated, when there is a session to
        # translate against. A build with no session -- `inventory` reading the
        # built-in tool set off a compiled graph -- has no root to resolve to and
        # no turn to protect.
        if session_dir is not None:
            middleware.append(
                WorkspaceToolPaths(frozenset(entry.name for entry in walked), session_dir)
            )

    defined, activated = _activated_subagents(cfg, capabilities, session_dir, catalogue=roots)
    surface = _resolve_tools(
        source_of(roots.tools),
        capabilities,
        walked,
        assemble,
        # Either list naming anything is enough: both are checked against their
        # own offered set, and neither set is knowable without the probe.
        # Either tool list naming anything needs the offered sets. A delegate
        # naming a helper needs the built tool *objects*, which come off the
        # same probe -- so wanting one is equally a reason to run it.
        names_needed=any(
            defined[n].tools not in (ALL, None)
            or defined[n].builtin_tools not in (ALL, None)
            or defined[n].subagents is not None
            for n in activated
        ),
    )
    permitted = surface.permitted

    if interpreter_at is not None and permitted is not None:
        # Re-wired now that the union is known. It had to be in place for the
        # probe -- `eval` is a tool, so a request naming it needs it in the
        # enumerated set -- but unrestricted, since the grant was not resolved
        # yet. A caller that withheld the shell must not reach it from code.
        middleware[interpreter_at] = _interpreter(cfg, permitted)

    # Both kinds read it, so it is resolved before either branch. It used to
    # be bound inside the delegates' block, which meant an agent with no
    # delegates never reached a registry at all.
    registry = middleware_registry or {}

    if capabilities.subagents is not None:
        offered = available_skills(cfg, session_dir, catalogue=roots)
        for name in activated:
            subject = f"subagent {name!r}"
            surface.offers.refuse_unknown(
                defined[name].builtin_tools, defined[name].tools, subject=subject
            )
            # After the unknown-name check, so a definition naming `csv_column`
            # hears that the name is wrong rather than that it has moved. The
            # catalogue's own definitions had their paths checked at
            # construction; this is what covers one a request uploaded.
            surface.offers.refuse_moved(defined[name].tool_sources, subject=subject)

        wanted = _wanted_endpoints(run_on, activated, capabilities.models)

        def _built(
            name: str,
            *,
            helpers: list[Any] | None = None,
            default_model: Any = None,
            tool_objects: list[Any] | None = None,
        ) -> dict[str, Any]:
            """One delegate, with the request's ceiling on every axis.

            `helpers` is whatever this delegate names, built first. The
            recursion is the feature: delegation nests to any depth, and what
            stops it running forever is `refuse_cycles` on the catalogue rather
            than a bound here. This used to omit `helpers` for a helper, and
            that omission *was* the depth bound.

            A helper is otherwise built exactly like any other delegate: its own
            tools, its own skills, its own endpoint, each clamped by what the
            *request* granted rather than by the delegate that reached it. The
            caller had to name it too, so the caller has already seen it.

            """
            return as_subagent(
                defined[name],
                cfg,
                backend=resolved_backend,
                endpoints=capabilities.endpoints,
                builtin_tools=surface.granted_builtin,
                tools=surface.granted_workspace,
                skills=subagent_skills(defined[name], offered, capabilities.skills),
                skill_sources=skills_sources(roots.registry.folders),
                helpers=helpers,
                default_model=default_model,
                tool_objects=tool_objects,
                catalogue=walked,
                # Its own, if it has a folder named after it. Looked up by the
                # key a grant uses, which is what `bundled_tools` is keyed by,
                # so a qualified `analysis/surveyor.yaml::surveyor` finds its
                # bundle and a bare `surveyor` finds its own.
                private=_private_tools(roots, name),
                private_skills=_private_skills(roots, name),
                run_on=wanted.get(name),
                extra_middleware=declared_middleware(
                    defined[name], registry, capabilities.middleware, kind="subagent"
                ),
            )

        # One compiled agent per definition, however many places it appears.
        # Not an optimisation: compiling per *path* is exponential in the shape
        # of the catalogue, and a catalogue with no cycle at all can describe an
        # enormous number of paths. Measured -- 15 definitions each naming three
        # is 6,872 compilations and seven seconds, twenty is two and a half
        # minutes. Compiled once each, the same catalogue is twenty.
        #
        # Safe because `refuse_cycles` already ran: a definition cannot be
        # in-flight when it is asked for again, so this needs no re-entry guard.
        # Keyed by name *and position*, because the two are not the same agent.
        # A delegate the request activated is registered by `create_deep_agent`
        # and inherits its model and its built-in tools; one nested inside
        # another is built by `SubAgentMiddleware` and inherits nothing --
        # deepagents refuses a nested spec with no `model` outright.
        #
        # So a definition used in both places compiles twice, which is still
        # two per definition rather than one per path. Handing the explicit
        # model and tools to a top-level delegate instead would work and would
        # cost it the inheritance: it would stop tracking a parent that changed.
        #
        # What it inherits is part of the key for the same reason position is: a
        # definition naming no model runs whatever reached it, so `checker` under
        # a cheap parent and `checker` under an expensive one are two different
        # agents wearing one name. Bounded by definitions times the models above
        # them, which is a catalogue's own shape rather than the number of paths
        # through it.
        #
        # By identity, and that is the whole of what changed when `distinct`
        # went. The key held the summoner's model *id*, which `model_for` was
        # called early to obtain -- a call that existed for `distinct` and went
        # with it. The object is what actually distinguishes two delegates here:
        # one is built per parent, so two parents are two objects, and a
        # top-level delegate inherits `None` every time.
        compiled: dict[tuple[str, bool, int], Any] = {}

        # What the main agent itself runs, as an object a helper can be handed.
        # A top-level delegate needs none of this -- deepagents gives it the
        # agent's own model -- but `SubAgentMiddleware` gives a nested one
        # nothing, and deepagents refuses a nested spec with no model at all.
        root = running

        def _with_helpers(name: str, *, nested: bool, inherited: Any = None) -> Any:
            key = (name, nested, id(inherited))
            if key not in compiled:
                override = wanted.get(name)
                mine = model_object(
                    defined[name],
                    cfg,
                    endpoints=capabilities.endpoints,
                    run_on=override,
                    inherited=inherited,
                )
                helpers = [
                    _with_helpers(
                        helper,
                        nested=True,
                        # Its parent's model, which is what "runs whatever
                        # summoned it" means one level down. This was the main
                        # agent's, so a helper under a delegate pinned to the
                        # cheap model quietly ran the expensive one.
                        inherited=mine if mine is not None else root,
                    )
                    for helper in subagent_helpers(
                        defined[name], defined, capabilities.subagents
                    )
                ]
                compiled[key] = _built(
                    name,
                    helpers=helpers or None,
                    default_model=inherited if nested else None,
                    tool_objects=list(surface.objects.values()) if nested else None,
                )
            return compiled[key]

        extras["subagents"] = [
            _with_helpers(n, nested=False) for n in activated
        ]

    def deployment_middleware() -> list[Any]:
        """What this deployment's registry owes one graph, freshly built.

        Called once per graph rather than shared, which is what `as_subagent`
        already does for a declared delegate -- the factory runs again for each
        one. It is a real choice and the other reading is defensible: one shared
        rate limiter bounds a whole turn where one per graph bounds each. This
        follows the delegates; changing it should change it for them too.
        """
        if agent is None:
            return []
        return declared_middleware(agent, registry, capabilities.middleware, kind="agent")

    if permitted is not None:
        middleware.append(ToolAllowlist(permitted))

    # deepagents supplies a `general-purpose` delegate with "the same
    # capabilities as the main agent" and none of our middleware, present
    # whenever `task` is. Supplying one by the same name *replaces* it -- the
    # specs are keyed by name -- so it keeps working and arrives with the
    # caller's ceiling on it, rather than being withheld.
    #
    # Their spec, our middleware: the description and prompt are tuned and there
    # is no reason to reinvent either.
    #
    # Unconditional now, where it used to happen only for a request that
    # narrowed something. That was right while the ceiling was the only thing
    # being attached -- an unrestricted request has no ceiling to attach. It
    # stopped being right when the deployment's own middleware went on too:
    # that is owed to every run, and left as it was, the way to run unaudited
    # was to ask for nothing in particular. `DeclaredDelegatesOnly` moves with
    # it for a reason of its own, written below.
    supplied = list(extras.get("subagents", ()))
    supplied.append(
        {
            **GENERAL_PURPOSE_SUBAGENT,
            "middleware": (
                ([ToolAllowlist(permitted)] if permitted is not None else [])
                + deployment_middleware()
            ),
        }
    )
    extras["subagents"] = supplied
    # Backstop. Only these names are reachable, so a delegate deepagents adds in
    # some future version does not silently arrive unrestricted -- a reason that
    # has nothing to do with whether this caller narrowed anything, though it
    # was wired only when they had.
    reachable = tuple(spec["name"] for spec in supplied)
    middleware.append(DeclaredDelegatesOnly(reachable))

    # The agent's own, and last -- the same placement `as_subagent` gives a
    # delegate's, for the same reason: a deployment's middleware should see the
    # tool and skill narrowing kingfisher applied rather than run ahead of it.
    #
    # `capabilities.middleware` is already this agent's names narrowed by the
    # request, since `declares` folded them in above. Passing both halves is
    # not circular: `approved_middleware` refuses a name the request subtracted
    # rather than running with less than the definition asked for, which is the
    # difference between an audit hook that is off and one nobody knows is off.
    middleware.extend(deployment_middleware())

    return assemble(surface.carried)

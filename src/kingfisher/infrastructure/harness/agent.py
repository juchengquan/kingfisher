"""Agent assembly: kingfisher's configuration and grants into a deepagents graph.

Two jobs it used to do live elsewhere now, because at 657 lines it was doing four.
`prompting` assembles the system prompt -- moved out because it needs nothing foreign,
and sharing a file with `create_deep_agent` cost every consumer of `system_prompt` 764ms
and three provider SDKs. Resolving what a delegate runs with is
`infrastructure.harness.subagents`, beside this file. Neither calls anything here.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, create_deep_agent
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain.agents.middleware import TodoListMiddleware

from kingfisher.config import Config
from kingfisher.domain.capabilities import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    refuse_ungranted_models,
)
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.activation import (
    _activated_subagents,
    _private_skills,
    _skill_denials,
    activatable_skills,
)
from kingfisher.infrastructure.harness.backend import (
    MEMORY_SOURCES,
    default_backend,
    skills_sources,
    tool_guards,
)
from kingfisher.infrastructure.harness.backend_contract import refuse_unusable_backend
from kingfisher.infrastructure.harness.interpreter import _interpreter
from kingfisher.infrastructure.harness.middleware import (
    ByName,
    MiddlewareFactory,
    declared_middleware,
    offered_middleware,
)
from kingfisher.infrastructure.harness.models import build_model, model_named
from kingfisher.infrastructure.harness.narrowing import (
    DeclaredDelegatesOnly,
    NarrowedSkills,
    ToolAllowlist,
)
from kingfisher.infrastructure.harness.subagents import (
    as_subagent,
    model_object,
    subagent_helpers,
    subagent_skills,
)
from kingfisher.infrastructure.harness.tools import (
    _private_tools,
    _resolve_tools,
    registered_tools,
)
from kingfisher.infrastructure.prompting import system_prompt
from kingfisher.infrastructure.sandbox.confinement import EXTERNAL
from kingfisher.kinds.agents.spec import AgentSpec
from kingfisher.kinds.subagents.spec import RunOn
from kingfisher.kinds.tools.spec import Found
from kingfisher.layout import denied_read_scopes, denied_scopes

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


def read_only_permissions() -> list[FilesystemPermission]:
    """A deny rule for every scope the layout refuses writes under.

    Deduplicated there, so `/skills/**` is one rule covering the catalogue and every
    bundle. One rule per *mount* would make the count
    depend on how many bundles a catalogue ships, for no gain.

    **What this does not reach is the shell.** Filesystem permissions are applied by
    `FilesystemMiddleware` at the tool level, so they cover `write_file`, `edit_file`
    and `delete` -- `FilesystemOperation` is read|write and delete maps to write --
    and `execute` bypasses them entirely. The other half of each rule is elsewhere
    and neither half is sufficient: `infrastructure.workspace.permissions` drops the
    write bits under `/data`, and `confinement.resolve` denies writes to the skills
    directory in the sandbox profile, which is macOS-only and can be switched off.
    """
    return [
        FilesystemPermission(operations=["write"], paths=[scope], mode="deny")
        for scope in denied_scopes()
    ] + [
        FilesystemPermission(operations=["read"], paths=[scope], mode="deny")
        for scope in denied_read_scopes()
    ]


def _backend_for(
    cfg: Config,
    session_dir: Path | None,
    backend: Any | None,
    catalogue: Definitions,
) -> Any:
    """The filesystem an agent sees: supplied ready-made, or kingfisher's own rooted
    at a session.

    A deployment reaches this through `Kingfisher`, which calls its factory itself and
    arrives here with the backend already made -- runner and all, since the runner is
    the factory's argument now. What is left is the harness's own two callers --
    `--list` and this repository's tests -- which have a session, want the default
    built for them, and have never had a runner to hand it.
    """
    if backend is not None:
        built = backend
    elif session_dir is not None:
        built = default_backend(cfg, session_dir, catalogue=catalogue)
    else:
        msg = "build_agent needs either a session_dir to root a backend at, or a backend"
        raise ValueError(msg)

    # Every path that resolves one, the default included, and that is the point: a
    # check only a supplied backend ran would be a check nothing here exercises.
    refuse_unusable_backend(built)
    return built


def _wanted_endpoints(
    run_on: Mapping[str, RunOn] | None, activated: tuple[str, ...], granted: Selection
) -> Mapping[str, RunOn]:
    """Where this request wants delegates to run, once it may say so."""
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


def _running(
    agent: AgentSpec | None, cfg: Config, endpoints: Selection, injected: Any
) -> Any:
    """The model instance this agent's graph is built on.

    It returned the model *id* beside the instance until `distinct` went. The id was
    what a delegate refusing to match its caller was measured against, and nothing
    tracks the caller chain any more -- `indistinct` still reports, and has always
    compared against the deployment's default rather than the caller.
    """
    if agent is None:
        return injected or build_model(*cfg.models.resolve())
    mine = model_object(agent, cfg, endpoints=endpoints)
    return injected or mine or build_model(*cfg.models.resolve())


def builtin_tool_names(
    cfg: Config, catalogue: Definitions, workspace_tools: Sequence[Found] | None = None
) -> tuple[str, ...] | None:
    """The built-in set, which is only knowable from an assembled graph.

    Here rather than in `harness/tools.py`, where the rest of the tool surface lives:
    this has to call `build_agent`, and that module is imported *by* this one, so the
    edge would close a cycle -- which `test_no_module_in_the_package_can_reach_itself`
    now refuses outright.

    It is a *check* as well as a question, whoever calls it: assembling the graph
    refuses a workspace tool wearing a built-in's name. `workspace_tools` does not
    decide that -- `build_agent` falls back to the catalogue's own when it is `None`
    -- it is there for a caller that has already walked them and would rather not
    hand the decision to somebody else.
    """
    import tempfile  # noqa: PLC0415 -- one caller, and only on the branch that probes

    from kingfisher.infrastructure.workspace.sessions import (  # noqa: PLC0415
        ensure_session_layout,
    )

    with tempfile.TemporaryDirectory(prefix="kingfisher-builtin-") as scratch:
        return registered_tools(
            build_agent(
                # `EXTERNAL` because this shell never runs a command, and any other
                # mode warns that it is unconfined -- which `doctor` and `list` would
                # then print on top of their own report, for a shell nothing uses.
                replace(cfg, shell_sandbox=EXTERNAL),
                session_dir=ensure_session_layout(Path(scratch)),
                catalogue=catalogue,
                workspace_tools=workspace_tools,
                # No delegates. What a workspace *offers* is answered from the
                # catalogue by the caller; this build exists solely to read the
                # built-in tool set off a compiled graph, and wiring a roster to
                # do it would make a listing refuse the very things it is meant
                # to report -- two definitions of a name are printed, not raised.
                capabilities=Capabilities(subagents=None),
            )
        )


def build_agent(  # noqa: PLR0913, PLR0915 -- the composition root; each parameter
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
    checkpointer: Any | None = None,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
    workspace_tools: Sequence[Found] | None = None,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> CompiledStateGraph:
    """Wire model, backend and checkpointer into a deep agent."""
    # The agent file is the baseline and the request only ever subtracts from it. One
    # lattice, applied in the one direction it already goes: what a caller asks for
    # cannot exceed what the definition declared.
    asked = capabilities or Capabilities()
    capabilities = agent.declares(held).intersect(asked) if agent is not None else asked
    roots = catalogue or Definitions.from_config(cfg)
    resolved_backend = _backend_for(cfg, session_dir, backend, roots)
    # The catalogue walked these when the deployment was wired; a caller that
    # has already walked them itself -- `--list` -- still wins. Walked before the
    # stack rather than beside the tools below, because `tool_guards` needs the
    # names and every graph built here gets the same three.
    walked = tuple(roots.tools.found if workspace_tools is None else workspace_tools)
    # Every walked tool, not the granted ones: a request that activated none of
    # them cannot reach one, and narrowing this to the grant would mean building
    # the guard from a set that is computed after it.
    held = frozenset(entry.name for entry in walked)
    middleware: list[Any] = [TodoListMiddleware(), *tool_guards(held, session_dir)]
    permissions = read_only_permissions()
    extras: dict[str, Any] = {}

    # One answer to "are skills on", for this agent and for every delegate it
    # builds. `cfg` says what is wired and the request says what it wants of that --
    # and the delegate branch below asked only the request, so a deployment with
    # skills switched off still handed a delegate an index over a route it was
    # never meant to reach.
    skills_on = cfg.skills_enabled

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

    if skills_on:
        registry = activatable_skills(cfg, catalogue=roots)
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

    def _model_named(written: str, subject: str) -> Any:
        """A model named beside a middleware, under this request's ceiling."""
        return model_named(written, cfg, endpoints=capabilities.endpoints, subject=subject)

    def _provisions(runs: Any, definition: Any) -> dict[str, Any]:
        """What a class that declared `wants` is filled in from, for one graph.

        Both call sites hand over the same keys and different values -- a delegate's
        model is its own, not this agent's -- so a `middlewares:` line means the same
        thing in an agent file and a subagent file. One builder rather than two, so
        they cannot drift; a second mapping written inline at one of them is what
        `test_both_kinds_are_handed_the_same_things_to_want` would catch.
        """
        return {
            "model": ByName(runs, resolve=_model_named),
            "backend": resolved_backend,
            "definition": definition,
        }

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

    defined, activated = _activated_subagents(cfg, capabilities, catalogue=roots)
    surface = _resolve_tools(
        str(roots.tools.root) if roots.tools.root is not None else "the catalogue",
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
    #
    # Two sources since `middlewares/` became a definition kind: what this
    # deployment wired in its own program, and what its workspace defines.
    # Merged once here for the same reason it was hoisted -- an agent and its
    # delegates must select from one mapping, or a name would mean different
    # things one level apart.
    registry = offered_middleware(middleware_registry or {}, roots.middlewares)

    if capabilities.subagents is not None:
        # The registry rather than its `names`: a delegate's grant has to be
        # resolved to the skill it means, not merely recognised as a word.
        offered = activatable_skills(cfg, catalogue=roots)
        for name in activated:
            subject = f"subagent {name!r}"
            surface.offers.refuse_unknown(
                defined[name].builtin_tools, defined[name].tools, subject=subject
            )
            # After the unknown-name check, so a definition naming `csv_column`
            # hears that the name is wrong rather than that it has moved.
            surface.offers.refuse_moved(defined[name].tool_sources, subject=subject)

        wanted = _wanted_endpoints(run_on, activated, capabilities.models)

        def _built(
            name: str,
            *,
            # This delegate's model, not the agent's -- see `_with_helpers`, which
            # is the only caller and the only place the difference is known.
            runs: Any,
            helpers: list[Any] | None = None,
            default_model: Any = None,
            tool_objects: list[Any] | None = None,
        ) -> dict[str, Any]:
            """One delegate, with the request's ceiling on every axis."""
            return as_subagent(
                defined[name],
                cfg,
                backend=resolved_backend,
                endpoints=capabilities.endpoints,
                builtin_tools=surface.granted_builtin,
                tools=surface.granted_workspace,
                # Behind the deployment's switch, like the agent's own index above:
                # a delegate asked only the request before this, so a workspace with
                # skills off still handed one an index of the shared catalogue.
                skills=(
                    subagent_skills(defined[name], offered, capabilities.skills)
                    if skills_on
                    else None
                ),
                skill_sources=skills_sources(roots.registry.folders) if skills_on else None,
                helpers=helpers,
                default_model=default_model,
                tool_objects=tool_objects,
                catalogue=walked,
                # Its own, if it has a folder named after it. Looked up by the
                # key a grant uses, which is what `bundled_tools` is keyed by,
                # so a qualified `analysis/surveyor.yaml::surveyor` finds its
                # bundle and a bare `surveyor` finds its own.
                private=_private_tools(roots, name),
                # Not behind the switch: a bundle is the delegate's own folder rather
                # than the catalogue the switch is about, and the backend mounts its
                # route either way -- withholding the index here would leave the files
                # reachable and unnamed.
                private_skills=_private_skills(roots, name),
                # Reaches only a compiled delegate, whose tools are wrapped against it.
                # An assembled one is given the same translation as middleware.
                session_dir=session_dir,
                run_on=wanted.get(name),
                extra_middleware=declared_middleware(
                    defined[name],
                    registry,
                    capabilities.middlewares,
                    kind="subagent",
                    provisions=_provisions(runs, defined[name]),
                ),
            )

        # One compiled agent per definition, however many places it appears. Not an
        # optimisation: compiling per *path* is exponential in the shape of the
        # catalogue, and a catalogue with no cycle at all can describe an enormous
        # number of paths. Measured -- 15 definitions each naming three is 6,872
        # compilations and seven seconds, twenty is two and a half minutes. Compiled
        # once each, the same catalogue is twenty.
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
                    # What this delegate actually runs: its own model, or the model
                    # of whoever summoned it when it names none and inherits none.
                    # `as_subagent` leaves `model` off the spec in that case and
                    # deepagents hands it the parent's, so a middleware told the
                    # agent's model here would be told the wrong one exactly when a
                    # delegate was pinned to a cheaper one.
                    runs=mine if mine is not None else root,
                    helpers=helpers or None,
                    default_model=inherited if nested else None,
                    tool_objects=list(surface.objects.values()) if nested else None,
                )
            return compiled[key]

        extras["subagents"] = [
            _with_helpers(n, nested=False) for n in activated
        ]

    def deployment_middleware() -> list[Any]:
        """What this deployment's registry owes one graph, freshly built."""
        if agent is None:
            return []
        return declared_middleware(
            agent,
            registry,
            capabilities.middlewares,
            kind="agent",
            provisions=_provisions(running, agent),
        )

    if permitted is not None:
        middleware.append(ToolAllowlist(permitted))

    # deepagents supplies a `general-purpose` delegate with "the same capabilities as
    # the main agent" and none of our middleware, present whenever `task` is. Supplying
    # one by the same name *replaces* it -- the specs are keyed by name -- so it keeps
    # working and arrives with the caller's ceiling on it, rather than being withheld.
    supplied = list(extras.get("subagents", ()))
    supplied.append(
        {
            **GENERAL_PURPOSE_SUBAGENT,
            # It is handed *this* agent's tools -- deepagents fills a spec that
            # names none from the parent -- so it holds the same objects and needs
            # the same guards around them. Without them a tool call through this
            # delegate reached the tool with its paths untranslated.
            "middleware": (
                tool_guards(held, session_dir)
                + ([ToolAllowlist(permitted)] if permitted is not None else [])
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

    # The agent's own, and last -- the same placement `as_subagent` gives a delegate's,
    # for the same reason: a deployment's middleware should see the tool and skill
    # narrowing kingfisher applied rather than run ahead of it.
    middleware.extend(deployment_middleware())

    return assemble(surface.carried)

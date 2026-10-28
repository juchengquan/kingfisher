"""Agent assembly: kingfisher's configuration and grants into a deepagents graph.

The system prompt is assembled in `prompting` and what a subagent runs with in
`core.agent.kinds.subagents`, and neither calls anything here: this module imports
deepagents, and naming `system_prompt` must not.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents import FilesystemPermission, MemoryMiddleware, create_deep_agent
from deepagents.backends import CompositeBackend, FilesystemBackend, LocalShellBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

from kingfisher.core.access import (
    ALL,
    Capabilities,
    CapabilityError,
    Selection,
    approved_middleware,
    offers,
    refuse_ungranted_models,
)
from kingfisher.core.agent.kinds.declared_middleware import (
    ByName,
    MiddlewareFactory,
    declared_middleware,
    offered_middleware,
)
from kingfisher.core.agent.kinds.subagents import (
    as_subagent,
    clearing_mark,
    model_object,
    subagent_helpers,
    subagent_skills,
)
from kingfisher.core.agent.kinds.tools import (
    ToolSurface,
    bundled_tools_in,
    handed_graphs_in,
    private_tools,
    registered_tools,
    resolve_tools,
)
from kingfisher.core.agent.middlewares.clearing import result_clearing
from kingfisher.core.agent.middlewares.narrowing import CatalogueSkillsMiddleware
from kingfisher.core.agent.middlewares.recorder import (
    Audiences,
    RecorderMiddleware,
    result_id,
)
from kingfisher.core.agent.middlewares.standing import standing_middleware
from kingfisher.core.agent.middlewares.step_budget import StepBudget
from kingfisher.core.config import Config
from kingfisher.core.definitions.agents.spec import AgentError, AgentSpec
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents.spec import RunOn, opens_skills
from kingfisher.core.definitions.tools.spec import FoundTool, audienced, own_audiences
from kingfisher.core.interfaces import AgentsMiddlewares
from kingfisher.core.layout import Layout

from .activation import (
    activatable_skills,
    activated_subagents,
    agents_to_ask,
    for_the_caller,
    offered_to,
    private_skills,
    refuse_misnamed_by,
    skill_denials,
)
from .backend import (
    memory_sources,
    shell_told,
    skills_sources,
)
from .interpreter import interpreter
from .models import (
    deployment_default,
    model_named,
    refuse_a_mark_deepagents_reaches_first,
    summarizer_count,
    summarizer_trigger,
)
from .permitted_backend import PermittedBackend
from .prompting import EXECUTE, memory_guidelines, skills_index, subagent_prompt, system_prompt
from .session_paths import SessionPaths
from .tool_context import Caller, ToolContext
from .tool_guards import guarded_tools
from .usable_backend import refuse_unusable_backend

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from langgraph.graph.state import CompiledStateGraph

    from kingfisher.core.definitions.subagents.spec import SubagentSpec
    from kingfisher.core.interfaces import (
        PdfAttachments,
        ScratchRunLimit,
        SubagentMiddlewares,
    )
    from kingfisher.core.session.ledger import Ledger


@dataclass(frozen=True)
class Assembled:
    """One graph, and what it was built with, so a caller need not intercept
    `create_deep_agent` to ask.

    Every keyword it was called with, not only those a test reads today: a record of
    only what is interesting needs a second mechanism when the next keyword appears.
    """

    #: What `create_deep_agent` returned. A `TYPE_CHECKING` name is safe here: a
    #: dataclass does not evaluate its annotations.
    graph: CompiledStateGraph[Any, ToolContext, Any, Any]
    model: Any
    backend: Any
    system_prompt: str
    middleware: list[Any]
    permissions: list[FilesystemPermission]
    checkpointer: Any
    tools: list[Any] | None
    context_schema: type[ToolContext]
    #: What every turn on this graph is driven with, a resume too: langgraph keeps the
    #: context out of the checkpoint, and a graph resumed without it hands a tool `None`.
    #: On the record because only this build holds both the backend and its file rules.
    context: ToolContext
    #: Passed through `**extras` only when this build wired them, so each defaults to
    #: what "not passed" means rather than to an empty value a test could mistake for one.
    memory: Any = None
    skills: Any = None
    interrupt_on: Mapping[str, Any] | None = None
    subagents: list[Any] | None = None


def memory_is_denied(layout: Layout) -> FilesystemPermission:
    """For a request that declined memory a deployment did wire.

    Reads are denied rather than the prompt rewritten: the prompt is the cached prefix.
    """
    return FilesystemPermission(
        operations=["read"],
        paths=[f"{layout.memory_route}**"],
        mode="deny",
    )


def read_only_permissions(layout: Layout) -> list[FilesystemPermission]:
    """A deny rule for every scope the layout refuses writes or reads under.

    One rule per scope, not per mount, so `/skills/**` covers the catalogue and every
    bundle whatever a catalogue ships.

    These bind the file tools and not `execute`, and neither half suffices alone. The
    shell's half is `confinement.resolve`, macOS-only and switchable off, and the fences,
    which refuse `/inputs` and `.harness` in their own terms -- the saved results in only
    some, since Landlock cannot carve a folder out of the scratchpad it grants whole. The
    write bits `plugins.sessions.permissions` drops under `/inputs` are not that half: the
    shell runs as the files' owner and can lift them.
    """
    return [
        FilesystemPermission(operations=["write"], paths=[scope], mode="deny")
        for scope in layout.denied_scopes()
    ] + [
        FilesystemPermission(operations=["read"], paths=[scope], mode="deny")
        for scope in layout.denied_read_scopes()
    ]


def _gates_for(agent: AgentSpec | None, checkpointer: Any) -> tuple[str, ...]:
    """Which tools this agent stops on, refusing gates that could never pause.

    Asked of the checkpointer, not `conversation_enabled`: a pause needs a saver, and
    without one langgraph suppresses the interrupt, so the gated call would be skipped
    with nothing said.
    """
    wanted = agent.interrupt_on if agent is not None else ()
    if not wanted or agent is None or checkpointer is not None:
        return wanted
    msg = (
        f"agent {agent.name!r} gates {', '.join(wanted)} behind a person, and this "
        f"deployment keeps no conversation (KINGFISHER_CONVERSATION_ENABLED is "
        f"false), so there is no checkpointer for a gated call to pause into. It "
        f"would be skipped without a word. Turn the conversation on, or drop "
        f"interrupt_on from the agent"
    )
    raise AgentError(msg)


def _refuse_unknown_gates(
    agent: AgentSpec | None, gated: tuple[str, ...], surface: ToolSurface
) -> None:
    """Refuse a gate on a name no tool anywhere in this build answers to.

    Against what this build offers, not this caller's grant: a gate on a tool one
    caller was not granted is fine, and a name matching nothing is a typo that leaves
    the author believing a call is gated.

    The names calls carry, which is what an interrupt is keyed by, so a subagent's own
    bundled tool can be gated and an ambiguous one is gated by the name both answer to.
    """
    if not gated or agent is None:
        return
    if held := tuple(name for name in gated if name in surface.only_compiled):
        owners = ", ".join(sorted({surface.only_compiled[name] for name in held}))
        msg = (
            f"{agent.name}: interrupt_on names {', '.join(held)}, which only compiled "
            f"subagent(s) {owners} hold, in their own folder. deepagents runs a compiled "
            f"graph as it was given and hands it none of this agent's gates, so the call "
            f"would run unapproved. Drop the gate, or make it a subagent kingfisher builds"
        )
        raise AgentError(msg)
    if unknown := tuple(name for name in gated if name not in surface.dispatchable):
        msg = (
            f"{agent.name}: interrupt_on names {', '.join(unknown)}, which no tool "
            f"here answers to -- a gate on a name nothing matches never fires. "
            f"Offered: {', '.join(sorted(surface.dispatchable))}"
        )
        raise AgentError(msg)


def _wanted_endpoints(
    run_on: Mapping[str, RunOn] | None, activated: tuple[str, ...], granted: Selection
) -> Mapping[str, RunOn]:
    """Where this request wants subagents to run, once it may say so."""
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
) -> tuple[Any, int | None]:
    """The model this agent's graph runs, and its entry's `clear_results_at`, if any.

    A model the deployment passed in has no entry. The entry is looked up by the name
    `model_object` builds from, so the mark is this model's and not the default's.
    """
    mine = None if agent is None else model_object(agent, cfg, endpoints=endpoints)
    if injected is not None:
        return injected, None
    profile, _ = cfg.models.resolve(None if agent is None else agent.wanted)
    return mine or deployment_default(cfg, endpoints=endpoints), profile.clear_results_at


def _provisions(
    runs: Any, definition: Any, *, cfg: Config, endpoints: Selection, backend: Any
) -> dict[str, Any]:
    """What a class that declared `wants` is filled in from, for one graph.

    One builder for both kinds, so a `middlewares:` line means the same in an agent
    file and a subagent file; only the values differ, a subagent's model being its own.
    """
    return {
        "model": ByName(
            runs, resolve=lambda w, s: model_named(w, cfg, endpoints=endpoints, subject=s)
        ),
        "backend": backend,
        "definition": definition,
        "result_id": result_id,
    }


def _deployment_middleware(
    agent: AgentSpec | None,
    registry: Mapping[str, MiddlewareFactory],
    allowed: Selection,
    *,
    provisions: Mapping[str, Any],
) -> list[Any]:
    """What this deployment's registry owes one graph, freshly built.

    Per graph, because two graphs sharing a middleware instance would share its state.
    `provisions` holds none, so it may be built once.
    """
    if agent is None:
        return []
    return declared_middleware(
        agent, registry, allowed, kind="agent", provisions=provisions
    )


def _probe_backend(cfg: Config, scratch: Path) -> CompositeBackend:
    """A backend nothing runs on, to read the built-in tool set off an assembled graph.

    deepagents' own classes, since the core names no sessions implementation. It
    executes, so `execute` is offered as on a real one, and routes every path the layout
    does, without which a graph carrying kingfisher's deny rules does not build.
    """
    routes = {}
    for route in cfg.layout.routed_paths():
        folder = scratch / route.strip("/")
        folder.mkdir(parents=True, exist_ok=True)
        routes[route] = FilesystemBackend(root_dir=folder, virtual_mode=True)
    return CompositeBackend(
        default=LocalShellBackend(root_dir=scratch, virtual_mode=True), routes=routes
    )


def builtin_tool_names(
    cfg: Config, catalogue: Definitions, offered_tools: Sequence[FoundTool] | None = None
) -> tuple[str, ...] | None:
    """The built-in set, which is only knowable from an assembled graph.

    Not in `kinds/tools.py`: that module is imported by this one, so calling
    `build_agent` from there would close a cycle.

    Also a check: assembling refuses an offered tool wearing a built-in's name.
    `offered_tools` does not decide that; it spares a caller that has walked them.
    """
    import tempfile  # noqa: PLC0415 -- one caller, and only on the branch that probes

    with tempfile.TemporaryDirectory(prefix="kingfisher-builtin-") as scratch:
        return registered_tools(
            build_agent(
                cfg,
                backend=_probe_backend(cfg, Path(scratch)),
                catalogue=catalogue,
                offered_tools=offered_tools,
                # No subagents: wiring a roster would make a listing refuse what it
                # reports -- two definitions of a name are printed, not raised.
                capabilities=Capabilities(subagents=None),
            ).graph
        )


def build_agent(  # noqa: PLR0912, PLR0913, PLR0915 -- the composition root: each statement wires one thing
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
    middleware_registry: Mapping[str, MiddlewareFactory] | None = None,
    model: Any | None = None,
    backend: Any,
    checkpointer: Any | None = None,
    catalogue: Definitions | None = None,
    run_on: Mapping[str, RunOn] | None = None,
    offered_tools: Sequence[FoundTool] | None = None,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
    caller: Caller | None = None,
    ledger: Ledger | None = None,
    instructions: str = "",
    turn_text: str | None = None,
    users_memory: bool = False,
    pdf_attachments: PdfAttachments | None = None,
    subagent_middlewares: SubagentMiddlewares | None = None,
    scratch_run_limit: ScratchRunLimit | None = None,
) -> Assembled:
    """Wire model, backend and checkpointer into a deep agent, and say what was wired.

    `ledger` is the turn's, recorded into by every graph built here; `None` records nothing.
    `turn_text` is what the deployment's `TurnInstructions` said for this turn, sent to every
    graph but a compiled one; `None` where it wired none.
    `users_memory` where this turn's memory is the user's, kept by a `MemoryStore`.
    `pdf_attachments` is asked by every graph built here, as the ledger is recorded into,
    and `scratch_run_limit` by every one holding offered tools; `None` limits nothing.
    `subagent_middlewares` is asked of every portable subagent; `None` runs the agent's own.
    """
    # The agent file is the baseline and the request only subtracts from it. One
    # function, because the run's report of what it activated reads the same answer.
    asked = capabilities or Capabilities()
    capabilities = for_the_caller(agent, asked, held=held)
    roots = catalogue or Definitions.from_config(cfg)
    # Every backend: a check only some builds ran would be one nothing here exercises.
    refuse_unusable_backend(backend, cfg.layout)
    shell = shell_told(backend)
    # Before the middleware stack, because `standing_middleware` needs the names. A caller that
    # has walked them itself -- `--list` -- wins over the catalogue.
    walked = tuple(roots.tools.found if offered_tools is None else offered_tools)
    # Every walked tool, not the granted ones: the grant is computed after the guards,
    # and a tool outside it cannot be reached anyway.
    guarded = frozenset(entry.name for entry in walked)
    permissions = read_only_permissions(cfg.layout)
    # What a tool is handed as `runtime.context.backend` and its `path` resolves through.
    # It reads the rules when it checks, so those added below -- memory declined, skills
    # narrowed -- apply to it too.
    under_rules = PermittedBackend(backend, permissions, cfg.layout)
    paths = SessionPaths(under_rules, cfg.sessions_root, cfg.layout)

    bundled = bundled_tools_in(roots)
    # A bundle's tools too: a subagent's calls are recorded, and a bundled tool may say
    # who it admits.
    in_hand = (*walked, *(found for found, _, _ in bundled))
    # Only where something is locked: with no lock, every stamp would say everyone. The
    # tools in hand, so a catalogue a caller has already walked is not walked again.
    locked = roots.vocabulary_with(audienced(in_hand)) is not None
    own = own_audiences(in_hand) if locked else {}

    def recording(definition: object | None) -> RecorderMiddleware | None:
        """A recorder for one graph, reading access off `definition`'s audiences.

        It saves through the turn's backend, not `under_rules`: the results folder is
        write-denied to the file tools, and the recorder is what writes it. A
        deployment's own refusals still apply, since they live in the backend.
        """
        if ledger is None:
            return None
        audiences = Audiences.of(definition, own=own) if locked else None
        return RecorderMiddleware(ledger, backend, audiences, cfg.layout.tool_results_route)

    running, mark = _running(agent, cfg, capabilities.endpoints, model)
    # Merged once, so an agent and its subagents select from one mapping, or a name would
    # mean different things one level apart.
    middlewares = offered_middleware(middleware_registry or {}, roots.middlewares)
    agent_provisions = (
        {}
        if agent is None
        else _provisions(
            running,
            agent,
            cfg=cfg,
            endpoints=capabilities.endpoints,
            backend=backend,
        )
    )
    # Before anything the summariser sizes: one of these may be it, in deepagents' place.
    named = _deployment_middleware(
        agent, middlewares, capabilities.middlewares, provisions=agent_provisions
    )
    # The agent's alone: `general-purpose` runs its own copies of these, so the same answer.
    refuse_a_mark_deepagents_reaches_first(running, mark, named)
    trigger = summarizer_trigger(running, named)
    clearing = result_clearing(
        ledger,
        trigger,
        mark,
        count=summarizer_count(running, named),
        subject="this agent" if agent is None else f"agent {agent.name!r}",
    )
    # Filled in kingfisher's order once the grant is known; the probe reads it before.
    middleware: list[Any] = []
    # One for the agent and every subagent, measured by `assemble`: a subagent's budget is
    # the agent's in calls, and only the agent's compiled graph says what a call costs.
    budget = StepBudget(cfg.recursion_limit)
    # What the deployment's settings switch on, which goes after every standing one.
    switched: list[Any] = []
    narrowed_skills: tuple[str, ...] | None = None
    narrowed_sources: list[Any] = []
    extras: dict[str, Any] = {}

    # One answer to "are skills on", for this agent's index and prompt and every subagent it
    # builds. A grant without `read_file` gets none: each skill it tried would be refused.
    skills_on = cfg.skills_enabled and opens_skills(capabilities.builtin_tools)

    def remembering(tools: Sequence[str] | None) -> MemoryMiddleware:
        """The memory middleware, telling an agent granted `tools` what it saves with."""
        return MemoryMiddleware(
            backend=backend,
            sources=extras["memory"],
            # As deepagents' own has it: the memory block's own breakpoint.
            add_cache_control=True,
            system_prompt=memory_guidelines(tools, cfg.layout, users=users_memory),
        )

    def indexing(sources: list[Any], tools: Sequence[str] | None) -> CatalogueSkillsMiddleware:
        """Every skill's index, in deepagents' slot, written for an agent granted `tools`."""
        held = tools is None or EXECUTE in tools
        return CatalogueSkillsMiddleware(
            backend=backend,
            sources=sources,
            system_prompt=skills_index(cfg.layout, shell, shelled=held),
        )

    skills_at: int | None = None
    memory_at: int | None = None
    # Narrowing only subtracts: `memory=True` against a deployment that wired none
    # stays off.
    if cfg.memory_enabled and capabilities.memory is not False:
        extras["memory"] = memory_sources(cfg.layout)
        # For its `system_prompt`: deepagents' own says memory lasts into "future
        # conversations". Beside `memory=`, not instead: merged by name, this takes the
        # slot deepagents builds its own in, after the static prompt's cache breakpoint.
        # Alone it lands ahead of it, and every memory edit costs the prompt its cache.
        memory_at = len(switched)
        switched.append(remembering(None))
    elif cfg.memory_enabled:
        # Wired but declined: no memory block is sent, so nothing describes memory, and
        # the file is denied, which a file tool could still open. The block sits behind
        # its own breakpoint, so dropping it leaves the prefix cached.
        permissions.append(memory_is_denied(cfg.layout))

    if skills_on:
        registry = activatable_skills(cfg, catalogue=roots)
        # One source per folder, so a nested skill is visible, labelled as the registry
        # labels it: a label is the first half of what a request grants.
        sources = skills_sources(registry.folders)
        if capabilities.skills == ALL:
            extras["skills"] = sources
            skills_at = len(switched)
            switched.append(indexing(sources, None))
        elif capabilities.skills is None:
            # None granted is every skill denied, or each is a `read_file` away unnamed.
            permissions.extend(skill_denials((), registry))
        else:
            # A bare name two sources both offer is refused, not resolved: resolving
            # it is the silent pick this exists to stop.
            activated = tuple(registry.resolve(one) for one in capabilities.skills)
            # Supplied as middleware rather than via `skills=`: passing that
            # argument makes deepagents construct its own SkillsMiddleware,
            # leaving no way to substitute a filtered one.
            narrowed_skills, narrowed_sources = activated, sources
            permissions.extend(skill_denials(activated, registry))

    interpreter_at: int | None = None
    if cfg.interpreter_enabled:
        # Unrestricted for now: the probe below has to see `eval` to count it
        # among the built-ins, and the grant is not resolved until after it.
        interpreter_at = len(switched)
        switched.append(interpreter(cfg, None))

    # Before the first build, which binds a tool's runs under the gates.
    gated = _gates_for(agent, checkpointer)

    def agents_standing(
        *,
        tools: tuple[str, ...] | None = None,
        reachable: tuple[str, ...] | None = None,
        inner: Sequence[Any] = (),
        limit: ScratchRunLimit | None = None,
    ) -> list[Any]:
        return standing_middleware(
            "agent",
            name="" if agent is None else agent.name,
            inner=inner,
            names=guarded,
            paths=paths,
            layout=cfg.layout,
            backend=backend,
            budget=budget,
            execution_timeout_s=cfg.execution_timeout_s,
            models=cfg.models,
            permissions=permissions,
            keep_results_rounds=cfg.keep_results_rounds,
            trigger=trigger,
            pdf_attachments=pdf_attachments,
            scratch_run_limit=limit,
            recorder=recording(agent),
            clearing=clearing,
            tools=tools,
            skills=narrowed_skills,
            skill_sources=narrowed_sources,
            # Every skill's index is in deepagents' slot, beside this stack.
            indexed=skills_at is not None,
            reachable=reachable,
            gated=gated,
            turn_text=turn_text,
        )

    # What the probe below builds with: nothing narrowed yet, and no subagents to name.
    # No limit either: this stack never runs, and a deployment's is asked once a graph.
    middleware[:] = agents_standing(inner=switched)

    if gated:
        # `True`, not an `InterruptOnConfig`: its `when` is a narrowing this format does
        # not offer, and which decisions a pause allows is decided where it is read.
        extras["interrupt_on"] = dict.fromkeys(gated, True)

    def assemble(
        extra_tools: tuple[Any, ...], granted: tuple[str, ...] | None = None
    ) -> Assembled:
        # One mapping for the call and the record, so the record cannot describe a
        # build that did not happen.
        attached: dict[str, Any] = {
            "model": running,
            "backend": backend,
            # Told of the tools it was granted, which only the last build knows: the
            # probe's prompt is never sent.
            "system_prompt": system_prompt(
                cfg,
                agent.prompt_for(held) if agent else "",
                shell,
                instructions,
                granted,
            ),
            "middleware": middleware,
            "permissions": permissions,
            "checkpointer": checkpointer,
            "tools": list(extra_tools) or None,
            "context_schema": ToolContext,
            **extras,
        }
        graph = create_deep_agent(**attached)
        budget.measure(graph)
        return Assembled(
            graph=graph, context=ToolContext(backend=under_rules, caller=caller), **attached
        )

    # `asked`, not `capabilities`: combined, the agent's names are the ones this caller
    # reaches, and a typo in the file must be refused whoever is calling.
    refuse_misnamed_by(cfg, agent, asked, held=held, catalogue=roots)
    defined, activated = activated_subagents(
        cfg, capabilities, catalogue=roots, held=held, agent=agent
    )
    # What a refusal may list, not what it judges. `None` with nobody to narrow for or no
    # agent resolved: an empty `offered:` list is worse than a wide one.
    in_reach = None if held is None or agent is None else offers("tools", held, [agent])
    surface = resolve_tools(
        str(roots.tools.root) if roots.tools.root is not None else "the catalogue",
        capabilities,
        walked,
        assemble,
        bundled=bundled,
        handed=handed_graphs_in(roots),
        # Either tool list naming anything needs the offered sets, and a subagent
        # naming a helper the built tool objects: the probe is what yields both.
        names_needed=bool(gated)
        or any(
            defined[n].tools not in (ALL, None)
            or defined[n].builtin_tools not in (ALL, None)
            # One axis everything and the other not, as a portable subagent writing
            # `builtin_tools: ["*"]` has: folding both into one list needs "every
            # built-in" read off the probe.
            or (defined[n].tools == ALL) != (defined[n].builtin_tools == ALL)
            or defined[n].subagents is not None
            for n in activated
        ),
        in_reach=in_reach,
        held=held,
    )
    # After the probe: what a build offers is only knowable from an assembled graph,
    # and asking `builtin_tool_names` from here would recurse. `gated` forces the probe.
    _refuse_unknown_gates(agent, gated, surface)
    permitted = surface.permitted

    if interpreter_at is not None and (permitted is not None or gated):
        # Re-wired now the grant is known; the probe needed it unrestricted to see
        # `eval`. A caller who withheld the shell must not reach it from code, nor an
        # unrestricted request a gated `task`.
        switched[interpreter_at] = interpreter(
            cfg,
            permitted,
            gated=gated,
            # Wrapped: a call from code meets none of the guards on this graph's stack.
            offered=guarded_tools(surface.carried, paths),
        )

    if skills_at is not None and permitted is not None:
        # Re-written now the grant is known, so an agent without the shell is not told how
        # the shell reaches the catalogue.
        switched[skills_at] = indexing(extras["skills"], permitted)

    if memory_at is not None and permitted is not None:
        # Re-wired now the grant is known, so an agent holding no tool to save with is
        # not told to save.
        switched[memory_at] = remembering(permitted)

    # Outside the block below, which runs only where a subagent is granted: a request
    # granted none is refused its `run_on` too, rather than having it ignored unsaid.
    wanted = _wanted_endpoints(run_on, activated, capabilities.models)

    if capabilities.subagents is not None:
        # The registry rather than its `names`: a subagent's grant has to be
        # resolved to the skill it means, not merely recognised as a word.
        offered = activatable_skills(cfg, catalogue=roots)
        for name in activated:
            subject = f"subagent {name!r}"
            surface.offers.refuse_unknown(
                defined[name].builtin_tools,
                defined[name].tools,
                subject=subject,
                in_reach=in_reach,
            )
            # After the unknown-name check, so a misspelt name is told it is wrong
            # rather than that it has moved.
            surface.offers.refuse_moved(defined[name].tool_sources, subject=subject)

        # The agent's own, as `named` holds them: what a portable subagent's are chosen from.
        agents_names = (
            ()
            if agent is None
            else approved_middleware(
                agent.middlewares,
                registered=middlewares,
                granted=capabilities.middlewares,
                subject=f"agent {agent.name!r}",
            )
        )
        choosing = subagent_middlewares or AgentsMiddlewares()

        def _middleware_of(spec: SubagentSpec) -> tuple[SubagentSpec, str | None]:
            """`spec`, holding the agent's middleware the deployment chose for it if it
            is portable, and who chose it: one written to travel cannot name any."""
            # No agent resolved is nothing to inherit, as `_deployment_middleware` answers.
            if not spec.portable or agent is None:
                return spec, None
            # A tuple, so `"*"` answered is a name refused rather than a grant of all.
            chosen = tuple(choosing.names(spec.name, agent.name, agents_names))
            settings = agent.middleware_settings
            return replace(
                spec,
                middlewares=chosen,
                middleware_settings={one: settings[one] for one in chosen if one in settings},
            ), "this deployment's SubagentMiddlewares"

        def _built(  # noqa: PLR0913 -- by keyword, so a mark is never passed for a model
            name: str,
            *,
            # This subagent's model, not the agent's.
            runs: Any,
            helpers: list[Any] | None = None,
            default_model: Any = None,
            tool_objects: list[Any] | None = None,
            # Read with `runs`, from the entry that built it.
            clear_results_at: int | None = None,
        ) -> dict[str, Any]:
            """One subagent, with the request's ceiling on every axis."""
            selecting, chosen_by = _middleware_of(defined[name])
            return as_subagent(
                defined[name],
                cfg,
                runs=runs,
                instructions=instructions,
                turn_text=turn_text,
                pdf_attachments=pdf_attachments,
                scratch_run_limit=scratch_run_limit,
                backend=backend,
                permissions=permissions,
                gated=gated,
                endpoints=capabilities.endpoints,
                builtin_tools=surface.granted_builtin,
                tools=surface.offered_grant,
                # Behind the deployment's switch too, like the agent's own index.
                skills=(
                    subagent_skills(defined[name], offered, capabilities.skills, held=held)
                    if skills_on
                    else None
                ),
                skill_sources=skills_sources(roots.registry.folders) if skills_on else None,
                helpers=helpers,
                default_model=default_model,
                tool_objects=tool_objects,
                catalogue=walked,
                # Its own folder's, looked up by the key a grant uses, which is
                # what `bundled_tools` is keyed by.
                private=private_tools(roots, name, held=held),
                # Not behind the switch: a bundle is the subagent's own folder, mounted
                # either way, so withholding its index leaves the files reachable unnamed.
                private_skills=private_skills(roots, name),
                paths=paths,
                run_on=wanted.get(name),
                held=held,
                recorder=recording(defined[name]),
                ledger=ledger,
                clear_results_at=clear_results_at,
                budget=budget,
                extra_middleware=declared_middleware(
                    selecting,
                    middlewares,
                    capabilities.middlewares,
                    kind="subagent",
                    provisions=_provisions(
                        runs,
                        defined[name],
                        cfg=cfg,
                        endpoints=capabilities.endpoints,
                        backend=backend,
                    ),
                    chosen_by=chosen_by,
                ),
            )

        # One compiled agent per definition, however many places it appears: compiling
        # per path is exponential in the catalogue's shape, even with no cycle.
        compiled: dict[tuple[str, bool, int], Any] = {}

        # Every agent this caller can open, not the one being built: this refusal is
        # about a subagent's file, and what it could name instead is the catalogue's.
        listing = offered_to(defined, held, by=agents_to_ask(cfg, catalogue=roots))

        # For a helper: deepagents gives a top-level subagent the agent's model but a
        # nested one none, and refuses a nested spec with no model.
        root = running

        def _with_helpers(
            name: str, *, nested: bool, inherited: Any = None, inherited_mark: int | None
        ) -> Any:
            # `inherited` alone in the key: the mark comes with the model it was read beside.
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
                # Beside `mine`, from the same entry: a mark read off another model's
                # clears by a size this one's summariser may come to first.
                marked = clearing_mark(
                    defined[name], cfg, run_on=override, inherited=inherited_mark
                )
                helpers = [
                    _with_helpers(
                        helper,
                        nested=True,
                        # Its parent's model, not the agent's: "runs whatever
                        # summoned it", one level down.
                        inherited=mine if mine is not None else root,
                        inherited_mark=marked,
                    )
                    # `activated`, not the grant, which may be `"*"`: it leaves out
                    # a helper out of this caller's reach.
                    for helper in subagent_helpers(
                        defined[name], defined, activated, held=held, listing=listing
                    )
                ]
                compiled[key] = _built(
                    name,
                    # What this subagent actually runs: its own model, else its
                    # summoner's, which deepagents hands a spec that names none.
                    runs=mine if mine is not None else root,
                    helpers=helpers or None,
                    default_model=inherited if nested else None,
                    tool_objects=list(surface.objects.values()) if nested else None,
                    clear_results_at=marked,
                )
            return compiled[key]

        extras["subagents"] = [
            _with_helpers(n, nested=False, inherited_mark=mark) for n in activated
        ]

    # deepagents adds a `general-purpose` subagent with the agent's capabilities and none
    # of our middleware whenever `task` is offered. One supplied by that name replaces
    # it, so it keeps working under the caller's ceiling rather than being withheld.
    supplied = list(extras.get("subagents", ()))
    # Its own instances: two graphs sharing one would share its state.
    general = _deployment_middleware(
        agent, middlewares, capabilities.middlewares, provisions=agent_provisions
    )
    generals_trigger = summarizer_trigger(running, general)
    supplied.append(
        {
            **GENERAL_PURPOSE_SUBAGENT,
            "system_prompt": subagent_prompt(
                GENERAL_PURPOSE_SUBAGENT["system_prompt"], instructions, cfg.layout
            ),
            # deepagents hands it the agent's own tool objects, so it needs the same guards.
            "middleware": (
                # The agent's recorder: these are the agent's tools, under its audiences.
                standing_middleware(
                    "general-purpose",
                    name="general-purpose",
                    inner=general,
                    names=guarded,
                    paths=paths,
                    layout=cfg.layout,
                    backend=backend,
                    budget=budget,
                    execution_timeout_s=cfg.execution_timeout_s,
                    models=cfg.models,
                    permissions=permissions,
                    keep_results_rounds=cfg.keep_results_rounds,
                    trigger=generals_trigger,
                    pdf_attachments=pdf_attachments,
                    scratch_run_limit=scratch_run_limit,
                    recorder=recording(agent),
                    # The agent's model and its entry's mark, read against its own stack.
                    clearing=result_clearing(
                        ledger,
                        generals_trigger,
                        mark,
                        count=summarizer_count(running, general),
                        subject="general-purpose",
                    ),
                    tools=permitted,
                    gated=gated,
                    skills=None,
                    turn_text=turn_text,
                )
            ),
        }
    )
    extras["subagents"] = supplied
    # Kingfisher's order: what it guarantees, then what the deployment's settings switch
    # on, then what its definitions name -- last, as `as_subagent` places a subagent's, so
    # a deployment's middleware sees kingfisher's tool and skill narrowing.
    middleware[:] = agents_standing(
        tools=permitted,
        reachable=tuple(spec["name"] for spec in supplied),
        inner=[*switched, *named],
        limit=scratch_run_limit,
    )

    return assemble(surface.carried, permitted)

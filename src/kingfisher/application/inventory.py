"""What a workspace offers, as one answer instead of two half-answers."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType

from kingfisher.application import access
from kingfisher.application.origins import Origins
from kingfisher.config import Config
from kingfisher.domain.access import AccessReport, SourceIds, Stated, reaches
from kingfisher.domain.capabilities import ALL, CapabilityError, Selection
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.kinds.agents.spec import AgentError
from kingfisher.kinds.middlewares.catalogue import MiddlewareError
from kingfisher.kinds.subagents.rules import refuse_cycles
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec
from kingfisher.kinds.tools.catalogue import ToolError
from kingfisher.kinds.tools.spec import Found, Offering

#: An empty mapping that cannot be written to, so a default is shared safely.
_NOTHING: Mapping[str, str] = MappingProxyType({})
#: The same emptiness for the one field whose values are name tuples. Two
#: constants rather than one untyped: an empty mapping cannot drift in value,
#: so what this buys is the type staying honest at the field it defaults.
_NO_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType({})
#: The same, for the nested audience record.
_NO_AUDIENCES: Mapping[str, Mapping[str, Stated]] = MappingProxyType({})


@dataclass(frozen=True)
class Inventory:
    """What this workspace offers right now, per kind, with where each came from."""

    #: Where every part of this deployment was read from, including the four catalogue
    #: directories. One record rather than the three loose strings that were here --
    #: `skills_source`, `subagents_source`, `agents_source` -- which named three of the
    #: four kinds and left `tools` out, because a fourth field is a thing somebody has
    #: to remember to add and nobody did.
    origins: Origins

    #: Agent name -> its description. First in the record because it is first in
    #: the listing: an agent is what a request names, and the three kinds below
    #: are what it selects from.
    agents: Mapping[str, str] = _NOTHING
    #: Agent name -> the file it came from.
    agent_sources: Mapping[str, str] = _NOTHING
    #: Delegate names each agent reaches, its own and theirs, resolved through
    #: the chain. Printed because nobody maintains it: an agent file names the
    #: delegates it calls, and what *those* call comes along -- so this is the
    #: only place the whole tree is visible without opening every file.
    agent_delegates: Mapping[str, tuple[str, ...]] = _NO_NAMES
    agents_error: str | None = None

    #: The agent's own tools, granted with `--builtin-tools`. Empty when the
    #: build failed, which `tools_error` says.
    builtin_tools: tuple[str, ...] = ()
    #: What the workspace defined, granted with `--tools`. A separate axis, not
    #: a second pile: a subtraction taken from the union produced a grant of
    #: built-in names on the workspace axis.
    tools: tuple[str, ...] = ()
    #: Tool name -> the module or package that defined it.
    tool_sources: Mapping[str, str] = _NOTHING
    tools_error: str | None = None

    #: Skill name -> its description, which is what the model is shown. From
    #: the registry rather than the directory listing: a directory that looks
    #: like a skill and will not parse used to be advertised here and then be
    #: absent from an agent that reported nothing wrong.
    skills: Mapping[str, str | None] = _NOTHING
    #: Present on disk, and the agent will never see them.
    skills_unloadable: tuple[str, ...] = ()
    #: Nested past the one level skills are read at. Reported separately because
    #: the fix is different: these parse, they are simply in the wrong place.
    skills_misplaced: tuple[str, ...] = ()
    #: Skills the agent *can* read, under a name their directory does not have,
    #: as `(directory, name)`. Neither missing nor broken: present under a name
    #: nobody typed, which is why it is its own field rather than folded into
    #: `skills_unloadable` or `skills_misplaced`.
    skills_misfiled: tuple[tuple[str, str], ...] = ()

    #: Subagent name -> its description.
    subagents: Mapping[str, str] = _NOTHING
    #: Subagent name -> the file it came from, where a store can say.
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None

    #: Definition -> the tools it names by a path they have moved from, keyed
    #: `"agent 'analyst'"` the way the refusal words its subject. Both kinds, and
    #: they differ in what happens next rather than in being wrong: a subagent's
    #: stops the deployment at startup, an agent's stops nothing at all and the
    #: agent simply runs without the tool it was granted.
    moved_tools: Mapping[str, tuple[str, ...]] = _NO_NAMES

    #: Middleware class name -> the module that defined it.
    middlewares: Mapping[str, str] = _NOTHING
    #: A middleware module that will not import, or offering something that is
    #: not an `AgentMiddleware`. Carried like the others, for the same reason:
    #: a listing is where somebody goes *because* something is broken.
    middlewares_error: str | None = None

    #: What each subagent brings itself, by name: the tools and skills in the
    #: folder named after it. Reported because they are the one capability a
    #: listing could not otherwise reveal -- an agent omitting `tools:` holds
    #: every tool there is, so a bundled one is the only kind the top-level
    #: agent does *not* get, and a reader has no other way to find that out.
    bundled_tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    bundled_skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Catalogue tools a bundle answers for instead, by subagent. Printed
    #: because shadowing is only acceptable while it is visible: the delegate
    #: gets its own and the shared one never reaches it, and nothing else in
    #: this output would say so.
    shadowed: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: A bundle this deployment cannot read: tools that will not import, or a
    #: folder that is one subagent's and holds two definitions. Its own field
    #: rather than `tools_error`, so a listing says which delegate to go and open.
    bundles_error: str | None = None
    #: Folders under `subagents/` holding `tools/` or `skills/` that no definition
    #: is named for. Legal, and nine times in ten a bundle whose definition was
    #: renamed -- which is why it is carried rather than left to the repository that
    #: computes it: a delegate that lost its bundle has no other symptom, at any
    #: point in a run, than holding nothing.
    orphaned_assets: tuple[str, ...] = ()

    #: Which of them are graphs the workspace built rather than definitions kingfisher
    #: assembles. Carried because it changes what the rest of the listing *means* for
    #: them: deepagents runs a compiled graph as given and never applies a tool
    #: allowlist to it, so `--tools` is not a limit on one.
    compiled_subagents: tuple[str, ...] = ()

    #: Kept so a caller does not have to reach for `cfg` to know whether an
    #: empty skills list means "none" or "switched off".
    skills_enabled: bool = True

    #: The reconciled policy, or `None` where this deployment has none.
    #:
    #: Carried rather than looked up by the printer, for the reason the sources
    #: above are: a listing is assembled once and formatted by whoever asked,
    #: and a renderer that had to reach for `Config` would be a second place
    #: deciding what a workspace offers.
    access: SourceIds | None = None
    #: What the policy and the catalogue disagree about. Empty when they agree.
    access_report: AccessReport = field(default_factory=AccessReport)
    #: Definition kind -> name -> {"source_ids": ..., "tools": {...}, ...}, for
    #: every definition that says anything about who reaches what.
    #:
    #: Carried rather than looked up by the printer: a renderer that reached for
    #: the specs would be a second place deciding what a workspace offers, and
    #: the roll-up below is inverted from exactly this.
    audiences: Mapping[str, Mapping[str, Stated]] = _NO_AUDIENCES
    #: Whose view this is, expanded, or `None` for the operator's view of
    #: everything. Set, the names above have already been filtered to what this
    #: caller reaches -- so the printer never filters and the two views cannot
    #: come apart.
    held: frozenset[str] | None = None

    @property
    def offered(self) -> dict[str, tuple[str, ...]]:
        """The four grant axes as bare names, which is what a subtraction needs."""
        return {
            "builtin_tools": self.builtin_tools,
            "tools": self.tools,
            "skills": tuple(self.skills),
            "subagents": tuple(self.subagents),
        }


def reached(named: Selection, defined: Mapping[str, SubagentSpec]) -> tuple[str, ...]:
    """Every delegate an agent ends up with: the ones it names, and theirs."""
    if named is None:
        return ()
    frontier = list(defined) if named == ALL else list(named)
    seen: set[str] = set()
    while frontier:
        name = frontier.pop()
        if name in seen:
            continue
        seen.add(name)
        spec = defined.get(name)
        if spec is None or spec.subagents is None:
            continue
        frontier.extend(defined if spec.subagents == ALL else spec.subagents)
    return tuple(sorted(seen))


def _bundled(
    resolved: Definitions,
) -> tuple[
    Mapping[str, tuple[str, ...]],
    Mapping[str, tuple[str, ...]],
    Mapping[str, tuple[str, ...]],
    str | None,
    tuple[str, ...],
]:
    """What each subagent brings itself, for a listing: tools, skills, shadowed.

    And the folders that bring it to nobody, which belong here rather than in a
    function of their own: an orphan is decided by the same `bundles` read, so
    asking separately means a second `try` around the failure this one returns on.
    """
    tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    shadowed: Mapping[str, tuple[str, ...]] = _NO_NAMES
    error: str | None = None
    orphans: tuple[str, ...] = ()
    try:
        # Imported here for the reason `tools` is: a listing is where someone
        # goes *because* something is broken, so the error is carried and
        # printed over the rest of the output rather than raised through it.
        tools = MappingProxyType(
            {
                name: tuple(sorted(one.name for one in repository.found))
                for name, repository in resolved.bundled_tools.items()
            }
        )
        catalogue = {one.name for one in resolved.tools.found}
        shadowed = MappingProxyType(
            {
                name: found
                for name, names in tools.items()
                if (found := tuple(sorted(catalogue.intersection(names))))
            }
        )
    except (ToolError, SubagentError) as exc:
        # `SubagentError` because reaching a bundle's tools reads the bundles first,
        # and a folder that is one subagent's and holds two definitions is refused
        # there. Uncaught it escaped `inventory` and took `doctor` down with it --
        # the same shape as a workspace tool wearing a built-in's name, found the
        # same way, by a rule that drove the refusal rather than reading about it.
        error = str(exc)
        return tools, skills, shadowed, error, orphans

    # Inside no `try` of its own, and that is the point: it reads the same bundles,
    # so the only way it raises is a way the block above has already returned on.
    skills = MappingProxyType(
        {
            name: tuple(sorted(registry.names))
            for name, registry in resolved.bundled_skills.items()
        }
    )
    # `getattr` for the reason `bundled_tools` uses one: the port declares `specs`
    # and nothing else, so a repository that is not the local one answers nothing
    # here rather than raising.
    orphans = tuple(getattr(resolved.subagents, "orphaned_assets", ()))
    return tools, skills, shadowed, error, orphans


def _moved_tools(resolved: Definitions) -> Mapping[str, tuple[str, ...]]:
    """Definitions naming a tool by a path it no longer lives at.

    Both kinds, through the same `Offering` the refusal uses. A catalogue that will
    not walk answers nothing rather than raising: whichever of `tools`, `agents` or
    `subagents` failed is already reported on its own line, and a second copy of
    that error here would say nothing new.
    """
    try:
        offers = Offering.of(resolved.tools.found)
        return MappingProxyType({
            f"{kind} {name!r}": tuple(one for one, _claimed, _actual in moved)
            for kind, specs in (
                ("agent", resolved.agents.specs),
                ("subagent", resolved.subagents.specs),
            )
            for name, spec in specs.items()
            if (moved := offers.moved(spec.tool_sources))
        })
    except (ToolError, AgentError, SubagentError):
        return _NO_NAMES


def _middlewares(resolved: Definitions) -> tuple[Mapping[str, str], str | None]:
    """What the workspace registers, and why it could not be read.

    Carried rather than raised, for the reason `_bundled` gives one function up: a
    listing is where somebody goes *because* something is broken, so the error is
    printed over the rest of the output rather than through it.
    """
    try:
        return MappingProxyType(
            {name: cls.__module__ for name, cls in resolved.middlewares.classes.items()}
        ), None
    except MiddlewareError as exc:
        return _NOTHING, str(exc)


def _audiences(specs: Mapping[str, object], *, kind: str) -> dict[str, Stated]:
    """What each definition of one kind says about who reaches what.

    The `says_nothing` filter is here rather than in the walk: a listing shows the
    definitions that restrict somebody, and the other two callers want what a
    definition says whether or not that is anything.
    """
    return {
        name: said
        for _kind, name, said in access.walked((kind, specs))
        if not said.says_nothing
    }


def _access(
    cfg: Config,
    source_ids: Iterable[str] | None,
    *,
    agents: Mapping[str, object],
    subagents: Mapping[str, object],
) -> tuple[dict[str, Mapping[str, Stated]], AccessReport, frozenset[str] | None, dict[str, str]]:
    """What the definitions say, what restricts nobody, and whose view this is."""
    stated = {
        "agents": _audiences(agents, kind="agent"),
        "subagents": _audiences(subagents, kind="subagent"),
    }
    if cfg.access is None:
        return stated, AccessReport(), None, {}
    # The same walk `Kingfisher` runs at construction, and the same functions -- which
    # is the point. A listing that disagreed with startup about who reaches what would
    # be worse than neither saying anything, and until these were shared nothing but a
    # docstring held them together.
    kinds = (("agent", agents), ("subagent", subagents))
    report = access.audit(*kinds, vocabulary=cfg.access)
    broken = {
        f"{kind}s": complaint
        for kind, specs in kinds
        if (complaint := access.undeclared_in(specs, kind=kind, vocabulary=cfg.access))
        is not None
    }
    held = access.held_by(cfg.access, source_ids)
    return stated, report, held, broken


def _reaching(
    held: frozenset[str] | None, audiences: Mapping[str, Mapping[str, Stated]]
) -> Callable[[str, Mapping[str, str]], Mapping[str, str]]:
    """A filter keeping only the definitions this caller reaches, or the identity."""
    if held is None:
        return lambda _kind, names: names

    # Bound once, rather than re-tested inside the closure. The comprehension used
    # to carry its own `held is None`, which could never be true -- this closure is
    # only built on the branch where it is not -- but removing it left the checker
    # with nothing to narrow the optional by, since the guard above is a statement
    # away across a closure. A name the guard has already settled says it to both.
    reachable = held

    def keep(kind: str, names: Mapping[str, str]) -> Mapping[str, str]:
        stated = audiences.get(kind, {})
        return {
            name: value
            for name, value in names.items()
            if reaches(stated.get(name, Stated()).source_ids, reachable)
        }

    return keep


def _builtin_tools(
    cfg: Config, resolved: Definitions, found: Sequence[Found] | None
) -> tuple[str, ...] | None:
    """The built-in set, asked of the harness that knows how to assemble one."""
    from kingfisher.infrastructure.harness.agent import builtin_tool_names  # noqa: PLC0415

    return builtin_tool_names(cfg, resolved, found)


def _tools(
    cfg: Config, resolved: Definitions
) -> tuple[tuple[str, ...], tuple[str, ...], Mapping[str, str], str | None]:
    """The built-in set, what the workspace adds, where each one lives, and why not.

    Lifted out of `inventory` for the reason `_bundled` and `_middlewares` are: one
    kind read, its failure carried rather than raised, and the answers handed back
    together.
    """
    builtin: tuple[str, ...] = ()
    workspace_tools: tuple[str, ...] = ()
    sources: Mapping[str, str] = _NOTHING
    tools_error: str | None = None
    try:
        # Inside the `try`, not before it. Walking the catalogue is what raises
        # -- two modules claiming one tool name, a module that will not import
        # -- so reading `.found` outside meant the error escaped this function
        # and `--list` printed a traceback over the rest of the inventory.
        found = resolved.tools.found
        on_offer = Offering.of(found)
        workspace_tools = tuple(sorted(on_offer.workspace))
        sources = MappingProxyType(dict(on_offer.sources))
        # Walked once and handed to the build. This needs two things that were
        # once fetched apart -- where each workspace tool is defined, and the
        # built-in set, which is only knowable from an assembled graph -- and a
        # tool module is Python, so fetching them apart ran every one twice.
        #
        introspected = _builtin_tools(cfg, resolved, found)
        if introspected is None:
            # The graph compiled and then could not be read, which means the
            # built-in set is unknown rather than empty. Carried as an error for
            # the same reason the others are: a listing is where someone goes
            # *because* something is broken, and printing "(none)" here would
            # answer a question we did not manage to ask.
            tools_error = (
                "built-in tools could not be read from the compiled agent -- the graph "
                "has no shape this version recognises, so what it dispatches is unknown"
            )
        else:
            defined = {entry.name for entry in found}
            builtin = tuple(name for name in introspected if name not in defined)
    except (ToolError, CapabilityError) as exc:
        # `CapabilityError` for the same reason as `ToolError`, and it arrives from
        # one line further on: assembling the probe refuses a workspace tool that
        # would replace a built-in of the same name. That is a fact about this
        # deployment's tools, which is what this field carries -- and left to
        # escape it took `doctor` and `list` down with a traceback over a
        # deployment that starts perfectly well, since nothing refuses the clash
        # until the first request that touches tools.
        tools_error = str(exc)
    except MiddlewareError:
        # Assembling the probe reads the middleware directory too, so a module that
        # will not import arrives here rather than at the read above. Deliberately
        # not `tools_error`: the tool catalogue walked fine, and `middlewares_error`
        # already carries this one with the file to go and open. What is lost is the
        # built-in set, which no longer has a graph to be read off -- and saying
        # "tools failed" about that would send a reader to the wrong directory.
        pass
    return builtin, workspace_tools, sources, tools_error


def inventory(
    cfg: Config, *, catalogue: Definitions | None = None, source_ids: Iterable[str] | None = None
) -> Inventory:
    """Ask the workspace what it offers, through the catalogue a run would use."""
    resolved = catalogue if catalogue is not None else resolve_definitions(cfg)

    middlewares, middlewares_error = _middlewares(resolved)
    moved_tools = _moved_tools(resolved)

    builtin, workspace_tools, sources, tools_error = _tools(cfg, resolved)

    registry = resolved.registry
    subagents: Mapping[str, str] = _NOTHING
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None
    compiled_subagents: tuple[str, ...] = ()
    # Bound before the `try`, because the audience walk below reads them and a
    # catalogue that will not load must still produce a listing -- that is the
    # whole reason these errors are carried rather than raised.
    specs: Mapping[str, object] = _NOTHING
    try:
        # Both reads, in one `try`. `sources` parses the same files `specs`
        # does, so reading it outside let the error escape from the line that
        # was only asking which file each definition came from -- and `getattr`
        # with a default does not help, because the property raises rather than
        # being absent.
        specs = resolved.subagents.specs
        # Asked here as well as at `build_agent`, and that is the point rather than
        # duplication. A cycle is a property of the catalogue, so an inventory that
        # reports the catalogue has to report it: this said a workspace was fine while a
        # run refused it, which is the same shape as `--list` advertising a skill the
        # agent would not load.
        refuse_cycles(specs)
        subagents = {name: spec.description for name, spec in specs.items()}
        compiled_subagents = tuple(
            name for name, spec in specs.items() if spec.build is not None
        )
        subagent_sources = MappingProxyType(
            dict(getattr(resolved.subagents, "sources", {}))
        )
    except SubagentError as exc:
        subagents_error = str(exc)

    bundled_tools, bundled_skills, shadowed, bundles_error, orphaned_assets = (
        _bundled(resolved)
        if subagents_error is None
        else (_NO_NAMES, _NO_NAMES, _NO_NAMES, None, ())
    )

    agents: Mapping[str, str] = _NOTHING
    defined_agents: Mapping[str, object] = _NOTHING
    agent_sources: Mapping[str, str] = _NOTHING
    agent_delegates: Mapping[str, tuple[str, ...]] = _NO_NAMES
    agents_error: str | None = None
    try:
        # The same shape as the subagent read below it, and in one `try` for the
        # same reason: `sources` parses the files `specs` does.
        defined_agents = resolved.agents.specs
        agents = {name: spec.description for name, spec in defined_agents.items()}
        agent_sources = MappingProxyType(dict(getattr(resolved.agents, "sources", {})))
        agent_delegates = MappingProxyType(
            {
                name: reached(spec.subagents, resolved.subagents.specs)
                for name, spec in defined_agents.items()
            }
        )
    except (AgentError, SubagentError) as exc:
        # `SubagentError` too: resolving the chain reads the subagent catalogue,
        # so a broken delegate makes the *agents* half unanswerable. Reported
        # here rather than raised, like its neighbours -- a listing is where
        # somebody goes because something is broken.
        agents_error = str(exc)

    stated, report, held, broken = _access(
        cfg, source_ids, agents=defined_agents, subagents=specs
    )
    reaching = _reaching(held, stated)

    return Inventory(
        # The resolved catalogue, not `cfg` -- so a deployment that staged its
        # definitions somewhere is listed as it is rather than as it was
        # configured, which is the difference the record was built for.
        origins=Origins.of(cfg, catalogue=resolved),
        agents=MappingProxyType(dict(reaching("agents", agents))),
        agent_sources=agent_sources,
        agent_delegates=agent_delegates,
        # `or`, not replace: a kind that already failed to load has the more
        # fundamental problem, and saying the second one instead would send a
        # reader to a source id in a file that does not parse.
        agents_error=agents_error or broken.get("agents"),
        builtin_tools=builtin,
        tools=tuple(reaching("tools", dict.fromkeys(workspace_tools, ""))),
        tool_sources=sources,
        tools_error=tools_error,
        skills={name: registry.description(name) for name in registry.names},
        skills_unloadable=tuple(registry.unloadable),
        skills_misplaced=tuple(getattr(resolved.skills, "misplaced", ())),
        skills_misfiled=tuple(registry.misfiled),
        subagents=MappingProxyType(dict(reaching("subagents", subagents))),
        subagent_sources=subagent_sources,
        subagents_error=subagents_error or broken.get("subagents"),
        middlewares=middlewares,
        middlewares_error=middlewares_error,
        moved_tools=moved_tools,
        bundled_tools=bundled_tools,
        bundled_skills=bundled_skills,
        shadowed=shadowed,
        bundles_error=bundles_error,
        orphaned_assets=orphaned_assets,
        compiled_subagents=compiled_subagents,
        skills_enabled=cfg.skills_enabled,
        access=cfg.access,
        access_report=report,
        held=held,
        audiences=stated,
    )

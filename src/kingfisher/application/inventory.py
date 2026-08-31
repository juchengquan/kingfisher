"""What a workspace offers, as one answer instead of two half-answers.

`--list` and `--without-skills` ask the same question and used to compute it
apart: `show_inventory` assembled the display, `_offered` assembled the names,
and both built an agent to do it. Two implementations of "what may a request
activate here" is one more than can be kept in step -- and the one that drifts
is `--without-skills`, which subtracts from a set that has to be the set the run
will actually offer, or it refuses a name the run did not have.

So the question is answered once, here, and returned as a record. Whoever wants
names reads the names; whoever wants to print reads the rest. Nothing here
prints: a library that writes to stdout cannot be used by a server, and the two
callers format differently anyway.

The tool surface is *built*, not listed. It includes whatever the workspace
defined, so the only honest answer is an assembled agent -- which is why this
lives in infrastructure and cannot sit any higher.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from kingfisher.config import Config
from kingfisher.domain.access import Access, AccessReport
from kingfisher.domain.agent import AgentError
from kingfisher.domain.capabilities import ALL, Capabilities, Selection
from kingfisher.domain.subagent import SubagentError, SubagentSpec
from kingfisher.domain.subagent.rules import refuse_cycles
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions, source_of
from kingfisher.infrastructure.catalogue.tools import ToolError
from kingfisher.infrastructure.workspace_fs import ensure_session_layout

#: An empty mapping that cannot be written to, so a default is shared safely.
_NOTHING: Mapping[str, str] = MappingProxyType({})
#: The same emptiness for the one field whose values are name tuples. Two
#: constants rather than one untyped: an empty mapping cannot drift in value,
#: so what this buys is the type staying honest at the field it defaults.
_NO_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType({})


@dataclass(frozen=True)
class Inventory:
    """What this workspace offers right now, per kind, with where each came from.

    The sources are not decoration. A catalogue can be deployed outside the
    workspace and shared by several deployments, a folder cannot reach a tool's
    name, and a definition can be present on disk and invisible to the agent.
    Each field beyond the names exists because one of those has gone wrong.

    `tools_error` and `subagents_error` are carried rather than raised. A
    listing is where someone goes *because* something is broken, so one
    unloadable catalogue must not take the other two down with it -- the
    printer decides what to say and what exit code to use.
    """

    workspace: Path
    #: Where each shared catalogue resolved to. Named rather than assumed.
    skills_source: str
    subagents_source: str
    agents_source: str = ""

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
    #: the two above.
    skills_misfiled: tuple[tuple[str, str], ...] = ()

    #: Subagent name -> its description.
    subagents: Mapping[str, str] = _NOTHING
    #: Subagent name -> the file it came from, where a store can say.
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None

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
    #: A bundle whose tools will not import. Its own field rather than
    #: `tools_error`, so a listing says which delegate to go and look at.
    bundles_error: str | None = None

    #: Which of them are graphs the workspace built rather than definitions
    #: kingfisher assembles. Carried because it changes what the rest of the
    #: listing *means* for them: deepagents runs a compiled graph as given and
    #: never applies a tool allowlist to it, so `--tools` is not a limit on one.
    #:
    #: A separate tuple rather than a flag folded into `subagents`, whose values
    #: are descriptions and are printed as such. Two facts about one name, and
    #: the second one is about a minority.
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
    access: Access | None = None
    #: What the policy and the catalogue disagree about. Empty when they agree.
    access_report: AccessReport = field(default_factory=AccessReport)
    #: Whose view this is, expanded, or `None` for the operator's view of
    #: everything. Set, the names above have already been filtered to what this
    #: caller reaches -- so the printer never filters and the two views cannot
    #: come apart.
    held: frozenset[str] | None = None

    @property
    def offered(self) -> dict[str, tuple[str, ...]]:
        """The four grant axes as bare names, which is what a subtraction needs.

        Derived rather than stored, so it cannot disagree with the listing above
        -- that disagreement is the bug this record exists to make impossible.
        """
        return {
            "builtin_tools": self.builtin_tools,
            "tools": self.tools,
            "skills": tuple(self.skills),
            "subagents": tuple(self.subagents),
        }


def reached(named: Selection, defined: Mapping[str, SubagentSpec]) -> tuple[str, ...]:
    """Every delegate an agent ends up with: the ones it names, and theirs.

    Resolved when the catalogue is read rather than kept in a file, which is the
    whole reason an agent names only the delegates it calls. A list written by
    hand goes stale the moment a file somebody else owns changes its own
    helpers, and nothing anywhere says so.

    Sorted, and deduplicated by the visit rather than at the end: a definition
    reached twice is not a loop, and `refuse_cycles` has already refused the
    ones that are -- so this cannot run away.
    """
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
]:
    """What each subagent brings itself, for a listing: tools, skills, shadowed.

    Its own function because `inventory` was at the statement limit, and because
    what it does is one thing: the three answers come from one pair of reads and
    are only ever wanted together.

    The tool half is in a `try` and the skill half is not, which is the split
    `list` already makes between the two kinds -- a tool that will not import is
    a broken catalogue and exits 1, a skill that will not load is reported and
    the run works without it.
    """
    tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    shadowed: Mapping[str, tuple[str, ...]] = _NO_NAMES
    error: str | None = None
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
    except ToolError as exc:
        error = str(exc)
    skills = MappingProxyType(
        {
            name: tuple(sorted(registry.names))
            for name, registry in resolved.bundled_skills.items()
        }
    )
    return tools, skills, shadowed, error



def _reconciled(
    cfg: Config,
    groups: Iterable[str] | None,
    *,
    agents: tuple[str, ...],
    subagents: tuple[str, ...],
    tools: tuple[str, ...],
) -> tuple[Access | None, AccessReport, frozenset[str] | None]:
    """The policy as it applies to what was just walked, and whose view this is.

    Reconciled against the names this function found rather than against a
    second reading of the same directories, so the report a listing prints says
    the same thing `Kingfisher` says at construction.
    """
    if cfg.access is None:
        return None, AccessReport(), None
    access, report = cfg.access.reconciled(
        {"agents": agents, "subagents": subagents, "tools": tools}
    )
    return access, report, (access.expand(groups) if groups is not None else None)


def _reaching(
    access: Access | None, held: frozenset[str] | None
) -> Callable[[str, Mapping[str, str]], Mapping[str, str]]:
    """A filter keeping only what this caller reaches, or the identity.

    Applied where the record is built rather than where it is printed, so a
    `--as` listing and the turn that caller would actually get are narrowed by
    one rule and cannot come apart. The identity for the operator's view, which
    is what `None` means.
    """
    if access is None or held is None:
        return lambda _kind, names: names

    def keep(kind: str, names: Mapping[str, str]) -> Mapping[str, str]:
        within = set(access.reachable(kind, held))
        return {name: value for name, value in names.items() if name in within}

    return keep

def inventory(
    cfg: Config, *, catalogue: Definitions | None = None, groups: Iterable[str] | None = None
) -> Inventory:
    """Ask the workspace what it offers, through the catalogue a run would use.

    `catalogue` is accepted so a caller that already resolved one does not
    resolve it twice; the fallback is `cfg`, which is what the drivers pass.
    Reading `cfg` here while the agent read somewhere else is how
    `--without-skills X` came to refuse a name the run did not have.

    `groups` narrows the answer to what one caller reaches, which is the same
    rule a turn runs under rather than a second one -- the filtering happens
    *here*, on the record, so the printed view and the runnable view cannot
    come apart. `None` is the operator's view of everything, which is what a
    listing is for and is why a listing is not refused the way a turn is: it
    is read-only, and whoever runs it can read the policy file anyway.
    """
    from kingfisher.infrastructure.harness.agent import (  # noqa: PLC0415
        build_agent,
        registered_tools,
    )

    resolved = catalogue if catalogue is not None else resolve_definitions(cfg)

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
        # Rooted at a throwaway directory. An agent needs a session to root its
        # backend at, but what a workspace *offers* is a question about the
        # workspace, and answering it must not leave a session behind for
        # `keep_runs` to reap.
        # Given its layout, because a backend is built against a session that
        # exists rather than one it makes for itself -- `ensure_session_layout`
        # is the only thing that makes a session now.
        with tempfile.TemporaryDirectory(prefix="kingfisher-inventory-") as scratch:
            introspected = registered_tools(
                build_agent(
                    cfg,
                    session_dir=ensure_session_layout(Path(scratch)),
                    catalogue=resolved,
                    workspace_tools=found,
                    # No delegates. What a workspace *offers* is answered from
                    # the catalogue a few lines down; this build exists solely to
                    # read the built-in tool set off a compiled graph, and wiring
                    # a roster to do it would make a listing refuse the very
                    # things it is meant to report -- two definitions of a name
                    # are printed here, not raised.
                    capabilities=Capabilities(subagents=None),
                )
            )
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
    except ToolError as exc:
        tools_error = str(exc)

    registry = resolved.registry
    subagents: Mapping[str, str] = _NOTHING
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None
    compiled_subagents: tuple[str, ...] = ()
    try:
        # Both reads, in one `try`. `sources` parses the same files `specs`
        # does, so reading it outside let the error escape from the line that
        # was only asking which file each definition came from -- and `getattr`
        # with a default does not help, because the property raises rather than
        # being absent.
        specs = resolved.subagents.specs
        # Asked here as well as at `build_agent`, and that is the point rather
        # than duplication. A cycle is a property of the catalogue, so an
        # inventory that reports the catalogue has to report it: this said a
        # workspace was fine while a run refused it, which is the same shape as
        # `--list` advertising a skill the agent would not load.
        #
        # Reading `specs` cannot raise it -- a definition naming a helper is
        # perfectly well-formed on its own, and the loop only exists across
        # files.
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

    bundled_tools, bundled_skills, shadowed, bundles_error = (
        _bundled(resolved) if subagents_error is None else (_NO_NAMES, _NO_NAMES, _NO_NAMES, None)
    )

    agents: Mapping[str, str] = _NOTHING
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

    access, report, held = _reconciled(
        cfg, groups, agents=tuple(agents), subagents=tuple(subagents), tools=workspace_tools
    )
    reaching = _reaching(access, held)

    return Inventory(
        workspace=cfg.workspace,
        skills_source=source_of(resolved.skills),
        subagents_source=source_of(resolved.subagents),
        agents_source=source_of(resolved.agents),
        agents=MappingProxyType(dict(reaching("agents", agents))),
        agent_sources=agent_sources,
        agent_delegates=agent_delegates,
        agents_error=agents_error,
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
        subagents_error=subagents_error,
        bundled_tools=bundled_tools,
        bundled_skills=bundled_skills,
        shadowed=shadowed,
        bundles_error=bundles_error,
        compiled_subagents=compiled_subagents,
        skills_enabled=cfg.skills_enabled,
        access=access,
        access_report=report,
        held=held,
    )

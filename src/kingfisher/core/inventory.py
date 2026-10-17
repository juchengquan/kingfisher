"""What a workspace offers, as one answer instead of two half-answers."""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from types import MappingProxyType

from kingfisher.core.access import checks as access
from kingfisher.core.access.capabilities import CapabilityError, unoffered
from kingfisher.core.access.reach import activated, handed_work_by, offers, reaches
from kingfisher.core.access.source_ids import AccessReport, SourceIds, Stated, with_datazone
from kingfisher.core.config import Config, ConfigError
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.catalogue import (
    Definitions,
    bundled_tool_names,
    resolve_definitions,
)
from kingfisher.core.definitions.importing import defined_in_a_workspace
from kingfisher.core.definitions.middlewares.catalogue import MiddlewareError
from kingfisher.core.definitions.subagents.rules import miscounted, refuse_cycles
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.core.definitions.tools.catalogue import ToolError
from kingfisher.core.definitions.tools.spec import (
    Found,
    Offering,
    admitted,
    audienced,
    out_of_reach,
    placed,
    reference,
    split_reference,
)
from kingfisher.core.origins import Origin, Origins

#: An empty mapping that cannot be written to, so a default is shared safely.
_NOTHING: Mapping[str, str] = MappingProxyType({})
#: The same, typed for the fields whose values are name tuples.
_NO_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType({})
#: The same, for the nested audience record.
_NO_AUDIENCES: Mapping[str, Mapping[str, Stated]] = MappingProxyType({})


@dataclass(frozen=True)
class Catalogued:
    """One definition kind's catalogue, as this walk found it.

    Not `Kind`: `origins.Kind` already means what sort of *place* something was read from.
    """

    name: str
    #: What stopped the directory being read, or `None`. Never set for `skills`, which fail
    #: one at a time, into `skills_unloadable`, `skills_misplaced` and `skills_misfiled`.
    error: str | None
    #: How many the agent can reach. Zero is a fact about the workspace, not a failure.
    defined: int
    #: Where this kind was read from, and whether that is where the configuration says.
    origin: Origin
    #: What one of these is on disk, for a message saying what to fix: a file or a module.
    written_as: str


@dataclass(frozen=True)
class Inventory:
    """What this workspace offers right now, per kind, with where each came from."""

    #: Where every part of this deployment was read from, every catalogue directory included.
    origins: Origins

    #: Agent name -> its description. First, as in the listing: an agent is what a request
    #: names, and the other kinds are what it selects from.
    agents: Mapping[str, str] = _NOTHING
    #: Agent name -> the file it came from.
    agent_sources: Mapping[str, str] = _NOTHING
    #: The subagents each agent activates: the ones its own file names, and no further.
    #: Walking their helpers would list a subagent the build drops.
    agent_subagents: Mapping[str, tuple[str, ...]] = _NO_NAMES
    agents_error: str | None = None

    #: The agent's own tools, granted with `builtin_tools:`. Empty where the build failed,
    #: which `tools_error` says, and where it could not be read, which only
    #: `builtin_tools_checked` says.
    builtin_tools: tuple[str, ...] = ()
    #: Whether the built-in set was read at all: `False` where a middleware that will not
    #: import took the probe down. That fault is `middlewares_error`'s, so `tools_error`
    #: stays clear and this says the count is unknown rather than zero.
    builtin_tools_checked: bool = True
    #: What the workspace defined, granted with `tools:`. A separate axis from
    #: `builtin_tools`: a subtraction from their union grants built-in names on this one.
    tools: tuple[str, ...] = ()
    #: Tool name -> the module or package that defined it.
    tool_sources: Mapping[str, str] = _NOTHING
    tools_error: str | None = None
    #: Why a graph could not be assembled at all: a `models.yaml` row that cannot start a
    #: turn, a backend or a mount that refuses. Not a kind's error, so not in `by_kind`;
    #: carried, so the commands that find such a fault still run.
    build_error: str | None = None

    #: Skill name -> its description, as the model is shown it. From the registry, not the
    #: directory listing, so a skill that will not parse is not advertised.
    skills: Mapping[str, str | None] = _NOTHING
    #: Present on disk, and the agent will never see them.
    skills_unloadable: tuple[str, ...] = ()
    #: Nested past the one level skills are read at: these parse, and are in the wrong place.
    skills_misplaced: tuple[str, ...] = ()
    #: Skills the agent *can* read, under a name their directory does not have, as
    #: `(directory, name)`: neither missing nor broken, so a field of their own.
    skills_misfiled: tuple[tuple[str, str], ...] = ()

    #: Subagent name -> its description.
    subagents: Mapping[str, str] = _NOTHING
    #: Subagent name -> the file it came from, where a store can say.
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None

    #: Definition -> the tools it names by a path they have moved from, keyed as a refusal
    #: words its subject. A subagent's stops startup; an agent's runs without the tool.
    moved_tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Whether `moved_tools` was asked at all: `False` when a catalogue it reads did not
    #: load, which an empty mapping would report as "nothing moved".
    moved_tools_checked: bool = True

    #: Definition -> the subagents it names that the catalogue has no key for, keyed as
    #: `moved_tools` is: one nobody defines, or a bare name two files share. A run refuses it.
    unresolved_subagents: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Whether `unresolved_subagents` was asked at all; `False` is not "none".
    unresolved_subagents_checked: bool = True

    #: Definitions whose `source: bundled` entries and own folder disagree, with how.
    #: Startup refuses this, and `doctor` promises to say in advance what startup refuses.
    miscounted_bundles: Mapping[str, str] = _NOTHING
    #: Whether `miscounted_bundles` was asked at all; `False` is not "none".
    miscounted_bundles_checked: bool = True

    #: Middleware class name -> the installed module it came from, or `""` for a workspace
    #: file, whose `__module__` is only a name made unique.
    middlewares: Mapping[str, str] = _NOTHING
    #: A middleware module that will not import, or offers something not an
    #: `AgentMiddleware`. Carried, as every error here is: a listing is where somebody
    #: goes *because* something is broken.
    middlewares_error: str | None = None

    #: What each subagent brings itself, by name: the tools and skills in its own folder,
    #: listed as `source: bundled`. The one kind the top-level agent never gets, so shown.
    bundled_tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    bundled_skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Skills in a compiled subagent's bundle, which it cannot list: deepagents gives a
    #: compiled graph no skills index. Carried so the listing says *why* the folder is refused
    #: as unlisted, or a reader adds a `source: bundled` line a compiled declaration refuses.
    stranded_skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Skills too deep in a subagent's own folder to load, by subagent, relative to the
    #: folder: a skill absent from what a subagent holds is otherwise invisible.
    bundled_misplaced: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Catalogue tools a bundle answers for instead, by subagent: shadowing is acceptable
    #: only while visible, and nothing else says the shared one never reaches it.
    shadowed: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Whether `shadowed` was asked at all: it needs the workspace catalogue, and one that
    #: did not load is not "nothing shadows".
    shadowed_checked: bool = True
    #: Subagents whose bundle arrived with the definition rather than in a folder under the
    #: catalogue: its tools were installed, so they print alike but are not reviewable here.
    carried_bundles: tuple[str, ...] = ()
    #: A bundle this deployment cannot read: tools that will not import, or one subagent's
    #: folder holding two definitions. Not `tools_error`, so a listing says which to open.
    bundles_error: str | None = None
    #: Folders under `subagents/` holding `tools/` or `skills/` that no definition is named
    #: for. Legal, but usually a renamed definition, whose subagent shows no other symptom
    #: than holding nothing.
    orphaned_assets: tuple[str, ...] = ()

    #: Subagents that are graphs the workspace built: deepagents runs one as given, so a
    #: `tools:` grant is no limit on it.
    compiled_subagents: tuple[str, ...] = ()

    #: Whether an empty `skills` means "none" or "switched off".
    skills_enabled: bool = True

    #: The reconciled policy, or `None` where there is none. Carried, not looked up by the
    #: printer: a renderer reaching for `Config` would be a second place deciding what is offered.
    access: SourceIds | None = None
    #: What the policy and the catalogue disagree about. Empty when they agree.
    access_report: AccessReport = field(default_factory=AccessReport)
    #: Definition kind -> name -> {"source_ids": ..., "tools": {...}, ...}, for every
    #: definition saying who reaches what, a tool named as a grant writes it and a
    #: subagent's own as `subagent::tool`. Carried so no renderer reads the specs itself.
    audiences: Mapping[str, Mapping[str, Stated]] = _NO_AUDIENCES
    #: Whose view this is, expanded, or `None` for the operator's view of everything. Set,
    #: every field is answered for this caller here, so the printer never filters: a field
    #: naming a definition keeps what the caller reaches, a catalogue keeps what those
    #: definitions offer, and the policy and faults reaching nobody are dropped.
    held: frozenset[str] | None = None

    def by_kind(self) -> tuple[Catalogued, ...]:
        """Every definition kind, in the order a listing shows them.

        Spelled out rather than read by name, so a type checker follows the fields.
        """
        return (
            Catalogued("agents", self.agents_error, len(self.agents), self.origins.agents, "file"),
            Catalogued("skills", None, len(self.skills), self.origins.skills, "file"),
            Catalogued(
                "subagents",
                self.subagents_error,
                len(self.subagents),
                self.origins.subagents,
                "file",
            ),
            Catalogued("tools", self.tools_error, len(self.tools), self.origins.tools, "module"),
            Catalogued(
                "middlewares",
                self.middlewares_error,
                len(self.middlewares),
                self.origins.middlewares,
                "module",
            ),
        )

    @property
    def offered(self) -> dict[str, tuple[str, ...]]:
        """The four grant axes as bare names, which is what a subtraction needs."""
        return {
            "builtin_tools": self.builtin_tools,
            "tools": self.tools,
            "skills": tuple(self.skills),
            "subagents": tuple(self.subagents),
        }


def _bundled(
    resolved: Definitions,
) -> tuple[
    Mapping[str, tuple[str, ...]],
    Mapping[str, tuple[str, ...]],
    Mapping[str, tuple[str, ...]] | None,
    str | None,
    tuple[str, ...],
    tuple[str, ...],
]:
    """What each subagent brings itself, for a listing, and the folders that bring it to nobody.

    Orphans are decided by the same `bundles` read, so asking apart needs a second `try`.
    """
    tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    shadowed: Mapping[str, tuple[str, ...]] | None = _NO_NAMES
    error: str | None = None
    orphans: tuple[str, ...] = ()
    carried: tuple[str, ...] = ()
    reasons: list[str] = []
    try:
        # One subagent at a time, so a failure is named by whose folder it was in and the
        # others still answer.
        own: dict[str, tuple[str, ...]] = {}
        for name, repository in resolved.bundled_tools.items():
            try:
                own[name] = bundled_tool_names(name, repository)
            except (ToolError, SubagentError) as exc:
                reasons.append(str(exc))
        tools = MappingProxyType(own)
        # Its own `try`: a `tools/` failure is `tools_error`'s, and in `bundles_error` too
        # it would send a reader to the wrong directory.
        try:
            catalogue = {one.name for one in resolved.tools.found}
        except (ToolError, CapabilityError):
            # `tools_error` names the file. `None`, not empty: this is not "nothing shadows".
            shadowed = None
        else:
            shadowed = MappingProxyType(
                {
                    name: found
                    for name, names in tools.items()
                    if (found := tuple(sorted(catalogue.intersection(names))))
                }
            )
        # Inside the `try`: a subagent carrying two skills of one name is refused here.
        skills = MappingProxyType(
            {
                name: tuple(sorted(registry.names))
                for name, registry in resolved.bundled_skills.items()
            }
        )
    except (ToolError, SubagentError) as exc:
        # `SubagentError`: reaching a bundle's tools reads the bundles first, which refuses
        # one subagent's folder holding two definitions. Joined to `reasons`, so both report.
        error = "; ".join([*reasons, str(exc)])
        return tools, skills, shadowed, error, orphans, carried

    error = "; ".join(reasons) or None
    orphans = tuple(resolved.subagents.orphaned_assets)
    # Asked of the backing, not the file extension: a `.py` under `subagents/` may own a
    # folder like any document.
    bundles = resolved.subagents.bundles
    carried = tuple(sorted(name for name, one in bundles.items() if one.root is None))
    return tools, skills, shadowed, error, orphans, carried


def _subject(kind: str, name: str) -> str:
    """A definition as a refusal words it, which is how `moved_tools` is keyed."""
    return f"{kind} {name!r}"


def _shut_out_of_bundles(
    resolved: Definitions, held: frozenset[str] | None
) -> Mapping[str, frozenset[str]]:
    """Each subagent's own tools whose own audience shuts this caller out.

    The build holds them back whatever the grant says, so a listing must not name them.
    """
    if held is None:
        return {}
    shut: dict[str, frozenset[str]] = {}
    # One subagent at a time: a bundle that will not load must not stop the others'.
    with suppress(SubagentError):
        for owner, bundle in resolved.bundled_tools.items():
            with suppress(ToolError, SubagentError):
                shut[owner] = frozenset(
                    one.name for one in bundle.found if not reaches(one.audience, held)
                )
    return shut


def _without(
    by_name: Mapping[str, tuple[str, ...]], shut: Mapping[str, frozenset[str]]
) -> Mapping[str, tuple[str, ...]]:
    """Each subagent's names less the ones `shut` holds back, and one left with none."""
    if not shut:
        return by_name
    return MappingProxyType(
        {
            name: kept
            for name, names in by_name.items()
            if (kept := tuple(one for one in names if one not in shut.get(name, ())))
        }
    )


def _kept[V](by_name: Mapping[str, V], reached: Container[str]) -> Mapping[str, V]:
    """Only the entries for definitions this caller reaches."""
    return MappingProxyType({name: one for name, one in by_name.items() if name in reached})


def _claimed_by(spec: object, held: frozenset[str] | None) -> Mapping[str, str]:
    """The `source:` each entry claims, of the entries this caller reaches.

    Through `declares(held)`, or a listing withholding a tool from `tools` names it here.
    A field that is not a list of entries cannot have narrowed, so its claims stand.
    """
    claimed = spec.tool_sources  # ty: ignore[unresolved-attribute]
    reached = spec.declares(held).tools  # ty: ignore[unresolved-attribute]
    if not isinstance(reached, tuple):
        return claimed
    # By the bare name, which `claimed_sources` keys on, where the selection holds the
    # reference as written: `moved/elsewhere.py::probe_one` against `probe_one`.
    named = {split_reference(one)[1] for one in reached}
    return {one: where for one, where in claimed.items() if one in named}


def _moved_tools(
    resolved: Definitions, held: frozenset[str] | None
) -> Mapping[str, tuple[str, ...]] | None:
    """Definitions naming a tool by a path it no longer lives at, or `None` when that
    could not be asked.

    Through the same `Offering` the refusal uses. `None` rather than raising: whichever
    kind failed already reports it on its own line.
    """
    try:
        offers = Offering.of(resolved.tools.found)
        # By the bare name, which is what a claim is keyed on.
        shut = {split_reference(one)[1] for one in out_of_reach(resolved.tools.found, held)}
        return MappingProxyType({
            _subject(kind, name): tuple(one for one, _claimed, _actual in moved)
            for kind, specs in (
                ("agent", resolved.agents.specs),
                ("subagent", resolved.subagents.specs),
            )
            for name, spec in specs.items()
            if (
                moved := offers.moved(
                    {one: at for one, at in _claimed_by(spec, held).items() if one not in shut}
                )
            )
        })
    except (ToolError, AgentError, SubagentError):
        return None


def _unresolved_subagents(
    resolved: Definitions, held: frozenset[str] | None
) -> Mapping[str, tuple[str, ...]] | None:
    """Definitions naming a subagent the catalogue has no key for, or `None` when
    that could not be asked.
    """
    try:
        defined = resolved.subagents.specs
        return MappingProxyType({
            _subject(kind, name): missing
            for kind, specs in (("agent", resolved.agents.specs), ("subagent", defined))
            for name, spec in specs.items()
            # As this caller reaches them: a name an entry audience keeps from them is not
            # theirs to be told. `*` is every key, so it cannot name a missing one.
            if isinstance(written := spec.declares(held).subagents, tuple)
            and (missing := unoffered(written, offered=defined))
        })
    except (AgentError, SubagentError):
        return None


def _miscounted_bundles(resolved: Definitions) -> Mapping[str, str] | None:
    """Definitions whose bundled entries no longer match their own folder, or `None`
    when that could not be asked.

    Through the rule `warm` refuses with, so a listing cannot call fine what startup
    refuses. `None` rather than raising: whichever kind failed is already on its own line.
    """
    found: dict[str, str] = {}
    try:
        for name, spec in resolved.subagents.specs.items():
            where, tools, skills = resolved.bundled(name)
            complaint = miscounted(
                spec,
                where=where,
                tools=tools,
                skills=skills,
                buried=resolved.bundled_misplaced.get(name, ()),
            )
            if complaint is not None:
                found[name] = complaint
    except (ToolError, SubagentError):
        return None
    return MappingProxyType(found)


def _middlewares(resolved: Definitions) -> tuple[Mapping[str, str], str | None]:
    """What the workspace registers, and why it could not be read."""
    try:
        return MappingProxyType({
            name: "" if defined_in_a_workspace(cls.__module__) else cls.__module__
            for name, cls in resolved.middlewares.classes.items()
        }), None
    except MiddlewareError as exc:
        return _NOTHING, str(exc)


def _audiences(specs: Mapping[str, object], *, kind: str) -> dict[str, Stated]:
    """What each definition of one kind says about who reaches what.

    Only those restricting somebody. Filtered here, not in the walk, whose other callers
    want what a definition says whether or not that is anything.
    """
    return {
        name: said
        for _kind, name, said in access.walked((kind, specs))
        if not said.says_nothing
    }


def _access(
    cfg: Config,
    *,
    agents: Mapping[str, object],
    subagents: Mapping[str, object],
    tools: Mapping[str, object],
    named_tools: Mapping[str, Found],
) -> tuple[dict[str, Mapping[str, Stated]], AccessReport, dict[str, str]]:
    """What the definitions say, and what restricts nobody."""
    stated = {
        "agents": _audiences(agents, kind="agent"),
        "subagents": _audiences(subagents, kind="subagent"),
        "tools": _audiences(named_tools, kind="tool"),
    }
    if cfg.access is None:
        return stated, AccessReport(), {}
    # The walk `Kingfisher` runs at construction, by the same functions, so a listing
    # cannot disagree with startup about who reaches what.
    kinds = (("agent", agents), ("subagent", subagents))
    report = access.audit(*kinds, vocabulary=cfg.access)
    broken = {
        f"{kind}s": complaint
        for kind, specs in (*kinds, ("tool", tools))
        if (complaint := access.undeclared_in(specs, kind=kind, vocabulary=cfg.access))
        is not None
    }
    return stated, report, broken


def _reaching(
    held: frozenset[str] | None, audiences: Mapping[str, Mapping[str, Stated]]
) -> Callable[[str, Mapping[str, str]], Mapping[str, str]]:
    """A filter keeping only the definitions this caller reaches, or the identity."""
    if held is None:
        return lambda _kind, names: names

    # Rebound so the type checker sees it narrowed inside the closure.
    reachable = held

    def keep(kind: str, names: Mapping[str, str]) -> Mapping[str, str]:
        stated = audiences.get(kind, {})
        return {
            name: value
            for name, value in names.items()
            if reaches(stated.get(name, Stated()).audience, reachable)
        }

    return keep


def _audienced_tools(resolved: Definitions) -> Mapping[str, Found]:
    """The tools that say who they admit, or none where the catalogue would not walk."""
    try:
        return resolved.audienced_tools
    except (ToolError, CapabilityError, SubagentError):
        return {}


def _named_tools(resolved: Definitions) -> dict[str, Found]:
    """The tools that say who they admit, named as the rest of a listing names them.

    A subagent's own under its subagent's name: `audienced_tools` keys it by a file the
    key's folder does not name, which sends a reader to `tools/`.
    """
    named: dict[str, Found] = {}
    # Apart, so a bundle that will not load still leaves the catalogue's tools said.
    with suppress(ToolError, CapabilityError):
        named.update(audienced(resolved.tools.found))
    with suppress(ToolError, CapabilityError, SubagentError):
        for owner, bundle in resolved.bundled_tools.items():
            named.update(
                {reference(owner, name): one for name, one in audienced(bundle.found).items()}
            )
    return named


def _shut_out(resolved: Definitions, held: frozenset[str] | None) -> frozenset[str]:
    """The tools whose own audience shuts this caller out, as the build would leave them.

    Nothing where the catalogue would not walk: `_tools` carries that failure.
    """
    try:
        return out_of_reach(resolved.tools.found, held)
    except (ToolError, CapabilityError):
        return frozenset()


def _builtin_tools(
    cfg: Config, resolved: Definitions, found: Sequence[Found] | None
) -> tuple[str, ...] | None:
    """The built-in set, asked of the harness that knows how to assemble one."""
    from kingfisher.core.agent.assembly import builtin_tool_names  # noqa: PLC0415

    return builtin_tool_names(cfg, resolved, found)


def _tools(
    cfg: Config, resolved: Definitions
) -> tuple[tuple[str, ...], tuple[str, ...], Mapping[str, str], str | None, bool, str | None]:
    """The built-in set, what the workspace adds, where each one lives, why not,
    whether the built-in set was read at all, and why no graph could be built.
    """
    builtin: tuple[str, ...] = ()
    workspace_tools: tuple[str, ...] = ()
    sources: Mapping[str, str] = _NOTHING
    tools_error: str | None = None
    checked = True
    try:
        # Inside the `try`: walking the catalogue is what raises, on two modules claiming
        # one tool name or a module that will not import.
        found = resolved.tools.found
        on_offer = Offering.of(found)
        workspace_tools = tuple(sorted(on_offer.workspace))
        sources = MappingProxyType(dict(on_offer.sources))
        # Walked once and handed to the build: a tool module is Python, and a second walk
        # runs every one twice.
        try:
            introspected = _builtin_tools(cfg, resolved, found)
        except ConfigError as exc:
            # The probe builds the default model and its backend, either of which may refuse
            # the deployment: not a fact about `tools/`, so not `tools_error`.
            return (), workspace_tools, sources, tools_error, False, str(exc)
        if introspected is None:
            # Compiled and then unreadable: the built-in set is unknown, not empty.
            tools_error = (
                "built-in tools could not be read from the compiled agent -- the graph "
                "has no shape this version recognises, so what it dispatches is unknown"
            )
        else:
            defined = {entry.name for entry in found}
            builtin = tuple(name for name in introspected if name not in defined)
    except (ToolError, CapabilityError) as exc:
        # `CapabilityError` too: the probe refuses a workspace tool replacing a built-in of
        # the same name, a fact about this deployment's tools.
        tools_error = str(exc)
    except MiddlewareError:
        # The probe reads the middleware directory too. Not `tools_error`: the tool catalogue
        # walked fine and `middlewares_error` names the file. Unchecked, not silently empty.
        checked = False
    # `None` for the build: only the early return above has a build error.
    return builtin, workspace_tools, sources, tools_error, checked, None


def inventory(
    cfg: Config,
    *,
    catalogue: Definitions | None = None,
    source_ids: Iterable[str] | None = None,
    datazone: str | None = None,
    sessions: object | None = None,
) -> Inventory:
    """Ask the workspace what it offers, through the catalogue a run would use."""
    resolved = catalogue if catalogue is not None else resolve_definitions(cfg)
    # Whose view this is, before the agents are read: each one's chain is what it
    # activates for this caller.
    held = access.held_by(cfg.access, with_datazone(source_ids, datazone))

    middlewares, middlewares_error = _middlewares(resolved)
    moved_tools = _moved_tools(resolved, held)
    unresolved_subagents = _unresolved_subagents(resolved, held)
    miscounted_bundles = _miscounted_bundles(resolved)

    builtin, workspace_tools, sources, tools_error, builtin_checked, build_error = _tools(
        cfg, resolved
    )

    registry = resolved.registry
    subagents: Mapping[str, str] = _NOTHING
    subagent_sources: Mapping[str, str] = _NOTHING
    subagents_error: str | None = None
    compiled_subagents: tuple[str, ...] = ()
    # Bound before the `try`: the audience walk reads it even when the catalogue will not load.
    specs: Mapping[str, object] = _NOTHING
    try:
        # `sources` in the same `try`: it parses the files `specs` does, and the property
        # raises rather than being absent.
        specs = resolved.subagents.specs
        # As at `build_agent`, not a duplicate: a cycle is the catalogue's, so a listing of it
        # must report what a run would refuse.
        refuse_cycles(specs)
        subagents = {name: spec.description for name, spec in specs.items()}
        compiled_subagents = tuple(
            name for name, spec in specs.items() if spec.build is not None
        )
        subagent_sources = MappingProxyType(dict(resolved.subagents.sources))
    except SubagentError as exc:
        subagents_error = str(exc)

    bundled_tools, bundled_skills, shadowed, bundles_error, orphaned_assets, carried = (
        _bundled(resolved)
        if subagents_error is None
        else (_NO_NAMES, _NO_NAMES, _NO_NAMES, None, (), ())
    )
    held_back = _shut_out_of_bundles(resolved, held)
    bundled_misplaced: Mapping[str, tuple[str, ...]] = _NO_NAMES
    if subagents_error is None and bundles_error is None:
        bundled_misplaced = resolved.bundled_misplaced

    agents: Mapping[str, str] = _NOTHING
    defined_agents: Mapping[str, object] = _NOTHING
    agent_sources: Mapping[str, str] = _NOTHING
    agent_subagents: Mapping[str, tuple[str, ...]] = _NO_NAMES
    agents_error: str | None = None
    try:
        # One `try`, as for subagents: `sources` parses the files `specs` does.
        defined_agents = resolved.agents.specs
        agents = {name: spec.description for name, spec in defined_agents.items()}
        agent_sources = MappingProxyType(dict(resolved.agents.sources))
        agent_subagents = MappingProxyType(
            {
                # `declares(held)`, which is what the build reads: an entry the agent's
                # own audience keeps from this caller is not activated for them.
                name: activated(spec.declares(held).subagents, resolved.subagents.specs)
                for name, spec in defined_agents.items()
            }
        )
    except (AgentError, SubagentError) as exc:
        # `SubagentError` too: resolving the chain reads the subagent catalogue.
        agents_error = str(exc)

    stated, report, broken = _access(
        cfg,
        agents=defined_agents,
        subagents=specs,
        tools=_audienced_tools(resolved),
        named_tools=_named_tools(resolved),
    )
    reaching = _reaching(held, stated)
    scoped = held is not None
    # A scoped view carries only what that caller reaches, and none of the policy. Decided
    # here, not per renderer, and computed once for every map to filter against: two
    # answers to what a caller reaches is how a field leaks.
    for_caller = reaching("agents", agents)
    # A subagent needs both: its own line admits this caller, and an agent they can open
    # names it. By the first alone, one nothing activates is listed, reachable by no path.
    reachable = handed_work_by(
        (spec for name, spec in defined_agents.items() if name in for_caller),
        specs,
        held,
    )
    for_caller_subagents = {
        name: description
        for name, description in reaching("subagents", subagents).items()
        if name in reachable
    }
    # Every field naming a subagent is kept to the same answer, or one names a subagent
    # this caller cannot open.
    reached_subjects = {
        *(_subject("agent", name) for name in for_caller),
        *(_subject("subagent", name) for name in for_caller_subagents),
    }
    # The catalogues, kept to what the definitions this caller reaches offer, not only by a
    # tool's own audience, which is `shut=`. Reachable definitions only: an entry written
    # `["*"]` means anyone reaching *this definition*, which a run gates by the caller
    # opening it and a listing must gate here. Agents, not the subagents they reach: a
    # subagent only narrows what reached it.
    reached_agents = [spec for name, spec in defined_agents.items() if name in for_caller]
    # `same` on both axes: a grant may spell a tool or a skill the long way, `where::what`,
    # where the catalogue has its own name for it.
    spelt = set(workspace_tools)
    offers_tool = admitted(
        offers("tools", held, reached_agents, same=lambda one: placed(one, spelt)),
        shut=_shut_out(resolved, held),
    )
    offers_skill = offers("skills", held, reached_agents, same=registry.identity)

    return Inventory(
        # The resolved catalogue, not `cfg`, so staged definitions are listed where they are.
        origins=Origins.of(cfg, catalogue=resolved, sessions=sessions),
        agents=MappingProxyType(dict(for_caller)),
        agent_sources=reaching("agents", agent_sources),
        # Keyed from the same walk as the names, so an unscoped view keeps every chain with
        # no branch of its own; another walk would silently drop some. A chain's names are
        # kept to `for_caller_subagents`: the build drops what a subagent's line withholds.
        agent_subagents=MappingProxyType(
            {
                name: tuple(one for one in chain if one in for_caller_subagents)
                for name, chain in agent_subagents.items()
                if name in for_caller
            }
        ),
        # `or`, not replace: a kind that failed to load has the more fundamental problem.
        # `broken` is policy, so withheld from a scoped view; a load error stays, since a
        # kind that will not load is one nobody gets, and an unexplained empty list is worse.
        agents_error=agents_error or (None if scoped else broken.get("agents")),
        builtin_tools=builtin,
        builtin_tools_checked=builtin_checked,
        tools=tuple(name for name in workspace_tools if offers_tool(name)),
        tool_sources=MappingProxyType(
            {name: where for name, where in sources.items() if offers_tool(name)}
        ),
        tools_error=tools_error or (None if scoped else broken.get("tools")),
        build_error=build_error,
        skills={
            name: registry.description(name) for name in registry.names if offers_skill(name)
        },
        # A misfiled skill is loaded and offered, so it filters as `skills` does. The
        # unloadable and misplaced never loaded, so nothing offers them by name: they are
        # the operator's diagnostics, which a scoped view carries none of.
        skills_unloadable=() if scoped else tuple(registry.unloadable),
        skills_misplaced=() if scoped else tuple(resolved.skills.misplaced),
        skills_misfiled=tuple(
            (where, name) for where, name in registry.misfiled if offers_skill(name)
        ),
        subagents=MappingProxyType(dict(for_caller_subagents)),
        # Keyed off the names above, not `reaching` again: a second walk answers a second
        # question, naming subagents no agent in reach hands work to.
        subagent_sources=_kept(subagent_sources, for_caller_subagents),
        subagents_error=subagents_error or (None if scoped else broken.get("subagents")),
        middlewares=middlewares,
        middlewares_error=middlewares_error,
        moved_tools=_NO_NAMES if moved_tools is None else _kept(moved_tools, reached_subjects),
        moved_tools_checked=moved_tools is not None,
        unresolved_subagents=(
            _NO_NAMES
            if unresolved_subagents is None
            else _kept(unresolved_subagents, reached_subjects)
        ),
        unresolved_subagents_checked=unresolved_subagents is not None,
        miscounted_bundles=(
            _NOTHING
            if miscounted_bundles is None
            else _kept(miscounted_bundles, for_caller_subagents)
        ),
        miscounted_bundles_checked=miscounted_bundles is not None,
        bundled_tools=_kept(_without(bundled_tools, held_back), for_caller_subagents),
        bundled_skills=_kept(bundled_skills, for_caller_subagents),
        # Here, not in `_bundled`, which does not know which definitions carry a graph.
        stranded_skills=MappingProxyType(
            {
                name: bundled_skills[name]
                for name in compiled_subagents
                if name in for_caller_subagents and bundled_skills.get(name)
            }
        ),
        bundled_misplaced=_kept(bundled_misplaced, for_caller_subagents),
        shadowed=(
            _kept(_without(shadowed, held_back), for_caller_subagents)
            if shadowed is not None
            else _NO_NAMES
        ),
        shadowed_checked=shadowed is not None,
        carried_bundles=tuple(name for name in carried if name in for_caller_subagents),
        bundles_error=bundles_error,
        # Empty for a scoped view: an orphan reaches nobody, so it is the operator's diagnostic.
        orphaned_assets=() if scoped else orphaned_assets,
        compiled_subagents=tuple(
            name for name in compiled_subagents if name in for_caller_subagents
        ),
        skills_enabled=cfg.skills_enabled,
        # The policy, so `None` and empty for a scoped view.
        access=None if scoped else cfg.access,
        access_report=AccessReport() if scoped else report,
        held=held,
        # And empty with no vocabulary: a `source_ids:` line is inert until `source_ids.yaml`
        # declares the names. Here, not in a renderer, because both forms are handed this.
        audiences=_NO_AUDIENCES if scoped or cfg.access is None else stated,
    )

"""What the definitions offer, as one answer instead of two half-answers."""

from __future__ import annotations

from collections.abc import Callable, Container, Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import NamedTuple

from .access import (
    ALL,
    AccessReport,
    Capabilities,
    CapabilityError,
    Selection,
    Stated,
    Vocabulary,
    activated,
    audit,
    handed_work_by,
    held_by,
    reaches,
    unoffered,
    walked,
    with_datazone,
)
from .config import Config, ConfigError
from .definitions.agents.spec import AgentError, AgentSpec
from .definitions.catalogue import (
    Definitions,
    DoubledSkill,
    bundled_tool_names,
    caller_offers,
    doubled_skill,
    resolve_definitions,
)
from .definitions.importing import defined_in_a_definition_file
from .definitions.middlewares.catalogue import MiddlewareError
from .definitions.prompts import unused_names
from .definitions.skill_locks import SkillLock, SkillLockError
from .definitions.skills.registry import SkillRegistry
from .definitions.subagents.rules import refuse_cycles
from .definitions.subagents.spec import SubagentError, SubagentSpec
from .definitions.tools.catalogue import ToolError
from .definitions.tools.spec import (
    FoundTool,
    Offering,
    reference,
    split_reference,
    tools_shut,
)
from .origins import Origins

#: An empty mapping that cannot be written to, so a default is shared safely.
_NOTHING: Mapping[str, str] = MappingProxyType({})
#: The same, typed for the fields whose values are name tuples.
_NO_NAMES: Mapping[str, tuple[str, ...]] = MappingProxyType({})
#: The same, for the nested audience record.
_NO_AUDIENCES: Mapping[str, Mapping[str, Stated]] = MappingProxyType({})


@dataclass(frozen=True)
class Grant:
    """What one grant field of one definition comes to, in this view."""

    #: Written `["*"]`, which `names` alone cannot say: a star covering nothing today
    #: still covers whatever is added tomorrow.
    every: bool = False
    #: An agent's star expanded to what it covers today. A subagent's is not expanded: it
    #: holds whatever the agent calling it holds, so these are what it holds besides.
    names: tuple[str, ...] = ()


#: The same empty mapping, for the grant fields.
_NO_GRANTS: Mapping[str, Grant] = MappingProxyType({})


@dataclass(frozen=True)
class Catalogued:
    """One definition kind's catalogue, as this walk found it.

    Not `Kind`: `origins.Kind` already means what sort of *place* something was read from.
    """

    name: str
    #: What stopped the directory being read, or `None`. A skill that does not load is not
    #: this but `skills_unloadable`, `skills_misplaced` or `skills_misfiled`: the rest load.
    error: str | None
    #: How many the agent can reach. Zero is a fact about the definitions, not a failure.
    defined: int
    #: What one of these is on disk, for a message saying what to fix: a file or a module.
    written_as: str


@dataclass(frozen=True)
class Inventory:
    """What the definitions offer right now, per kind, with where each came from."""

    #: Where every part of this deployment was read from, every catalogue directory included.
    origins: Origins

    #: Agent name -> its description. First, as in the listing: an agent is what a request
    #: names, and the other kinds are what it selects from.
    agents: Mapping[str, str] = _NOTHING
    #: Agent name -> the file it came from.
    agent_sources: Mapping[str, str] = _NOTHING
    #: The subagents each agent activates: the ones its own file names, and no further.
    #: Walking their helpers would list a subagent the build drops.
    agent_subagents: Mapping[str, Grant] = _NO_GRANTS
    #: What each agent holds on the two tool axes. A tool is spelt as `tools` spells it,
    #: so one reads the same under every agent whichever way each file wrote it.
    agent_tools: Mapping[str, Grant] = _NO_GRANTS
    agent_builtin_tools: Mapping[str, Grant] = _NO_GRANTS
    #: What each agent stops for a person on, as its `interrupt_on` names them. Not a
    #: `Grant`: the field takes no star, so there is nothing for one to expand.
    agent_gates: Mapping[str, tuple[str, ...]] = _NOTHING
    agents_error: str | None = None

    #: The agent's own tools, granted with `builtin_tools:`. Empty where the build failed,
    #: which `tools_error` says, and where it could not be read, which only
    #: `builtin_tools_checked` says.
    builtin_tools: tuple[str, ...] = ()
    #: Whether the built-in set was read at all: `False` where a middleware that will not
    #: import took the probe down. That fault is `middlewares_error`'s, so `tools_error`
    #: stays clear and this says the count is unknown rather than zero.
    builtin_tools_checked: bool = True
    #: The offered tools, granted with `tools:`. A separate axis from
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
    #: Why `skill_locks.yaml` will not load, or `None`. Startup refuses the same file.
    skill_locks_error: str | None = None
    #: Why the skills catalogue could not be read at all, which startup refuses. The skills
    #: fields are then empty, and carried rather than raised so every other kind still lists.
    skills_error: str | None = None

    #: Subagent name -> its description.
    subagents: Mapping[str, str] = _NOTHING
    #: Subagent name -> the file it came from.
    subagent_sources: Mapping[str, str] = _NOTHING
    #: What each subagent's own file grants it on the two tool axes. Its own tools are
    #: `owner::tool`, or one shadowing the catalogue's reads as the catalogue's.
    subagent_tools: Mapping[str, Grant] = _NO_GRANTS
    subagent_builtin_tools: Mapping[str, Grant] = _NO_GRANTS
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

    #: Definition -> the tools it names, on either line, that nothing offers there as
    #: written, keyed as `moved_tools` is. Startup loads it; every run reaching it refuses.
    unresolved_tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Whether `unresolved_tools` was asked at all; `False` is not "none".
    unresolved_tools_checked: bool = True

    #: Definition -> how it names one skill twice, keyed as `moved_tools` is. Startup
    #: refuses this, and `doctor` promises to say in advance what startup refuses.
    doubled_skills: Mapping[str, str] = _NOTHING
    #: Whether `doubled_skills` was asked at all; `False` is not "none".
    doubled_skills_checked: bool = True

    #: Definition -> the source ids and datazones its prompt slots are keyed on that no lock
    #: uses, keyed as `moved_tools` is. Startup refuses this, and `doctor` promises to say
    #: in advance what startup refuses.
    unused_slot_keys: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: Whether `unused_slot_keys` was asked at all; `False` is not "none".
    unused_slot_keys_checked: bool = True

    #: Definitions whose `source: bundled` entries and own folder disagree, with how.
    #: Startup refuses this, and `doctor` promises to say in advance what startup refuses.
    miscounted_bundles: Mapping[str, str] = _NOTHING
    #: Whether `miscounted_bundles` was asked at all; `False` is not "none".
    miscounted_bundles_checked: bool = True

    #: Middleware class name -> the installed module it came from, or `""` for a definition
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
    #: Whether `shadowed` was asked at all: it needs the shared catalogue, and one that
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
    #: `tools/` and `skills/` directly under `subagents/` that nothing reads: a definition
    #: filed in one does not exist, and nothing else says so.
    unread_folders: tuple[str, ...] = ()

    #: Subagents that are graphs their definitions built: deepagents runs one as given, so a
    #: `tools:` grant is no limit on it.
    compiled_subagents: tuple[str, ...] = ()

    #: Whether an empty `skills` means "none" or "switched off".
    skills_enabled: bool = True

    #: The source ids and datazones the definitions use, and which use each, or `None`
    #: where nothing is locked. Carried, not looked up by the printer: a renderer asking the
    #: catalogue would be a second place deciding what is said.
    vocabulary: Vocabulary | None = None
    #: What the locks leave open. Empty when nothing is.
    access_report: AccessReport = field(default_factory=AccessReport)
    #: Definition kind -> name -> who reaches it and each skill it narrows, for every
    #: definition saying who reaches what, a tool named as a grant writes it and a
    #: subagent's own as `subagent::tool`. Carried so no renderer reads the specs itself.
    audiences: Mapping[str, Mapping[str, Stated]] = _NO_AUDIENCES
    #: Whose view this is, or `None` for the operator's view of everything. Set,
    #: every field is answered for this caller here, so the printer never filters: a field
    #: naming a definition keeps what the caller reaches, a catalogue keeps what those
    #: definitions offer, and the policy and faults reaching nobody are dropped.
    held: frozenset[str] | None = None

    def by_kind(self) -> tuple[Catalogued, ...]:
        """Every definition kind, in the order a listing shows them.

        Spelled out rather than read by name, so a type checker follows the fields.
        """
        return (
            Catalogued("agents", self.agents_error, len(self.agents), "file"),
            Catalogued("skills", self.skills_error, len(self.skills), "folder"),
            Catalogued("subagents", self.subagents_error, len(self.subagents), "file"),
            Catalogued("tools", self.tools_error, len(self.tools), "module"),
            Catalogued("middlewares", self.middlewares_error, len(self.middlewares), "module"),
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


class _Bundles(NamedTuple):
    """What each subagent brings itself, by name, and the folders that bring it to nobody."""

    tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    #: `None` where the shared catalogue would not load, which is not "nothing shadows".
    shadowed: Mapping[str, tuple[str, ...]] | None = _NO_NAMES
    error: str | None = None
    orphans: tuple[str, ...] = ()
    unread: tuple[str, ...] = ()
    #: Subagents whose bundle arrived with the definition rather than in a folder.
    carried: tuple[str, ...] = ()


def _bundled(resolved: Definitions) -> _Bundles:
    """What each subagent brings itself, for a listing, and the folders that bring it to nobody.

    Orphans are decided by the same `bundles` read, so asking apart needs a second `try`.
    """
    tools: Mapping[str, tuple[str, ...]] = _NO_NAMES
    skills: Mapping[str, tuple[str, ...]] = _NO_NAMES
    shadowed: Mapping[str, tuple[str, ...]] | None = _NO_NAMES
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
        return _Bundles(
            tools=tools, skills=skills, shadowed=shadowed, error="; ".join([*reasons, str(exc)])
        )

    # Asked of the backing, not the file extension: a `.py` under `subagents/` may own a
    # folder like any document.
    bundles = resolved.subagents.bundles
    return _Bundles(
        tools=tools,
        skills=skills,
        shadowed=shadowed,
        error="; ".join(reasons) or None,
        orphans=tuple(resolved.subagents.orphaned_assets),
        unread=resolved.subagents.unread_folders,
        carried=tuple(sorted(name for name, one in bundles.items() if one.root is None)),
    )


def _subject(kind: str, name: str) -> str:
    """A definition as a refusal words it, which is how `moved_tools` is keyed."""
    return f"{kind} {name!r}"


def _bundled_tools_shut(
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


def _among(
    by_name: Mapping[str, tuple[str, ...]], told: Container[str]
) -> Mapping[str, tuple[str, ...]]:
    """Each subagent's names that `told` holds, less one left with none."""
    return MappingProxyType(
        {
            name: kept
            for name, names in by_name.items()
            if (kept := tuple(one for one in names if one in told))
        }
    )


def _kept[V](by_name: Mapping[str, V], reached: Container[str]) -> Mapping[str, V]:
    """Only the entries for definitions this caller reaches."""
    return MappingProxyType({name: one for name, one in by_name.items() if name in reached})


def _grant(
    written: Selection,
    *,
    today: Iterable[str] = (),
    own: Iterable[str] = (),
    spell: Callable[[str], str] = lambda one: one,
    keep: Callable[[str], bool] = lambda _one: True,
) -> Grant:
    """One grant field as this view reads it.

    `today` is what a star covers and `own` what the definition holds whatever it was
    granted. `keep` sees each entry as `spell` writes it, which is how its answer is keyed.
    """
    if written == ALL:
        return Grant(every=True, names=tuple(sorted({*today, *own})))
    named = {spelt for one in written or () if keep(spelt := spell(one))}
    return Grant(names=tuple(sorted({*named, *own})))


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
        shut = {split_reference(one)[1] for one in tools_shut(resolved.tools.found, held)}
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
            # As this caller reaches them: a name in a definition they cannot open is not
            # theirs to be told. `*` is every key, so it cannot name a missing one.
            if isinstance(written := spec.declares(held).subagents, tuple)
            and (missing := unoffered(written, offered=defined))
        })
    except (AgentError, SubagentError):
        return None


def _unresolved_tools(
    resolved: Definitions, builtin: tuple[str, ...]
) -> Mapping[str, tuple[str, ...]] | None:
    """Definitions naming a tool their line does not offer, or `None` when that could
    not be asked.

    As written, not as a caller reaches them: a run refuses the file's own names whoever
    is asking. The caller decides only which definitions are reported.
    """
    try:
        offers = Offering.of(resolved.tools.found, builtin=builtin)
        return MappingProxyType({
            _subject(kind, name): missing
            for kind, specs in (
                ("agent", resolved.agents.specs),
                ("subagent", resolved.subagents.specs),
            )
            for name, spec in specs.items()
            if (missing := offers.unknown(spec.builtin_tools, spec.tools))
        })
    except (ToolError, AgentError, SubagentError):
        return None


#: A doubled skill as `doubled_skills` words it to a caller who is not told of the skill.
_DOUBLED_UNNAMED = "skills names one skill twice, bare and qualified"


def _doubled_skills(resolved: Definitions) -> Mapping[str, DoubledSkill] | None:
    """Definitions naming one skill twice, bare and qualified, or `None` when that could
    not be asked.

    Through the rule `warm` refuses with, and as written rather than as a caller reaches
    it: startup refuses the file whoever is asking.
    """
    try:
        return MappingProxyType({
            _subject(kind, name): found
            for kind, specs in (
                ("agent", resolved.agents.specs),
                ("subagent", resolved.subagents.specs),
            )
            for name, spec in specs.items()
            if (found := doubled_skill(spec.skills, resolved.registry)) is not None
        })
    except (AgentError, SubagentError, ConfigError):
        # `ConfigError`: the skills catalogue would not read, which `skills_error` carries.
        return None


def _unused_slot_keys(resolved: Definitions) -> Mapping[str, tuple[str, ...]] | None:
    """Definitions keying a prompt slot on a name no lock uses, or `None` when that could
    not be asked.

    Through the names `warm` refuses, against the whole deployment's locks: a slot key is
    refused whoever is asking.
    """
    try:
        # Read first, as `warm` reads them: one that will not load leaves its locks out of
        # the vocabulary, and a slot keyed on a name only those use would read as misspelt.
        _ = resolved.tools.found, resolved.skill_locks
        return MappingProxyType({
            _subject(kind, name): unused
            for kind, specs in (
                ("agent", resolved.agents.specs),
                ("subagent", resolved.subagents.specs),
            )
            for name, spec in specs.items()
            if (unused := unused_names(spec.slots, resolved.vocabulary))
        })
    except (AgentError, SubagentError, ToolError, SkillLockError):
        return None


def _miscounted_bundles(resolved: Definitions) -> Mapping[str, str] | None:
    """Definitions whose bundled entries no longer match their own folder, or `None`
    when that could not be asked.

    Through the rule `warm` refuses with, so a listing cannot call fine what startup
    refuses. `None` rather than raising: whichever kind failed is already on its own line.
    """
    found: dict[str, str] = {}
    try:
        for name in resolved.subagents.specs:
            if (complaint := resolved.miscounted(name)) is not None:
                found[name] = complaint
    except (ToolError, SubagentError):
        return None
    return MappingProxyType(found)


def _middlewares(resolved: Definitions) -> tuple[Mapping[str, str], str | None]:
    """What the definitions register, and why it could not be read."""
    try:
        return MappingProxyType({
            name: "" if defined_in_a_definition_file(cls.__module__) else cls.__module__
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
        for _kind, name, said in walked((kind, specs))
        if not said.says_nothing
    }


def _access(
    vocabulary: Vocabulary | None,
    *,
    agents: Mapping[str, object],
    subagents: Mapping[str, object],
    named_tools: Mapping[str, FoundTool],
    named_skills: Mapping[str, SkillLock],
) -> tuple[dict[str, Mapping[str, Stated]], AccessReport]:
    """What the definitions say, and what restricts nobody."""
    stated = {
        "agents": _audiences(agents, kind="agent"),
        "subagents": _audiences(subagents, kind="subagent"),
        "tools": _audiences(named_tools, kind="tool"),
        "skills": _audiences(named_skills, kind="skill"),
    }
    if vocabulary is None:
        return stated, AccessReport()
    # Tools and skills are not audited: one with no lock is open to whoever opens an
    # agent listing it.
    return stated, audit(("agent", agents), ("subagent", subagents))


def _keep_reached(
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


def _tools_shut(resolved: Definitions, held: frozenset[str] | None) -> frozenset[str]:
    """The tools whose own audience shuts this caller out, as the build would leave them.

    Nothing where the catalogue would not walk: `_tools` carries that failure.
    """
    try:
        return tools_shut(resolved.tools.found, held)
    except (ToolError, CapabilityError):
        return frozenset()


def _skill_locks(resolved: Definitions) -> tuple[Mapping[str, SkillLock], str | None]:
    """Each lock `skill_locks.yaml` writes, by the name it writes, and why the file will
    not load where it will not."""
    try:
        # The skills catalogue not reading is `skills_error`'s: what each lock means is then
        # unknown, and what the file writes still stands.
        with suppress(ConfigError):
            _ = resolved.locked_skills
    except SkillLockError as exc:
        return {}, str(exc)
    return {lock.name: lock for lock in resolved.skill_locks}, None


def _registry(resolved: Definitions) -> tuple[SkillRegistry, str | None]:
    """What the agent will be told about, and why the skills catalogue could not be read.

    Empty where it could not, so every other kind still lists.
    """
    try:
        return resolved.registry, None
    except ConfigError as exc:
        return SkillRegistry(offered={}), str(exc)


def _skills_shut(
    resolved: Definitions, held: frozenset[str] | None, registry: SkillRegistry
) -> frozenset[str]:
    """The skills whose own lock shuts this caller out, as `source::name`.

    Every skill where the locks will not load: a lock that cannot be read cannot be passed,
    and `_skill_locks` carries the fault. `registry` is the one `_registry` read, which is
    empty where the skills would not.
    """
    try:
        return resolved.skills_shut(held)
    except (SkillLockError, ConfigError):
        return frozenset() if held is None else frozenset(registry.offered)


class _Tools(NamedTuple):
    """The built-in set, what the definitions add, where each one lives, and why not."""

    builtin: tuple[str, ...] = ()
    offered: tuple[str, ...] = ()
    sources: Mapping[str, str] = _NOTHING
    error: str | None = None
    #: Whether the built-in set was read at all.
    builtin_checked: bool = True
    #: Why no graph could be built, which is not a fact about `tools/`.
    build_error: str | None = None


def _tools(cfg: Config, resolved: Definitions) -> _Tools:
    """The built-in set, asked of the harness that knows how to assemble one, and the rest."""
    from .agent.assembly import builtin_tool_names  # noqa: PLC0415

    builtin: tuple[str, ...] = ()
    offered_tools: tuple[str, ...] = ()
    sources: Mapping[str, str] = _NOTHING
    tools_error: str | None = None
    checked = True
    try:
        # Inside the `try`: walking the catalogue is what raises, on two modules claiming
        # one tool name or a module that will not import.
        found = resolved.tools.found
        on_offer = Offering.of(found)
        offered_tools = tuple(sorted(on_offer.offered))
        sources = MappingProxyType(dict(on_offer.sources))
        # Walked once and handed to the build: a tool module is Python, and a second walk
        # runs every one twice.
        try:
            introspected = builtin_tool_names(cfg, resolved, found)
        except ConfigError as exc:
            # The probe builds the default model and its backend, either of which may refuse
            # the deployment: not a fact about `tools/`, so not `tools_error`.
            return _Tools(
                offered=offered_tools, sources=sources, builtin_checked=False, build_error=str(exc)
            )
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
        # `CapabilityError` too: the probe refuses an offered tool replacing a built-in of
        # the same name, a fact about this deployment's tools.
        tools_error = str(exc)
    except MiddlewareError:
        # The probe reads the middleware directory too. Not `tools_error`: the tool catalogue
        # walked fine and `middlewares_error` names the file. Unchecked, not silently empty.
        checked = False
    # No build error: only the early return above has one.
    return _Tools(
        builtin=builtin,
        offered=offered_tools,
        sources=sources,
        error=tools_error,
        builtin_checked=checked,
    )


class _Agents(NamedTuple):
    """Each agent, what it declares for this caller, its description, the file it came
    from, the subagents it activates, and why they could not all be read."""

    specs: Mapping[str, AgentSpec]
    declared: Mapping[str, Capabilities]
    described: Mapping[str, str]
    sources: Mapping[str, str]
    chains: Mapping[str, tuple[str, ...]]
    error: str | None


def _agents(resolved: Definitions, held: frozenset[str] | None) -> _Agents:
    """Every agent, as this caller would have it built.

    What was read before a failure comes back with it: a listing of broken definitions
    still shows what it can.
    """
    specs: Mapping[str, AgentSpec] = MappingProxyType({})
    declared: Mapping[str, Capabilities] = MappingProxyType({})
    described: Mapping[str, str] = _NOTHING
    sources: Mapping[str, str] = _NOTHING
    chains: Mapping[str, tuple[str, ...]] = _NO_NAMES
    error: str | None = None
    try:
        # One `try`, as for subagents: `sources` parses the files `specs` does.
        specs = resolved.agents.specs
        # `declares(held)`, which is what the build reads: an entry the agent's own
        # audience keeps from this caller is not activated for them.
        declared = {name: spec.declares(held) for name, spec in specs.items()}
        described = {name: spec.description for name, spec in specs.items()}
        sources = MappingProxyType(dict(resolved.agents.sources))
        chains = MappingProxyType(
            {
                name: activated(grants.subagents, resolved.subagents.specs)
                for name, grants in declared.items()
            }
        )
    except (AgentError, SubagentError) as exc:
        # `SubagentError` too: resolving the chain reads the subagent catalogue.
        error = str(exc)
    return _Agents(
        specs=specs,
        declared=declared,
        described=described,
        sources=sources,
        chains=chains,
        error=error,
    )


class _Subagents(NamedTuple):
    """Each subagent, what it declares for this caller, its description, the file it came
    from, which are graphs their definitions built, and why they could not all be read."""

    specs: Mapping[str, SubagentSpec]
    declared: Mapping[str, Capabilities]
    described: Mapping[str, str]
    sources: Mapping[str, str]
    compiled: tuple[str, ...]
    error: str | None


def _subagents(resolved: Definitions, held: frozenset[str] | None) -> _Subagents:
    """Every subagent, as this caller would have it built.

    What was read before a failure comes back with it, as for agents: the audience walk
    reads the specs even where a cycle refuses the catalogue.
    """
    specs: Mapping[str, SubagentSpec] = MappingProxyType({})
    declared: Mapping[str, Capabilities] = MappingProxyType({})
    described: Mapping[str, str] = _NOTHING
    sources: Mapping[str, str] = _NOTHING
    compiled: tuple[str, ...] = ()
    error: str | None = None
    try:
        # `sources` in the same `try`: it parses the files `specs` does, and the property
        # raises rather than being absent.
        specs = resolved.subagents.specs
        # As at `build_agent`, not a duplicate: a cycle is the catalogue's, so a listing of it
        # must report what a run would refuse.
        refuse_cycles(specs)
        declared = {name: spec.declares(held) for name, spec in specs.items()}
        described = {name: spec.description for name, spec in specs.items()}
        compiled = tuple(name for name, spec in specs.items() if spec.build is not None)
        sources = MappingProxyType(dict(resolved.subagents.sources))
    except SubagentError as exc:
        error = str(exc)
    return _Subagents(
        specs=specs,
        declared=declared,
        described=described,
        sources=sources,
        compiled=compiled,
        error=error,
    )


def inventory(  # noqa: PLR0913 -- the caller's three, beside where to look
    cfg: Config,
    *,
    catalogue: Definitions | None = None,
    source_ids: Iterable[str] | None = None,
    datazone: str | None = None,
    extra_datazones: Sequence[str] = (),
    sessions: object | None = None,
) -> Inventory:
    """Ask the definitions what they offer, through the catalogue a run would use."""
    resolved = catalogue if catalogue is not None else resolve_definitions(cfg)
    # Whose view this is, before the agents are read: each one's chain is what it
    # activates for this caller.
    held = held_by(resolved.vocabulary, with_datazone(source_ids, datazone, extra_datazones))
    return inventory_for(cfg, catalogue=resolved, held=held, sessions=sessions)


def inventory_for(
    cfg: Config,
    *,
    catalogue: Definitions,
    held: frozenset[str] | None,
    sessions: object | None = None,
) -> Inventory:
    """`inventory`, for a caller whose names a turn has already resolved."""
    middlewares, middlewares_error = _middlewares(catalogue)
    moved_tools = _moved_tools(catalogue, held)
    unresolved_subagents = _unresolved_subagents(catalogue, held)
    registry, skills_error = _registry(catalogue)
    doubled_skills = _doubled_skills(catalogue)
    unused_slot_keys = _unused_slot_keys(catalogue)
    miscounted_bundles = _miscounted_bundles(catalogue)

    tools = _tools(cfg, catalogue)
    # Not asked without the built-in set: every name on a `builtin_tools:` line would
    # read as unknown.
    unresolved_tools = (
        _unresolved_tools(catalogue, tools.builtin)
        if tools.builtin_checked and tools.build_error is None and tools.error is None
        else None
    )

    subagents = _subagents(catalogue, held)
    bundles = _bundled(catalogue) if subagents.error is None else _Bundles()
    bundled_shut = _bundled_tools_shut(catalogue, held)
    bundled_misplaced: Mapping[str, tuple[str, ...]] = _NO_NAMES
    if subagents.error is None and bundles.error is None:
        bundled_misplaced = catalogue.bundled_misplaced

    agents = _agents(catalogue, held)

    named_skills, skill_locks_error = _skill_locks(catalogue)
    stated, report = _access(
        catalogue.vocabulary,
        agents=agents.specs,
        subagents=subagents.specs,
        named_tools=catalogue.locked_tools,
        named_skills=named_skills,
    )
    keep_reached = _keep_reached(held, stated)
    scoped = held is not None
    # A scoped view carries only what that caller reaches, and none of the policy. Decided
    # here, not per renderer, and computed once for every map to filter against: two
    # answers to what a caller reaches is how a field leaks.
    for_caller = keep_reached("agents", agents.described)
    # A subagent needs both: its own line admits this caller, and an agent they can open
    # names it. By the first alone, one nothing activates is listed, reachable by no path.
    reachable = handed_work_by(
        (spec for name, spec in agents.specs.items() if name in for_caller),
        subagents.specs,
        held,
    )
    for_caller_subagents = {
        name: description
        for name, description in keep_reached("subagents", subagents.described).items()
        if name in reachable
    }
    # Every field naming a subagent is kept to the same answer, or one names a subagent
    # this caller cannot open.
    reached_subjects = {
        *(_subject("agent", name) for name in for_caller),
        *(_subject("subagent", name) for name in for_caller_subagents),
    }
    # The catalogues, kept to what the definitions this caller reaches offer, not only by a
    # tool's own audience, which is `tools_shut=`. Reachable definitions only: a field written
    # `["*"]` is narrowed by no definition's own line, which a run gates by the caller
    # opening it and a listing must gate here. Agents, not the subagents they reach: a
    # subagent only narrows what reached it.
    reached = caller_offers(
        held,
        [spec for name, spec in agents.specs.items() if name in for_caller],
        tools=tools.offered,
        tools_shut=_tools_shut(catalogue, held),
        skills_shut=_skills_shut(catalogue, held, registry),
        identity=registry.identity,
    )
    # Each definition's lines through the answers above rather than a walk of their own: a
    # tool kept out of `tools` for this caller must not come back under whoever grants it.
    for_caller_tools = tuple(name for name in tools.offered if reached.tool(name))
    # By the bare name, which `shadowed` holds: a bundle answering for a catalogue tool this
    # caller is not offered would tell them the tool exists.
    told_tools = {split_reference(one)[1] for one in for_caller_tools}
    own_tools = _kept(_without(bundles.tools, bundled_shut), for_caller_subagents)
    # A gate also stops a call one of the agent's own subagents makes, so a tool in its bundle
    # is a gate's to keep. Not a compiled one's: deepagents hands that graph none of the gates.
    delegated = {
        name: {
            tool
            for one in chain
            if one in for_caller_subagents and one not in subagents.compiled
            for tool in own_tools.get(one, ())
        }
        for name, chain in agents.chains.items()
    }

    return Inventory(
        # The resolved catalogue, not `cfg`, so staged definitions are listed where they are.
        origins=Origins.of(cfg, catalogue=catalogue, sessions=sessions),
        agents=MappingProxyType(dict(for_caller)),
        agent_sources=keep_reached("agents", agents.sources),
        # Keyed from the same walk as the names, so an unscoped view keeps every chain with
        # no branch of its own; another walk would silently drop some. A chain's names are
        # kept to `for_caller_subagents`: the build drops what a subagent's line withholds.
        agent_subagents=MappingProxyType(
            {
                name: Grant(
                    every=agents.declared[name].subagents == ALL,
                    names=tuple(one for one in chain if one in for_caller_subagents),
                )
                for name, chain in agents.chains.items()
                if name in for_caller
            }
        ),
        agent_tools=MappingProxyType(
            {
                name: _grant(
                    agents.declared[name].tools,
                    today=for_caller_tools,
                    spell=reached.spell,
                    keep=reached.tool,
                )
                for name in for_caller
            }
        ),
        # Not narrowed for a caller: a built-in has no audience, and is the same for all.
        agent_builtin_tools=MappingProxyType(
            {
                name: _grant(agents.declared[name].builtin_tools, today=tools.builtin)
                for name in for_caller
            }
        ),
        agent_gates=MappingProxyType(
            {
                name: tuple(
                    one
                    for one in agents.specs[name].interrupt_on
                    # Filtered as the tools line is: a gate names a tool, and a name this
                    # caller's locks shut out must not arrive under an agent they can open.
                    # A built-in has no audience, so it is shown to everyone.
                    if one in tools.builtin or reached.tool(one) or one in delegated.get(name, ())
                )
                for name in for_caller
            }
        ),
        # Kept for a scoped view: a kind that will not load is one nobody gets, and an
        # unexplained empty list is worse.
        agents_error=agents.error,
        builtin_tools=tools.builtin,
        builtin_tools_checked=tools.builtin_checked,
        tools=for_caller_tools,
        tool_sources=MappingProxyType(
            {name: where for name, where in tools.sources.items() if reached.tool(name)}
        ),
        tools_error=tools.error,
        build_error=tools.build_error,
        skills={
            name: registry.description(name) for name in registry.names if reached.skill(name)
        },
        # A misfiled skill is loaded and offered, so it filters as `skills` does. The
        # unloadable and misplaced never loaded, so nothing offers them by name: they are
        # the operator's diagnostics, which a scoped view carries none of.
        skills_unloadable=() if scoped else tuple(registry.unloadable),
        skills_misplaced=() if scoped else tuple(catalogue.skills.misplaced),
        skills_misfiled=tuple(
            (where, name) for where, name in registry.misfiled if reached.skill(name)
        ),
        # The operator's, like the other faults: who a skill is locked to is policy.
        skill_locks_error=None if scoped else skill_locks_error,
        # Kept for a scoped view, as `agents_error` is: a catalogue that will not read gives
        # nobody a skill, and startup refuses it whoever is asking.
        skills_error=skills_error,
        subagents=MappingProxyType(dict(for_caller_subagents)),
        # Keyed off the names above, not `keep_reached` again: a second walk answers a second
        # question, naming subagents no agent in reach hands work to.
        subagent_sources=_kept(subagents.sources, for_caller_subagents),
        # `keep` as for an agent's, which also drops what no agent in reach holds: a
        # subagent is never handed more than the agent calling it.
        subagent_tools=MappingProxyType(
            {
                name: _grant(
                    subagents.declared[name].tools,
                    own=(reference(name, one) for one in own_tools.get(name, ())),
                    spell=reached.spell,
                    keep=reached.tool,
                )
                for name in for_caller_subagents
            }
        ),
        subagent_builtin_tools=MappingProxyType(
            {
                name: _grant(subagents.declared[name].builtin_tools)
                for name in for_caller_subagents
            }
        ),
        subagents_error=subagents.error,
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
        unresolved_tools=(
            _NO_NAMES if unresolved_tools is None else _kept(unresolved_tools, reached_subjects)
        ),
        unresolved_tools_checked=unresolved_tools is not None,
        # Kept for a caller not told of the skill, since startup refuses the file whoever
        # asks, but without its name.
        doubled_skills=(
            _NOTHING
            if doubled_skills is None
            else MappingProxyType(
                {
                    subject: found.said if reached.skill(found.meant) else _DOUBLED_UNNAMED
                    for subject, found in _kept(doubled_skills, reached_subjects).items()
                }
            )
        ),
        doubled_skills_checked=doubled_skills is not None,
        # Empty for a scoped view: what a slot is keyed on is policy, as the locks are.
        unused_slot_keys=_NO_NAMES if scoped or unused_slot_keys is None else unused_slot_keys,
        unused_slot_keys_checked=unused_slot_keys is not None,
        miscounted_bundles=(
            _NOTHING
            if miscounted_bundles is None
            else _kept(miscounted_bundles, for_caller_subagents)
        ),
        miscounted_bundles_checked=miscounted_bundles is not None,
        bundled_tools=own_tools,
        bundled_skills=_kept(bundles.skills, for_caller_subagents),
        # Here, not in `_bundled`, which does not know which definitions carry a graph.
        stranded_skills=MappingProxyType(
            {
                name: bundles.skills[name]
                for name in subagents.compiled
                if name in for_caller_subagents and bundles.skills.get(name)
            }
        ),
        bundled_misplaced=_kept(bundled_misplaced, for_caller_subagents),
        shadowed=(
            _kept(
                _among(_without(bundles.shadowed, bundled_shut), told_tools),
                for_caller_subagents,
            )
            if bundles.shadowed is not None
            else _NO_NAMES
        ),
        shadowed_checked=bundles.shadowed is not None,
        carried_bundles=tuple(name for name in bundles.carried if name in for_caller_subagents),
        bundles_error=bundles.error,
        # Empty for a scoped view: an orphan reaches nobody, so it is the operator's diagnostic.
        orphaned_assets=() if scoped else bundles.orphans,
        unread_folders=() if scoped else bundles.unread,
        compiled_subagents=tuple(
            name for name in subagents.compiled if name in for_caller_subagents
        ),
        skills_enabled=cfg.skills_enabled,
        # What the locks are, so `None` and empty for a scoped view.
        vocabulary=None if scoped else catalogue.vocabulary,
        access_report=AccessReport() if scoped else report,
        held=held,
        # And empty where nothing is locked, rather than a kind per empty mapping. Here,
        # not in a renderer, because both forms are handed this.
        audiences=_NO_AUDIENCES if scoped or catalogue.vocabulary is None else stated,
    )

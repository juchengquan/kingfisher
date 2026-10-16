"""Who reaches what: the rule a definition's audience applies to the names a caller
holds."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from kingfisher.core.access.capabilities import ALL, Selection
from kingfisher.core.access.source_ids import AUDIENCED, Audience, Stated


def reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches something with this audience."""
    if audience == ALL:
        return True
    return any(one <= held if isinstance(one, frozenset) else one in held for one in audience)


def reaching(
    selection: Selection,
    *,
    audiences: Mapping[str, Audience],
    default: Audience,
    held: frozenset[str],
) -> Selection:
    """`selection`, keeping only the entries this caller reaches."""
    if selection == ALL or selection is None:
        return selection
    return tuple(name for name in selection if reaches(audiences.get(name, default), held))


def stated(spec: object) -> Stated:
    """What one definition says about who reaches what.

    `getattr` rather than a type, because the two kinds that carry an audience
    are read by four callers that have no reason to agree on anything else --
    and a definition written in Python satisfies this by having the fields.
    """
    return Stated(
        source_ids=getattr(spec, "source_ids", ALL),
        entries={
            field: dict(entries) for field, entries in getattr(spec, "audiences", {}).items()
        },
        datazone=getattr(spec, "datazone", None),
    )


def offers(
    field: str,
    held: frozenset[str] | None,
    specs: Iterable[object],
    *,
    same: Callable[[str], str | None] = lambda name: name,
) -> Callable[[str], bool]:
    """Whether any definition this caller reaches offers them one entry of a catalogue.

    The grant's half only. A skill's own file has nowhere to write `source_ids:`, but a
    tool made by `kingfisher.tool` may say who it admits, and that is an **and** this
    cannot see: the build, the listing and the run report each take out the tools
    `core.definitions.tools.spec.out_of_reach` names as well.

    A definition leaving the field unset offers the whole catalogue at its own audience,
    which is why one of those ends the walk rather than contributing to it: `ALL` is the
    string `"*"`, and a union that read its entries would collect the character.

    `same` is how two spellings of one entry are compared, and skills need it -- a grant
    may write `audit` or `catalogue::audit`, and an audience written one way has to keep
    the entry the registry names the other way.

    **`specs` is the agents a caller reaches, not every definition.** A subagent is
    never a widener -- `ceiling` narrows its tools by what the agent holds and
    `subagent_skills` by what the run activated -- so a subagent declaring a name no
    reachable agent grants contributes one the caller gets by no path. Asked of
    subagents too, this offered `atool` to a caller whose only agent granted `btool`,
    and the build then handed its subagent nothing: measured. The promise is "an asset
    a caller's source ids do not reach", and unioning over declarations answers a
    different sentence -- "no definition they reach declares it".

    Here rather than in the listing that first needed it, because the withheld run
    report is the second reader and the two had different answers: the report measured
    what a grant left out against the whole catalogue, so a caller narrowing their own
    grant was handed the name of every tool and skill no definition they reach offers.
    Two answers to what a caller reaches is how those come apart, and this is the one.
    """
    if held is None:
        return lambda _name: True
    reached: set[str | None] = set()
    for spec in specs:
        offered = narrowed_for(spec, held)[field]
        if offered == ALL:
            return lambda _name: True
        reached |= {same(name) for name in offered or ()}
    reached -= {None}
    return lambda name: same(name) in reached


def activated(named: Selection, defined: Mapping[str, Any]) -> tuple[str, ...]:
    """The subagents an agent activates: the ones it names, with `"*"` expanded.

    Its subagents' own `subagents:` are deliberately not walked. A helper reaches a
    run only where the agent names it too -- `subagent_helpers` narrows one by what
    the agent declared -- so walking the chain here printed a subagent the build
    would drop, which is worse than printing nothing: a listing is read to find out
    what an agent ends up with.
    """
    if named is None:
        return ()
    return tuple(sorted(defined if named == ALL else set(named) & set(defined)))


def within_reach(
    names: Iterable[str], defined: Mapping[str, Any], held: frozenset[str] | None
) -> tuple[str, ...]:
    """`names`, less each subagent whose own `source_ids:` does not reach this caller.

    The build drops those, so whatever tells a caller about subagents drops them too:
    out of reach reads as not offered, and a report naming one says it exists.
    """
    if held is None:
        return tuple(names)
    return tuple(name for name in names if reaches(stated(defined[name]).audience, held))


def handed_work_by(
    # Loosely typed for the reason `offers` is: a listing asks this of records read
    # off a catalogue that may have failed to load, so what arrives is whatever the
    # inventory managed to read rather than a settled `AgentSpec`.
    agents: Iterable[Any],
    defined: Mapping[str, Any],
    held: frozenset[str] | None,
) -> tuple[str, ...]:
    """Every subagent one of these agents can hand work to, for this caller.

    Two conditions, and a subagent needs both: its own `source_ids:` has to admit the
    caller, and some agent the caller can open has to name it. `within_reach` above
    asks the first alone, which left a subagent nothing activates reachable by no path
    and listed anyway -- measured, a definition admitting `B` that no agent names was
    offered to `B`, and a request naming it wired nothing.

    One function for both readers, because a listing and a run report asking this
    separately is how they came to disagree before: see `offers` above, written for the
    same reason. Here in `core/access` rather than beside the build, because the listing
    asks it and `kingfisher list` may not load a provider SDK to find out --
    `test_a_light_export_stays_light` is what says so.
    """
    if held is None:
        return tuple(defined)
    reachable = {
        name
        for spec in agents
        for name in activated(spec.declares(held).subagents, defined)
    }
    return within_reach(sorted(reachable), defined, held)


def narrowed_for(spec: object, held: frozenset[str] | None) -> dict[str, Selection]:
    """Each field in `AUDIENCED` on one definition, as one caller reaches it.

    `held` of `None` is no caller to narrow for -- no vocabulary, nobody named,
    or `UNSCOPED` -- and every selection comes back as the definition wrote it.

    Driven by `AUDIENCED` rather than by three lines each, because the two
    definitions that hold audiences wrote those lines out twice over and the
    list of fields is already declared once, here. What this deliberately does
    *not* return is a `Capabilities`: an agent widens `models` where a subagent
    leaves it unset, so the axes no audience touches stay with the definition
    that has an opinion about them.
    """
    said = stated(spec)
    return {
        field: getattr(spec, field)
        if held is None
        else reaching(
            getattr(spec, field),
            audiences=said.of(field),
            default=said.source_ids,
            held=held,
        )
        for field in AUDIENCED
    }

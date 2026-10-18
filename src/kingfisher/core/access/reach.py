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

    `getattr` rather than a type: the kinds carrying an audience share nothing else, and a
    definition written in Python satisfies this by having the fields.
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

    The grant's half only: a tool's own audience is an and this cannot see, which callers
    take out with `core.definitions.tools.spec.out_of_reach` as well. A definition writing
    `["*"]` ends the walk, offering everything: `ALL` is the string `"*"`, and a union would
    collect the character. `same` compares two spellings of one entry.

    `specs` is the agents a caller reaches, not every definition: a subagent never widens,
    so a name only a subagent declares is one the caller gets by no path. The listing and
    the withheld run report both ask this, so their answers cannot come apart.
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

    Its subagents' own `subagents:` are not walked: a helper reaches a run only where the
    agent names it too, and listing one the build would drop is worse than listing nothing.
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
    # Loosely typed: a listing asks this of whatever a catalogue that may have failed to
    # load managed to read, not a settled `AgentSpec`.
    agents: Iterable[Any],
    defined: Mapping[str, Any],
    held: frozenset[str] | None,
) -> tuple[str, ...]:
    """Every subagent one of these agents can hand work to, for this caller.

    Both conditions: its own `source_ids:` admits the caller, and an agent the caller can
    open names it. One function for the listing and the run report, so they cannot
    disagree; here rather than beside the build, as `kingfisher list` may not load a
    provider SDK.
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

    `held` of `None` is no caller to narrow for, and every selection comes back as written.
    Not a `Capabilities`: an agent widens `models` where a subagent leaves it unset, so the
    axes no audience touches stay with the definition.
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

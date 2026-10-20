"""Who reaches what, asked the same way by both askers: what the definitions say, and
what a caller holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import ALL
from .reach import stated
from .source_ids import (
    AccessError,
    AccessReport,
    CallerError,
    Datazone,
    _Unscoped,
    zone_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from .source_ids import SourceIds, Stated

#: One kind of definition and what this deployment has of it: `("agent", {name: spec})`.
Kind = tuple[str, "Mapping[str, object]"]


def held_by(
    vocabulary: SourceIds | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds -- or `None` where nothing narrows: no vocabulary, nobody named,
    or `UNSCOPED`.

    The one place the shape of `source_ids` is read: any sequence of names means what it
    looks like, and a bare string is refused rather than read a letter at a time.
    """
    if source_ids is None or isinstance(source_ids, _Unscoped):
        return None
    if isinstance(source_ids, str):
        msg = f"source ids is a sequence of names, not a string -- write [{source_ids!r}]"
        raise CallerError(msg)
    named = tuple(one for one in source_ids if not isinstance(one, Datazone))
    zones = tuple(one for one in source_ids if isinstance(one, Datazone))
    if zones:
        return _zoned(vocabulary, named, zones)
    if vocabulary is None:
        # A name where nothing narrows would go through unrestricted. Here rather than at
        # each door, so the read-only listing, which bypasses `caller_holds`, is covered;
        # an empty sequence names no caller to be wrong about.
        if named:
            msg = (
                f"this deployment declares no source ids, so {', '.join(sorted(named))} "
                f"narrows nothing and the answer would be the unrestricted one. Declare "
                f"them in source_ids.yaml, or pass UNSCOPED to mean no caller"
            )
            raise CallerError(msg)
        return None
    return vocabulary.known(named)


def _zoned(
    vocabulary: SourceIds | None, named: tuple[str, ...], zones: tuple[Datazone, ...]
) -> frozenset[str]:
    """What a caller in a datazone holds: their source ids, and the zone."""
    if vocabulary is None:
        msg = (
            f"this deployment declares no datazones, so {zones[0]!r} narrows nothing. "
            f"Declare it under 'datazones:' in source_ids.yaml, or pass no datazone"
        )
        raise CallerError(msg)
    if len(zones) > 1:
        msg = f"a caller is in one datazone, and this one named {', '.join(map(repr, zones))}"
        raise CallerError(msg)
    (zone,) = zones
    if zone not in vocabulary.datazones:
        msg = (
            f"unknown datazone {str(zone)!r}; this deployment defines "
            f"{', '.join(sorted(vocabulary.datazones)) or 'none'}"
        )
        raise CallerError(msg)
    return vocabulary.known(named) | {zone_name(zone)}


def caller_holds(
    vocabulary: SourceIds | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds, refusing a call that named nobody where a policy is in force.

    `held_by` answers `None` for no vocabulary, nobody named, or `UNSCOPED`, and a reach
    check reads `None` as reaching everything: wrong for nobody named under a policy. Here
    rather than at each door, so no door can forget it.
    """
    if vocabulary is not None and source_ids is None:
        msg = (
            "this deployment has an access policy, so a call must say who is "
            "calling: pass source_ids=[...] with the caller's source ids, or "
            "source_ids=UNSCOPED to run without one -- from the command line, "
            "--as A,B or --as UNSCOPED"
        )
        raise CallerError(msg)
    return held_by(vocabulary, source_ids)


def walked(*kinds: Kind) -> Iterator[tuple[str, str, Stated]]:
    """Every definition of every kind, with what it says, in a stable order.

    Shared by `audit`, `undeclared_in` and the listing rather than merged: a report that
    stopped at the first fault would be a refusal.
    """
    for kind, specs in kinds:
        for name, spec in sorted(specs.items()):
            yield kind, name, stated(spec)


def undeclared_in(specs: Mapping[str, object], *, kind: str, vocabulary: SourceIds) -> str | None:
    """The first definition naming a source id this deployment does not declare."""
    for _kind, name, said in walked((kind, specs)):
        if said.datazone is not None and said.datazone not in vocabulary.datazones:
            return (
                f"{kind} {name!r}: names undeclared datazone {said.datazone!r}; this "
                f"deployment defines {', '.join(sorted(vocabulary.datazones)) or 'none'}"
            )
        for where, audience in (
            (f"{kind} {name!r}", said.source_ids),
            *(
                (f"{kind} {name!r}: skills entry {entry!r}", who)
                for entry, who in said.skills.items()
            ),
        ):
            if (complaint := vocabulary.undeclared(audience, where=where)) is not None:
                return complaint
    return None


def refuse_undeclared(*kinds: Kind, vocabulary: SourceIds) -> None:
    """Stop a deployment whose definitions name source ids it never declared."""
    for kind, specs in kinds:
        if (complaint := undeclared_in(specs, kind=kind, vocabulary=vocabulary)) is not None:
            raise AccessError(complaint)


def audit(*kinds: Kind, vocabulary: SourceIds) -> AccessReport:
    """What this deployment's policy leaves open, in one pass over the files."""
    unrestricted: list[tuple[str, str]] = []
    narrowed: list[tuple[str, str]] = []
    for kind, name, said in walked(*kinds):
        if said.source_ids == ALL and said.datazone is None:
            unrestricted.append((kind, name))
        narrowed.extend(
            vocabulary.narrowing_in(said.skills, source_ids=said.source_ids, where=f"{kind} {name}")
        )
    return AccessReport(unrestricted=tuple(unrestricted), narrowed=tuple(narrowed))

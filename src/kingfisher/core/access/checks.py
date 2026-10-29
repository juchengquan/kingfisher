"""Who reaches what, asked the same way by both askers: what the definitions say, and
what a caller holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .capabilities import ALL
from .reach import stated
from .source_ids import (
    DATAZONE_PREFIX,
    AccessReport,
    CallerError,
    Datazone,
    ExtraDatazone,
    InZone,
    Vocabulary,
    _Unscoped,
    mentioned,
    zone_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from .source_ids import Stated

#: One kind of definition and what this deployment has of it: `("agent", {name: spec})`.
Kind = tuple[str, "Mapping[str, object]"]


def held_by(
    vocabulary: Vocabulary | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds -- or `None` where nothing narrows: no lock anywhere, nobody
    named, or `UNSCOPED`.

    The one place the shape of `source_ids` is read: any sequence of names means what it
    looks like, and a bare string is refused rather than read a letter at a time. A name
    no definition uses is held all the same and opens nothing: refusing it would make a
    call pass or fail on which locks exist that week.
    """
    if source_ids is None or isinstance(source_ids, _Unscoped):
        return None
    if isinstance(source_ids, str):
        msg = f"source ids is a sequence of names, not a string -- write [{source_ids!r}]"
        raise CallerError(msg)
    try:
        given: tuple[object, ...] = tuple(source_ids)
    except TypeError:
        given = (source_ids,)
    names = tuple(one for one in given if isinstance(one, str))
    if len(names) != len(given):
        stray = next(one for one in given if not isinstance(one, str))
        msg = f"source ids are names, and {stray!r} is not one -- write each as a string"
        raise CallerError(msg)
    named = tuple(one for one in names if not isinstance(one, Datazone))
    zones = tuple(one for one in names if isinstance(one, Datazone))
    # Held among the names, a prefixed one would be a datazone nobody passed as one, and
    # "a caller is in one datazone" would be left to whoever built the list.
    if prefixed := sorted({one for one in named if one.startswith(DATAZONE_PREFIX)}):
        msg = (
            f"{', '.join(map(repr, prefixed))} begins {DATAZONE_PREFIX!r}, which is how a "
            f"datazone is held, not a source id -- pass the datazone as datazone= instead"
        )
        raise CallerError(msg)
    if vocabulary is None:
        # A name where nothing narrows would go through unrestricted. Here rather than at
        # each door, so the read-only listing, which bypasses `caller_holds`, is covered;
        # an empty sequence names no caller to be wrong about.
        if named or zones:
            said = ", ".join(sorted(named) + [repr(str(zone)) for zone in zones])
            msg = (
                f"no definition here carries a lock, so {said} narrows nothing and the "
                f"answer would be the unrestricted one. Pass UNSCOPED to mean no caller"
            )
            raise CallerError(msg)
        return None
    homes = tuple(zone for zone in zones if not isinstance(zone, ExtraDatazone))
    if len(homes) > 1:
        msg = f"a caller is in one datazone, and this one named {', '.join(map(repr, homes))}"
        raise CallerError(msg)
    # Less the one they are in: repeated among the extras, its plain name would take the
    # place of the typed one in the set, and a tool be told the caller is in none.
    extras = {zone_name(one) for one in zones if isinstance(one, ExtraDatazone)}
    extras -= set(map(zone_name, homes))
    return frozenset(named) | extras | {InZone(zone_name(zone)) for zone in homes}


def caller_holds(
    vocabulary: Vocabulary | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds, refusing a call that named nobody where a lock is in force.

    `held_by` answers `None` for no lock, nobody named, or `UNSCOPED`, and a reach check
    reads `None` as reaching everything: wrong for nobody named where something is locked.
    Here rather than at each door, so no door can forget it.
    """
    if vocabulary is not None and source_ids is None:
        msg = (
            "a definition here carries a lock, so a call must say who is calling: pass "
            "source_ids=[...] with the caller's source ids, or source_ids=UNSCOPED to run "
            "without one -- from the command line, --as A,B or --as UNSCOPED"
        )
        raise CallerError(msg)
    return held_by(vocabulary, source_ids)


def walked(*kinds: Kind) -> Iterator[tuple[str, str, Stated]]:
    """Every definition of every kind, with what it says, in a stable order.

    Shared by `audit`, `collected` and the listing rather than merged: a report that
    stopped at the first fault would be a refusal.
    """
    for kind, specs in kinds:
        for name, spec in sorted(specs.items()):
            yield kind, name, stated(spec)


def collected(*kinds: Kind) -> Vocabulary | None:
    """Every source id and datazone these definitions name, with which name each -- or
    `None` where none carries a lock, so nothing narrows and naming a caller is refused.
    """
    source_ids: dict[str, dict[str, None]] = {}
    datazones: dict[str, dict[str, None]] = {}
    for kind, name, said in walked(*kinds):
        who = f"{kind} {name}"
        for one in mentioned(said.source_ids):
            source_ids.setdefault(one, {})[who] = None
        if said.datazone is not None:
            datazones.setdefault(said.datazone, {})[who] = None
    if not source_ids and not datazones:
        return None
    return Vocabulary(
        source_ids={one: tuple(who) for one, who in sorted(source_ids.items())},
        datazones={one: tuple(who) for one, who in sorted(datazones.items())},
    )


def audit(*kinds: Kind) -> AccessReport:
    """What this deployment's locks leave open, in one pass over the files."""
    return AccessReport(
        unrestricted=tuple(
            (kind, name)
            for kind, name, said in walked(*kinds)
            if said.source_ids == ALL and said.datazone is None
        )
    )

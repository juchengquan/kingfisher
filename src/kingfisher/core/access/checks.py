"""Who reaches what, asked the same way by both askers: what the definitions say, and
what a caller holds.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.core.access.capabilities import ALL
from kingfisher.core.access.source_ids import (
    AccessError,
    AccessReport,
    Datazone,
    _Unscoped,
    stated,
    zone_name,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping

    from kingfisher.core.access.source_ids import SourceIds, Stated

#: One kind of definition and what this deployment has of it, as the callers
#: hold it: `("agent", {name: spec})`. A pair rather than two arguments because
#: every function here walks both and neither ever wants one alone.
Kind = tuple[str, "Mapping[str, object]"]


def held_by(
    vocabulary: SourceIds | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds, expanded -- or `None` where nothing narrows: no vocabulary,
    nobody named, or `UNSCOPED`.

    The one place the shape of `source_ids` is read. Any sequence of names means what
    it looks like, because a list is the obvious thing to write; a bare string is a
    sequence too, and is refused rather than read a letter at a time.
    """
    if source_ids is None or isinstance(source_ids, _Unscoped):
        return None
    if isinstance(source_ids, str):
        msg = f"source ids is a sequence of names, not a string -- write [{source_ids!r}]"
        raise AccessError(msg)
    named = tuple(one for one in source_ids if not isinstance(one, Datazone))
    zones = tuple(one for one in source_ids if isinstance(one, Datazone))
    if zones:
        return _zoned(vocabulary, named, zones)
    if vocabulary is None:
        # A name where nothing narrows expanded to nobody and went through
        # unrestricted, so `list --as marketing` printed the whole catalogue and
        # exited zero -- the state the HTTP surface refuses at startup as "a server
        # somebody believes is locked down and is not".
        #
        # Here rather than at each door, so the listing is covered: it bypasses
        # `caller_holds` deliberately, being read-only, and the refusal it is exempt
        # from is the one for naming *nobody*. An empty sequence passes, naming no
        # caller to be wrong about, and `UNSCOPED` is how the absence is meant.
        if named:
            msg = (
                f"this deployment declares no source ids, so {', '.join(sorted(named))} "
                f"narrows nothing and the answer would be the unrestricted one. Declare "
                f"them in source_ids.yaml, or pass UNSCOPED to mean no caller"
            )
            raise AccessError(msg)
        return None
    return vocabulary.expand(named)


def _zoned(
    vocabulary: SourceIds | None, named: tuple[str, ...], zones: tuple[Datazone, ...]
) -> frozenset[str]:
    """What a caller in a datazone holds: their source ids, expanded, and the zone."""
    if vocabulary is None:
        msg = (
            f"this deployment declares no datazones, so {zones[0]!r} narrows nothing. "
            f"Declare it under 'datazones:' in source_ids.yaml, or pass no datazone"
        )
        raise AccessError(msg)
    if len(zones) > 1:
        msg = f"a caller is in one datazone, and this one named {', '.join(map(repr, zones))}"
        raise AccessError(msg)
    (zone,) = zones
    if zone not in vocabulary.datazones:
        msg = (
            f"unknown datazone {str(zone)!r}; this deployment defines "
            f"{', '.join(sorted(vocabulary.datazones)) or 'none'}"
        )
        raise AccessError(msg)
    return vocabulary.expand(named) | {zone_name(zone)}


def caller_holds(
    vocabulary: SourceIds | None, source_ids: Iterable[str] | _Unscoped | None
) -> frozenset[str] | None:
    """What a caller holds, refusing a call that named nobody where a policy is in force.

    `held_by` answers `None` for three different things -- no vocabulary, nobody named,
    or `UNSCOPED` -- and every reach check reads `None` as reaching everything. That is
    right for the first and the third, and wrong for the second: a deployment with a
    policy was asked something on a caller's behalf and nobody said whose behalf.

    Here rather than at each door, because the sentence below was written out at two of
    them and forgotten at the third, where reading one session answered for any of them.
    A caller who means no caller says so with `UNSCOPED`, which is what that value is
    for; housekeeping that is nobody's -- listing every session, deleting one -- does not
    come through here at all.
    """
    if vocabulary is not None and source_ids is None:
        msg = (
            "this deployment has an access policy, so a call must say who is "
            "calling: pass source_ids=[...] with the caller's source ids, or "
            "source_ids=UNSCOPED to run without one -- from the command line, "
            "--as A,B or --as UNSCOPED"
        )
        raise AccessError(msg)
    return held_by(vocabulary, source_ids)


def walked(*kinds: Kind) -> Iterator[tuple[str, str, Stated]]:
    """Every definition of every kind, with what it says, in a stable order.

    One walk for three questions. `audit` wants a report, `undeclared_in` wants the
    first refusal, and a listing wants what to print -- all three began by asking
    `stated` for the same definition, and the specs are a directory read. Shared
    here rather than merged: a report that stopped at the first fault would be a
    refusal, and a refusal that carried on would be a report.
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
                (f"{kind} {name!r}: {field} entry {entry!r}", who)
                for field, entries in said.entries.items()
                for entry, who in entries.items()
            ),
        ):
            try:
                vocabulary.refuse_undeclared(audience, where=where, error=AccessError)
            except AccessError as exc:
                return str(exc)
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
            vocabulary.narrowing_in(
                said.entries, source_ids=said.source_ids, where=f"{kind} {name}"
            )
        )
    return AccessReport(unrestricted=tuple(unrestricted), narrowed=tuple(narrowed))

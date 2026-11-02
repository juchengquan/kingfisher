"""Source ids: what a caller may hold, and what a definition may say about who reaches it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from kingfisher.core.result import Listed

from .capabilities import ALL

#: The selection fields a definition's own `source_ids:` narrows for a caller, since what it
#: holds is reached only through it. A tool's or skill's own lock is applied apart, from its
#: own file. Not `builtin_tools`: deepagents registers those, so
#: kingfisher can filter them but never leave them out, and an audience there would promise a
#: boundary nothing can hold.
AUDIENCED: Final[tuple[str, ...]] = ("tools", "subagents", "skills")

#: One audience entry satisfied only by holding every name in it. A `frozenset`: it reads
#: as "all of these", stays hashable so an `Audience` compares, and has no order.
Requires = frozenset[str]

#: Who may reach one thing: `"*"` for everyone, or exactly these entries. The tuple is an
#: or and a `Requires` entry an and, which gives or-of-ands out of one field.
Audience = Literal["*"] | tuple[str | Requires, ...]


#: What a datazone becomes among held names and requirements: one more name, which every
#: reach check already asks about. Refused as a source id, so no definition writes one and
#: no caller presents one.
DATAZONE_PREFIX = "datazone="


def zone_name(datazone: str) -> str:
    """A datazone as a name to hold."""
    return f"{DATAZONE_PREFIX}{datazone}"


class InZone(str):
    """The held name of the datazone a caller is in, as against an extra one.

    Equal to the plain name, so every reach check reads the two alike, and typed, so the
    zone a caller is in survives `held_by` making all of theirs one set.
    """

    __slots__ = ()


def zone_in(held: Iterable[str]) -> str | None:
    """The datazone a caller is in, not an extra one, or `None` for one in no datazone."""
    zones = (str(one).removeprefix(DATAZONE_PREFIX) for one in held if isinstance(one, InZone))
    return next(zones, None)


def zones_held(held: Iterable[str]) -> frozenset[str]:
    """Every datazone a caller holds: the one they are in, and their extra ones."""
    return frozenset(
        one.removeprefix(DATAZONE_PREFIX) for one in held if one.startswith(DATAZONE_PREFIX)
    )


@dataclass(frozen=True)
class Stated:
    """What one definition says about who reaches what."""

    #: The definition's own audience: who may reach it at all.
    source_ids: Audience = ALL
    #: The one datazone a caller must be in to reach it, or `None` for any.
    datazone: str | None = None

    @property
    def says_nothing(self) -> bool:
        """Whether this definition restricts anyone at all."""
        return self.source_ids == ALL and self.datazone is None

    @property
    def audience(self) -> Audience:
        """Who reaches the definition: its source ids, and its datazone as well."""
        return zoned(self.source_ids, self.datazone)


class CallerError(ValueError):
    """A call said who is calling in a way this deployment cannot take: a bare string, two
    datazones, a source id spelt as a datazone, a caller named where nothing is locked, or
    nobody named where something is."""


class _Unscoped:
    """The type of `UNSCOPED`, so that it is not confusable with a source id list."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSCOPED"


#: Running with no caller identity at all, said out loud. A sentinel, not `None`: `None` is
#: what an argument nobody passed looks like, and this can be grepped for in a review.
UNSCOPED: Final[_Unscoped] = _Unscoped()

#: What a call may say about who is making it: the source ids held, or the explicit refusal
#: to say.
Held = Sequence[str] | _Unscoped


def zoned(audience: Audience, datazone: str | None) -> Audience:
    """`audience`, and in `datazone` as well where there is one."""
    if datazone is None:
        return audience
    return jointly((audience, (zone_name(datazone),)))


class Datazone(str):
    """A caller's datazone, carried among their source ids as far as `held_by`.

    A `str` so it travels in a `Held` unchanged through every door that passes it on unread;
    a second parameter would be one more each of them had to remember.
    """

    __slots__ = ()


class ExtraDatazone(Datazone):
    """One of a caller's extra datazones, carried as their datazone is.

    A `Datazone`, so whatever leaves a caller's zones out of their source ids leaves this
    out too: read as a source id, `common` would open whatever a lock names `common`.
    """

    __slots__ = ()


def _one_zone(written: object, *, passed: str) -> str:
    """One datazone a caller names, as it is held; `passed` is how a call spells one."""
    # Read as a definition's is: held as given, `" finance"` reached nothing zoned finance
    # and pinned the session it ran in to `' finance'` for good.
    zone = written.strip() if isinstance(written, str) else ""
    if not zone:
        msg = f"a caller's datazone is one name -- pass {passed} -- got {written!r}"
        raise CallerError(msg)
    # Not "any", as `"*"` is in an audience: a caller is in named zones or none, and held
    # as a name it reached no zoned definition and pinned the session to a zone of `*`.
    if zone == ALL:
        msg = (
            f"a caller's datazone is one name, and '*' is not 'any' -- pass {passed}, or "
            f"leave it out"
        )
        raise CallerError(msg)
    return zone


def with_datazone[Caller: Iterable[str] | _Unscoped | None](
    source_ids: Caller, datazone: str | None, extra_datazones: Sequence[str] = ()
) -> Caller | tuple[str, ...]:
    """The caller's source ids, carrying their datazone and extra datazones where they
    named them."""
    if isinstance(extra_datazones, str):
        msg = (
            f"extra datazones are a sequence of names, not a string -- write "
            f"[{extra_datazones!r}]"
        )
        raise CallerError(msg)
    # Closed, as a caller in no datazone is: a deployment that forgot the zone and sent
    # its hardcoded extras would otherwise hand the caller everything locked to them.
    if datazone is None and extra_datazones:
        msg = (
            "extra datazones are a caller's beside the one they are in, and this call names "
            "none -- pass datazone= with them, from the command line --datazone"
        )
        raise CallerError(msg)
    if datazone is None or isinstance(source_ids, str):
        # A bare string is refused by `held_by`, naming the string; leave it to say so.
        return source_ids
    zone = _one_zone(
        datazone, passed="datazone='finance', from the command line --datazone finance"
    )
    extras = tuple(
        _one_zone(
            one,
            passed="extra_datazones=['common'], from the command line --extra-datazones common",
        )
        for one in extra_datazones
    )
    if isinstance(source_ids, _Unscoped):
        msg = (
            "UNSCOPED runs with no caller, so it has no datazone -- pass the caller's "
            "source ids with their datazone, or UNSCOPED alone"
        )
        raise CallerError(msg)
    # Carried on, the zone would be a caller, and `caller_holds` never see nobody named.
    if source_ids is None:
        msg = (
            f"datazone {zone!r} is a caller's, and this call names no caller -- pass "
            "the caller's source ids with it, from the command line --as A,B"
        )
        raise CallerError(msg)
    return (*source_ids, Datazone(zone), *map(ExtraDatazone, extras))


def jointly(audiences: Iterable[Audience]) -> Audience:
    """Who reaches every one of these, as one audience.

    Each is an or of ands, and so is the answer: every way of picking one entry from
    each, held together, keeping only the ways no other needs less than.
    """
    ways: set[frozenset[str]] = {frozenset()}
    for audience in audiences:
        if audience == ALL:
            continue
        picked = {
            way | (one if isinstance(one, frozenset) else frozenset((one,)))
            for way in ways
            for one in audience
        }
        ways = {way for way in picked if not any(other < way for other in picked)}
    if ways == {frozenset()}:
        return ALL
    return tuple(
        next(iter(way)) if len(way) == 1 else way
        for way in sorted(ways, key=lambda way: (len(way), sorted(way)))
    )


def listed(audience: Audience) -> Listed:
    """An audience as a ledger row holds it, which JSON can: a set becomes a sorted
    tuple, and everyone is `("*",)` so the field is a list whoever it names.
    """
    if audience == ALL:
        return (ALL,)
    return tuple(tuple(sorted(one)) if isinstance(one, frozenset) else one for one in audience)


def unlisted(written: Sequence[str | Sequence[str]]) -> Audience:
    """What `listed` wrote, read back from a row: a list where it wrote a tuple."""
    if list(written) == [ALL]:
        return ALL
    return tuple(one if isinstance(one, str) else frozenset(one) for one in written)


def spell(audience: Audience) -> str:
    """One audience, written the way the formats and the listing write it."""
    if audience == ALL:
        return ALL
    return ", ".join(
        "{" + ", ".join(sorted(one)) + "}" if isinstance(one, frozenset) else one
        for one in audience
    )


@dataclass(frozen=True)
class AccessReport:
    """What this deployment's locks leave open, as `kingfisher list` prints it."""

    #: Definitions carrying no `source_ids:` line, and so reachable by everyone, as `(kind,
    #: name)`.
    unrestricted: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.unrestricted

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing."""
        if self.is_clean:
            return ()
        said: list[str] = ["access:"]
        if self.unrestricted:
            said.append("  no source_ids: line, so reachable by everyone:")
            said.extend(f"    {kind} {name}" for kind, name in self.unrestricted)
        return tuple(said)


@dataclass(frozen=True)
class Opening:
    """Source ids and datazones, as one side of what a caller's names open."""

    source_ids: frozenset[str] = frozenset()
    datazones: frozenset[str] = frozenset()

    @classmethod
    def of(cls, names: Iterable[str]) -> Opening:
        """These held names, the datazones told from the source ids by their prefix.

        Split rather than handed back as one set: a zone folded in comes back spelt
        `datazone=finance`, which `held_by` refuses as a source id, so the answer would
        name its own fix in the one spelling the call rejects. Folded, "one more id
        would open this" and "no id can open this" also read alike, while the two take
        different levers.
        """
        held = set(names)
        return cls(
            source_ids=frozenset(one for one in held if not one.startswith(DATAZONE_PREFIX)),
            datazones=frozenset(
                one.removeprefix(DATAZONE_PREFIX)
                for one in held
                if one.startswith(DATAZONE_PREFIX)
            ),
        )


@dataclass(frozen=True)
class Opened:
    """Which of one caller's names open something under one agent, and which would.

    The operator's answer *about* a caller, and not that caller's own view: `would`
    names a lock they do not pass and the name that would pass it, which a scoped
    listing withholds as policy.
    """

    #: Of the names they hold, those sitting in an audience entry they satisfy.
    opens: Opening = Opening()
    #: Names they do not hold, each of which alone would satisfy a shut audience.
    would: Opening = Opening()


@dataclass(frozen=True)
class Vocabulary:
    """The source ids and datazones this deployment's definitions use, and which use each.

    Collected from the definitions, never written down: a name is real because a lock
    names it, so there is no second list to fall out of step with the locks.
    """

    #: Each source id a lock names, and the definitions naming it, as `kind name`.
    source_ids: Mapping[str, tuple[str, ...]]
    #: Each datazone a definition requires, and the definitions requiring it.
    datazones: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


def mentioned(audience: Audience) -> frozenset[str]:
    """Every source id an audience names, inside a set or not."""
    if audience == ALL:
        return frozenset()
    return frozenset(n for one in audience for n in ((one,) if isinstance(one, str) else one))

"""The source-id vocabulary: what a caller may hold, and what a definition may say about
who reaches it."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from kingfisher.core.result import Listed

from .capabilities import ALL

#: The selection fields a definition's own `source_ids:` narrows for a caller, since what it
#: holds is reached only through it. Not `builtin_tools`: deepagents registers those, so
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
#: reach check already asks about. Refused as a source id, so nobody declares or presents one.
DATAZONE_PREFIX = "datazone="


def zone_name(datazone: str) -> str:
    """A datazone as a name to hold."""
    return f"{DATAZONE_PREFIX}{datazone}"


def zone_held(held: Iterable[str]) -> str | None:
    """The datazone a caller holds, or `None` for one in no datazone."""
    zones = (one.removeprefix(DATAZONE_PREFIX) for one in held if one.startswith(DATAZONE_PREFIX))
    return next(zones, None)


@dataclass(frozen=True)
class Stated:
    """What one definition says about who reaches what."""

    #: The definition's own audience: who may reach it at all.
    source_ids: Audience = ALL
    #: Skill name -> who this definition tells about it, for the entries saying.
    skills: Mapping[str, Audience] = field(default_factory=dict)
    #: The one datazone a caller must be in to reach it, or `None` for any.
    datazone: str | None = None

    @property
    def says_nothing(self) -> bool:
        """Whether this definition restricts anyone at all."""
        return self.source_ids == ALL and not self.skills and self.datazone is None

    @property
    def audience(self) -> Audience:
        """Who reaches the definition: its source ids, and its datazone as well."""
        return zoned(self.source_ids, self.datazone)


class AccessError(ValueError):
    """The vocabulary is malformed, or a definition names a source id it does not declare."""


class CallerError(AccessError):
    """A call said who is calling in a way this deployment cannot take: a source id or
    datazone it does not define, or nobody where a policy is in force.

    An `AccessError` too, so a handler written against that still catches it.
    """


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


def with_datazone[Caller: Iterable[str] | _Unscoped | None](
    source_ids: Caller, datazone: str | None
) -> Caller | tuple[str, ...]:
    """The caller's source ids, carrying their datazone where they named one."""
    if datazone is None or isinstance(source_ids, str):
        # A bare string is refused by `held_by`, naming the string; leave it to say so.
        return source_ids
    if isinstance(source_ids, _Unscoped):
        msg = (
            "UNSCOPED runs with no caller, so it has no datazone -- pass the caller's "
            "source ids with their datazone, or UNSCOPED alone"
        )
        raise CallerError(msg)
    return (*(source_ids or ()), Datazone(datazone))


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
    """What a deployment's policy leaves open, as `kingfisher list` prints it."""

    #: Definitions carrying no `source_ids:` line, and so reachable by everyone, as `(kind,
    #: name)`.
    unrestricted: tuple[tuple[str, str], ...] = ()

    #: Skills entries naming none of the source ids their definition's own audience
    #: mentions, as `(where, audience)`. Reached only by a caller holding one of each.
    narrowed: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (self.unrestricted or self.narrowed)

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing."""
        if self.is_clean:
            return ()
        said: list[str] = ["access:"]
        if self.unrestricted:
            said.append("  no source_ids: line, so reachable by everyone:")
            said.extend(f"    {kind} {name}" for kind, name in self.unrestricted)
        if self.narrowed:
            said.append("  narrows past this definition's own audience,")
            said.append("  so it reaches only callers holding both:")
            said.extend(f"    {where}  [{who}]" for where, who in self.narrowed)
        return tuple(said)


@dataclass(frozen=True)
class SourceIds:
    """One deployment's source-id vocabulary: the names a caller may hold."""

    #: The declared source ids. Each stands only for itself.
    names: frozenset[str]
    #: The datazones a caller may be in and a definition may require.
    datazones: frozenset[str] = frozenset()

    def mentions(self, audience: Audience) -> frozenset[str]:
        """Every source id an audience names, inside a set or not."""
        if audience == ALL:
            return frozenset()
        return frozenset(n for one in audience for n in ((one,) if isinstance(one, str) else one))

    def known(self, held: Iterable[str]) -> frozenset[str]:
        """The source ids a caller sent, refused unless every one is declared."""
        wanted = frozenset(held)
        if unknown := sorted(wanted - self.names):
            msg = (
                f"unknown source id(s): {', '.join(unknown)}; "
                f"this deployment defines {', '.join(sorted(self.names)) or 'none'}"
            )
            raise CallerError(msg)
        return wanted

    def undeclared(self, audience: Audience, *, where: str) -> str | None:
        """The complaint about an audience naming a source id this deployment does not declare."""
        if audience == ALL:
            return None
        named = tuple(
            n for one in audience for n in ((one,) if isinstance(one, str) else sorted(one))
        )
        if unknown := tuple(name for name in named if name not in self.names):
            listed = ", ".join(repr(u) for u in sorted(set(unknown)))
            return (
                f"{where}: names undeclared source id(s) {listed}; "
                f"this deployment defines {', '.join(sorted(self.names)) or 'none'}"
            )
        return None

    def narrowing_in(
        self,
        skills: Mapping[str, Audience],
        *,
        source_ids: Audience,
        where: str,
    ) -> tuple[tuple[str, str], ...]:
        """Skills entries naming none of the source ids this definition's own audience mentions.

        An entry's audience is already an and with the definition's, since an entry is reached
        only through its definition: `[pii]` under `[sales_db, audit_log]` means "whoever
        opens this agent and holds `pii`", a good second requirement.
        """
        if source_ids == ALL:
            return ()
        admitted = self.mentions(source_ids)
        return tuple(
            (f"{where}: skill {entry}", spell(audience))
            for entry, audience in skills.items()
            if audience != ALL and not (self.mentions(audience) & admitted)
        )

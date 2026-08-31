"""Who reaches what: the group vocabulary, and the rule the definitions apply.

Two halves, and the split is the design. **Audiences live in the definitions** --
an agent or a subagent says who may reach it, and may say who reaches each tool,
delegate or skill it holds. What is central is only the *vocabulary*: which
group names exist, and which contain which. That file holds no policy at all,
and everything it used to hold now sits beside the thing it was about.

What that buys, beyond locality: there is nothing left to reconcile. A central
table could name an asset the workspace no longer offers, which had to be
detected and dropped or every turn would refuse. A definition *is* the asset, so
that failure has no shape here -- and a definition naming a tool that does not
exist was already refused by `Offering.refuse_unknown`, long before any of this.

The answer the rule produces is an ordinary `Capabilities`, which is unchanged
from the central design and is what keeps everything downstream unchanged too:
an ungranted tool is never attached to the graph, and an ungranted subagent is
never compiled.

Three fields may carry audiences and one deliberately may not. `builtin_tools`
is absent because deepagents registers those itself, so kingfisher can only
filter them afterwards, never leave them out of a graph -- see
`kingfisher.infrastructure.harness.narrowing`, which records a live run where a
model called `execute` from memory. Control them through which *agents* a group
may open instead: an agent declaring a read-only builtin set cannot yield the
shell to anyone.

Pure, like the rest of `domain/`: this module reads no file. The YAML half is
`kingfisher.infrastructure.access_policy`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Final, Literal

from kingfisher.domain import fields
from kingfisher.domain.capabilities import ALL, Selection

#: The selection fields that may be written as a mapping of name to audience.
#: `builtin_tools` is absent on purpose -- see the module docstring.
AUDIENCED: Final[tuple[str, ...]] = ("tools", "subagents", "skills")

#: Sections the central format defined, and where each has gone. Refused rather
#: than ignored, because a deployment upgrading has a file full of policy that
#: would otherwise be read and dropped in silence -- which is the single failure
#: this whole area exists to prevent.
#: Where audiences went, said once and shared by the three keys that used to
#: hold them.
_WENT = (
    "audiences live in the definition now: write `groups:` in the file itself, "
    "and a mapping under `tools:`, `subagents:` or `skills:` to narrow one "
    "entry further"
)

MOVED: Final[Mapping[str, str]] = dict.fromkeys(("agents", "subagents", "tools"), _WENT)

#: Who may reach one thing: `"*"` for everyone, or exactly these groups.
#:
#: No `None`. "Nobody" is not a state a definition can be in -- the absence of
#: an audience means it inherits the one around it, and a definition nobody may
#: reach is written by giving it a group nobody holds.
Audience = Literal["*"] | tuple[str, ...]

@dataclass(frozen=True)
class Stated:
    """What one definition says about who reaches what.

    A record rather than a mapping with two value shapes. It was the second for
    one commit, keyed `groups` beside one entry table per field, and every
    reader of it then had to narrow a union at the point of use -- which is a
    cost paid by everybody to save one type here.
    """

    #: The definition's own audience: who may reach it at all.
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry there.
    entries: Mapping[str, Mapping[str, Audience]] = field(default_factory=dict)

    @property
    def says_nothing(self) -> bool:
        """Whether this definition restricts anyone at all."""
        return self.groups == ALL and not self.entries

    def of(self, field_name: str) -> Mapping[str, Audience]:
        """One field's per-entry audiences, or nothing."""
        return self.entries.get(field_name, {})


class AccessError(ValueError):
    """The vocabulary is malformed, or a caller named a group it does not define."""


class _Unscoped:
    """The type of `UNSCOPED`, so that it is not confusable with a group list."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "UNSCOPED"


#: Running with no caller identity at all, said out loud.
#:
#: A sentinel rather than `None`, and that is the point. `None` is what an
#: argument nobody passed looks like, so a handler that forgot to say who is
#: calling would be indistinguishable from one that meant "no policy here".
#: This has to be typed, which means it can be grepped for in a review.
UNSCOPED: Final[_Unscoped] = _Unscoped()

#: What a call may say about who is making it: the groups held, or the explicit
#: refusal to say.
#:
#: `None` is a third thing and means *nobody said*, which is why this is not
#: spelled `tuple[str, ...] | None`. Once a vocabulary exists those two must not
#: collapse: "run without a caller" is a decision somebody made, and "nobody
#: said" is a handler that forgot the boundary. One is honoured, the other is
#: refused.
Held = tuple[str, ...] | _Unscoped


def reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches something with this audience.

    Overlap, not containment: a longer list means *more* people, which is what
    everyone reads an access list as meaning.

    Public because three readers ask it -- both definition formats and the
    listing -- and a private copy in each is one convention away from them
    disagreeing about who reaches what.
    """
    return audience == ALL or bool(held & set(audience))


def reaching(
    selection: Selection,
    *,
    audiences: Mapping[str, Audience],
    default: Audience,
    held: frozenset[str],
) -> Selection:
    """`selection`, keeping only the entries this caller reaches.

    `default` is the definition's own audience, used for any entry that did not
    state one. That is what makes a plain list under a policied definition mean
    "these, at my audience", so every definition written before audiences
    existed keeps its exact meaning once one is added above it.

    `ALL` and `None` pass through untouched. `ALL` is "everything available",
    which is bounded by the definition's own audience rather than by any entry;
    `None` is nothing, and nothing narrowed is still nothing.
    """
    if selection == ALL or selection is None:
        return selection
    return tuple(name for name in selection if reaches(audiences.get(name, default), held))


def refuse_dead(
    audiences: Mapping[str, Mapping[str, Audience]],
    *,
    groups: Audience,
    source: str,
    error: type[Exception],
) -> None:
    """Refuse an entry audience that the definition's own audience never admits.

    `reviewer` is `[A, B]` and writes `sql_query: [C]`. Nobody reaching
    `reviewer` is ever in `C`, so that line can never grant anything -- it is
    not a narrowing, it is a mistake, and almost always a group name typed from
    memory. Refused rather than reported, because unlike a stale central entry
    it costs nothing to fix and the file that is wrong is the file in front of
    you.

    Silent when the definition is `ALL`: everyone reaches it, so no entry
    audience can fall outside.
    """
    if groups == ALL:
        return
    admitted = set(groups)
    for field_name, entries in audiences.items():
        for entry, audience in entries.items():
            if audience == ALL or (set(audience) & admitted):
                continue
            msg = (
                f"{source}: {field_name} entry {entry!r} is for "
                f"{', '.join(audience)}, but this definition is only reachable by "
                f"{', '.join(admitted)} -- so that line never reaches anyone"
            )
            raise error(msg)


@dataclass(frozen=True)
class AccessReport:
    """What a deployment's policy leaves open, said once at startup.

    One half of what the central design reported, and the other half is gone
    rather than moved: a definition *is* the asset, so there is no such thing
    as a line naming something that is not there.
    """

    #: Definitions carrying no `groups:` line, and so reachable by everyone, as
    #: `(kind, name)`.
    #:
    #: Named because default-open must not also be silent. An absent optional
    #: field meaning "no restriction" is right -- it is what an absent field
    #: means everywhere else in these formats, and reading it as "nobody" would
    #: stop every unannotated definition working the moment a vocabulary file
    #: appeared. But it makes "we have not restricted that one yet" invisible,
    #: and this line is the whole of what stands between that and nobody
    #: noticing.
    unrestricted: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.unrestricted

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing.

        Lines rather than prints, for the reason `presentation.cli.listing`
        gives: a library that writes to stdout cannot be used by a server, and
        both reach this.
        """
        if self.is_clean:
            return ()
        return (
            "access:",
            "  no groups: line, so reachable by everyone:",
            *(f"    {kind} {name}" for kind, name in self.unrestricted),
        )


@dataclass(frozen=True)
class Groups:
    """One deployment's group vocabulary: the names, and what each contains.

    A dictionary rather than a policy. Nothing here says who reaches what --
    that is in the definitions. What this buys is the two things a definition
    cannot say for itself: that a name is real, so a typo is refused instead of
    inventing a group nobody holds, and that one name stands for several, so a
    broad group is written once instead of on every line forever.

    `names` maps each declared group to its own transitive closure, itself
    included, worked out when the document was read. Expansion happens once
    rather than on every turn, and a cycle is refused where it is written.
    """

    #: Declared name -> that name plus everything it contains, transitively.
    names: Mapping[str, tuple[str, ...]]

    def expand(self, held: Iterable[str]) -> frozenset[str]:
        """Every group a caller effectively holds, following `contains`.

        Refuses a name the vocabulary does not have. That refusal is the reason
        the vocabulary is closed: silently expanding to nothing would turn a
        typo in a caller's group list into a caller who reaches nothing, which
        looks exactly like a caller who was denied.
        """
        wanted = tuple(held)
        if unknown := tuple(name for name in wanted if name not in self.names):
            known = ", ".join(sorted(self.names)) or "none"
            msg = (
                f"unknown group(s): {', '.join(sorted(set(unknown)))}; "
                f"this deployment defines {known}"
            )
            raise AccessError(msg)
        return frozenset(one for name in wanted for one in self.names[name])

    def refuse_undeclared(self, audience: Audience, *, where: str, error: type[Exception]) -> None:
        """Refuse a definition naming a group this deployment does not declare.

        The other end of the closed vocabulary. Without it a mistyped audience
        invents a group nobody is in, and the only symptom is a tool quietly
        reachable by no one -- found weeks later by whoever needed it.
        """
        if audience == ALL:
            return
        if unknown := tuple(name for name in audience if name not in self.names):
            listed = ", ".join(repr(u) for u in sorted(set(unknown)))
            msg = (
                f"{where}: names undeclared group(s) {listed}; "
                f"this deployment defines {', '.join(sorted(self.names)) or 'none'}"
            )
            raise error(msg)


def _vocabulary(raw: object, source: str) -> dict[str, tuple[str, ...]]:
    """The declared groups and what each contains, before expansion.

    Two spellings, because the common case has no `contains` and should not
    have to write an empty mapping to say so. A list is the short form; a
    mapping is the long one. Both produce the same thing.
    """
    if raw is None:
        msg = (
            f"{source}: missing required section 'groups'; it is the closed "
            f"vocabulary every definition's audience is checked against"
        )
        raise AccessError(msg)
    if isinstance(raw, list):
        return {str(name): () for name in raw}
    if not isinstance(raw, Mapping):
        msg = f"{source}: 'groups' is a list of names, or a mapping of name to {{contains: [...]}}"
        raise AccessError(msg)

    declared: dict[str, tuple[str, ...]] = {}
    for name, body in raw.items():
        if body is None or body == {}:
            declared[str(name)] = ()
            continue
        if not isinstance(body, Mapping):
            msg = f"{source}: group {name!r} is {{contains: [...]}}, or empty"
            raise AccessError(msg)
        if complaint := fields.unrecognised(body, known={"contains"}, noun="key"):
            msg = f"{source}: group {name!r}: {complaint}"
            raise AccessError(msg)
        contains = body.get("contains") or ()
        if isinstance(contains, str):
            msg = f"{source}: group {name!r}: 'contains' is a list of group names"
            raise AccessError(msg)
        declared[str(name)] = tuple(str(one) for one in contains)
    return declared


def _closed(declared: Mapping[str, tuple[str, ...]], source: str) -> dict[str, tuple[str, ...]]:
    """Each group's transitive closure, itself included, with cycles refused.

    Depth-first with the path carried, so a cycle is reported as the whole loop
    rather than as one edge of it -- the same reason `subagent.rules` names
    every link: one edge does not tell a reader which to cut, and they may own
    none of the groups involved.
    """
    for name, contains in declared.items():
        for one in contains:
            if one not in declared:
                msg = (
                    f"{source}: group {name!r} contains {one!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)

    closure: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: tuple[str, ...]) -> tuple[str, ...]:
        if name in path:
            loop = " -> ".join((*path[path.index(name) :], name))
            msg = (
                f"{source}: groups contain themselves: {loop}. Expansion "
                f"follows every link, so a loop would never finish -- one of "
                f"these has to stop containing the next"
            )
            raise AccessError(msg)
        if name in closure:
            return closure[name]
        reached: list[str] = [name]
        for one in declared[name]:
            reached.extend(n for n in walk(one, (*path, name)) if n not in reached)
        # Written only once the whole subtree returned without raising, so a
        # memoised entry can never be a partial answer taken from inside a loop.
        closure[name] = tuple(reached)
        return closure[name]

    return {name: walk(name, ()) for name in declared}


def parse(document: Mapping[str, object], source: str) -> Groups:
    """One vocabulary document, from its decoded fields.

    Takes a mapping rather than a path: reading YAML needs a library and this
    is `domain/`. `kingfisher.infrastructure.access_policy` does that half.

    The three sections the central format had are refused *by name*, each
    saying where audiences went. A deployment upgrading has a file full of
    policy, and reading it and dropping it would be the quiet catastrophe: the
    server would come up believing it was locked down.
    """
    complaint = fields.unrecognised(document, known={"groups"}, declined=MOVED, noun="section")
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)
    return Groups(names=_closed(_vocabulary(document.get("groups"), source), source))

"""Which groups reach which assets, and what one caller's groups grant.

Static, deployment-authored policy, read once and then only asked questions.
The answer it gives is an ordinary `Capabilities` -- which is the whole design:
a group grant is not a second permission system beside the one that exists, it
is a way of *deriving* the one that exists. Everything downstream is unchanged,
including the part that matters most, which is that an ungranted tool is never
attached to the graph and an ungranted subagent is never compiled.

Three kinds are controlled and the rest are deliberately not. `builtin_tools`
is absent because deepagents registers those itself: kingfisher can only filter
them afterwards, so gating them here would buy the weakest form of the
guarantee -- see `kingfisher.infrastructure.harness.narrowing`, which records a
live run where a model called `execute` from memory. `skills` is absent because
a skill is guidance rather than a capability, and the boundary is the tools it
names.

Pure, like the rest of `domain/`: this module reads no file. The YAML half is
`kingfisher.infrastructure.access_policy`, the same split `Models` and
`kingfisher.infrastructure.model_catalogue` already have.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from kingfisher.domain import fields
from kingfisher.domain.capabilities import ALL, Capabilities

#: The kinds this format controls. Order is the order a report prints them in,
#: coarsest first, because an agent decides the most and a tool the least.
CONTROLLED: Final[tuple[str, ...]] = ("agents", "subagents", "tools")

#: Kinds a reader will reasonably expect and this format deliberately omits,
#: with the reason each is refused rather than accepted and ignored.
DECLINED: Final[Mapping[str, str]] = {
    "skills": (
        "skills are not controlled here: a skill is guidance rather than a "
        "capability, and what bounds it is the tools it names"
    ),
    "builtin_tools": (
        "builtin tools are not controlled here: deepagents registers them "
        "itself, so they can be filtered but never left out of the graph"
    ),
    "middleware": (
        "middleware is not controlled here: it is already granted rather than "
        "inherited, and its names come from the deployment's own registry"
    ),
}

#: Who may reach one asset: `"*"` for everyone, or exactly these groups.
#: No `None`. An asset nobody may reach is written by leaving it out, which is
#: what makes the file a whitelist rather than a whitelist with a hole in it.
Audience = Literal["*"] | tuple[str, ...]


class AccessError(ValueError):
    """The access policy is malformed, or a caller named a group it does not define."""


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
#: spelled `tuple[str, ...] | None`. Once a policy exists those two must not
#: collapse: "run without a caller" is a decision somebody made, and "nobody
#: said" is a handler that forgot the boundary. One is honoured, the other is
#: refused.
#:
#: Here rather than beside the service that threads it, because it is a fact
#: about access rather than about orchestration -- and because the command needs
#: to name the type too, through the public API.
Held = tuple[str, ...] | _Unscoped


def _reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches an asset with this audience.

    Overlap, not containment: a longer list means *more* people, which is what
    everyone reads an access list as meaning.
    """
    return audience == ALL or bool(held & set(audience))


@dataclass(frozen=True)
class AccessReport:
    """Where the policy and the workspace disagree, as `(kind, name)` pairs.

    Two halves of one rule read in opposite directions, which is the shape
    `withheld` and `all_but` already have in `capabilities`: one turns a grant
    into what it leaves out, the other turns what to leave out into a grant.

    Neither half is fatal. A stale line grants nothing, so it cannot be wrong
    in the dangerous direction, and refusing to start over one would couple a
    policy deploy to a catalogue deploy -- removing a tool would take the
    server down until someone edited a file they may not own. An unreachable
    asset is the whitelist going stale on its own, which is exactly what
    `withheld` exists to say out loud rather than leave for a confused user.
    """

    #: Policy lines naming an asset this workspace does not offer.
    listed_not_offered: tuple[tuple[str, str], ...] = ()
    #: Assets this workspace offers that no group can reach.
    offered_unreachable: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.listed_not_offered and not self.offered_unreachable

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing.

        Lines rather than prints, for the reason `presentation.cli.listing`
        gives: a library that writes to stdout cannot be used by a server, and
        both reach this.
        """
        if self.is_clean:
            return ()
        said: list[str] = ["access:"]
        for heading, pairs in (
            ("listed but not offered", self.listed_not_offered),
            ("offered, no group can reach", self.offered_unreachable),
        ):
            if not pairs:
                continue
            said.append(f"  {heading}:")
            said.extend(f"    {kind[:-1]} {name}" for kind, name in pairs)
        return tuple(said)


@dataclass(frozen=True)
class Access:
    """One deployment's policy: the group vocabulary, and who reaches what.

    `groups` maps each declared name to its own transitive closure, itself
    included, worked out when the document was read. Expansion happens once
    rather than on every turn, and a cycle is refused where it is written
    rather than found by a stack overflow on a Tuesday.
    """

    #: Declared name -> that name plus everything it contains, transitively.
    groups: Mapping[str, tuple[str, ...]]
    #: Kind -> asset name -> who reaches it. Kinds are `CONTROLLED`.
    entries: Mapping[str, Mapping[str, Audience]]

    def expand(self, held: Iterable[str]) -> frozenset[str]:
        """Every group a caller effectively holds, following `contains`.

        Refuses a name the vocabulary does not have. That refusal is the reason
        the vocabulary is closed: silently expanding to nothing would turn a
        typo in a caller's group list into a caller who reaches nothing, which
        looks exactly like a caller who was denied.
        """
        wanted = tuple(held)
        if unknown := tuple(name for name in wanted if name not in self.groups):
            known = ", ".join(sorted(self.groups)) or "none"
            msg = (
                f"unknown group(s): {', '.join(sorted(set(unknown)))}; "
                f"this deployment defines {known}"
            )
            raise AccessError(msg)
        return frozenset(one for name in wanted for one in self.groups[name])

    def reachable(self, kind: str, held: frozenset[str]) -> tuple[str, ...]:
        """The names of one kind this caller reaches, in the file's own order."""
        return tuple(
            name
            for name, audience in self.entries.get(kind, {}).items()
            if _reaches(audience, held)
        )

    def resolve(self, held: Iterable[str]) -> Capabilities:
        """What a caller holding these groups may use, as an ordinary grant.

        Every axis this format does not control is `ALL`, which is the identity
        for `intersect` -- so composing this with a deployment's own grants
        subtracts exactly the controlled kinds and nothing else. `None` there
        would revoke, silently, whatever the deployment had granted.

        `agents` has no axis on `Capabilities` and is not returned here. A
        request names an agent before there is anything to narrow, so it is
        checked where a session is opened instead.
        """
        expanded = self.expand(held)
        return Capabilities(
            builtin_tools=ALL,
            tools=self.reachable("tools", expanded),
            skills=ALL,
            subagents=self.reachable("subagents", expanded),
            middleware=ALL,
            endpoints=ALL,
            models=ALL,
            memory=None,
        )

    def reconciled(self, offered: Mapping[str, Iterable[str]]) -> tuple[Access, AccessReport]:
        """This policy with stale entries dropped, and what the two disagree on.

        Dropping rather than keeping is load-bearing rather than tidy. The
        resolved grant reaches `Offering.refuse_unknown`, which refuses a name
        the workspace does not offer -- so a policy line left pointing at a
        deleted tool would turn every turn into a refusal instead of the report
        this returns.

        `offered` is what the catalogue actually holds, per kind, which is why
        this is called where the catalogue is known rather than where the file
        is read. Names are matched exactly as the catalogue writes them: where
        two files define one `fetch`, it offers `vendor_a/fetch.py::fetch`, and
        a policy line saying the bare name is genuinely stale -- granting it
        would have to mean one of the two, and there is nothing to say which.
        """
        held = {kind: tuple(names) for kind, names in offered.items()}
        kept: dict[str, dict[str, Audience]] = {}
        missing: list[tuple[str, str]] = []
        for kind in CONTROLLED:
            available = set(held.get(kind, ()))
            kept[kind] = {}
            for name, audience in self.entries.get(kind, {}).items():
                if name in available:
                    kept[kind][name] = audience
                else:
                    missing.append((kind, name))

        unreachable = [
            (kind, name)
            for kind in CONTROLLED
            for name in held.get(kind, ())
            if name not in kept[kind]
        ]
        return (
            Access(groups=self.groups, entries=kept),
            AccessReport(
                listed_not_offered=tuple(missing),
                offered_unreachable=tuple(unreachable),
            ),
        )


def _vocabulary(raw: object, source: str) -> dict[str, tuple[str, ...]]:
    """The declared groups and what each contains, before expansion.

    Two spellings, because the common case has no `contains` and should not
    have to write an empty mapping to say so. A list is the short form; a
    mapping is the long one. Both produce the same thing.
    """
    if raw is None:
        msg = (
            f"{source}: missing required section 'groups'; it is the closed "
            f"vocabulary every other section is checked against"
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


def _audience(
    raw: object, *, kind: str, asset: str, known: Mapping[str, object], source: str
) -> Audience:
    """One asset's group list, checked against the vocabulary."""
    where = f"{source}: {kind} {asset!r}"
    shape = f'{where}: a list of group names, or ["*"] for everyone -- got {raw!r}'
    if isinstance(raw, str) or not isinstance(raw, list):
        # A bare string is one name, or a typo for `*`. Iterating its
        # characters -- the default for a `for` over a `str` -- is the worst
        # available answer, and is the mistake `capabilities._normalise`
        # refuses for the same reason.
        raise AccessError(shape)
    names = tuple(str(one) for one in raw)
    if not names:
        msg = (
            f"{where}: an empty list would mean nobody, which is what leaving "
            f"the entry out already means -- leave it out, or name the groups"
        )
        raise AccessError(msg)
    if ALL in names and len(names) > 1:
        msg = f'{where}: ["*"] is everyone, so it cannot mean both that and {names}'
        raise AccessError(msg)
    if names == (ALL,):
        return ALL
    if unknown := tuple(one for one in names if one not in known):
        listed = ", ".join(repr(u) for u in sorted(set(unknown)))
        msg = (
            f"{where}: names undeclared group(s) {listed}; "
            f"this file defines {', '.join(sorted(known))}"
        )
        raise AccessError(msg)
    return names


def parse(document: Mapping[str, object], source: str) -> Access:
    """One policy document, from its decoded fields.

    Takes a mapping rather than a path: reading YAML needs a library and this
    is `domain/`. `kingfisher.infrastructure.access_policy` does that half.

    A section this format does not define is refused rather than dropped, for
    the reason every format here gives: a key we ignore is a key the author
    believes took effect. Three sections get their *own* refusal, because a
    generic "unknown key" reads as "not supported yet" and sends someone
    looking for a workaround that does not exist.
    """
    complaint = fields.unrecognised(
        document, known={"groups", *CONTROLLED}, declined=DECLINED, noun="section"
    )
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)

    groups = _closed(_vocabulary(document.get("groups"), source), source)

    entries: dict[str, dict[str, Audience]] = {}
    for kind in CONTROLLED:
        section = document.get(kind) or {}
        if not isinstance(section, Mapping):
            msg = f"{source}: {kind!r} is a mapping of name to a list of groups"
            raise AccessError(msg)
        entries[kind] = {
            str(asset): _audience(raw, kind=kind, asset=str(asset), known=groups, source=source)
            for asset, raw in section.items()
        }
    return Access(groups=groups, entries=entries)

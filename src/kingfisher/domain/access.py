"""Who reaches what: the group vocabulary, and the rule the definitions apply."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, Literal

from kingfisher.domain import fields
from kingfisher.domain.capabilities import ALL, Selection

#: The selection fields that may be written as a mapping of name to audience.
#: `builtin_tools` is absent because deepagents registers those itself: kingfisher
#: can only filter them once a graph is built, never leave them out of one, so an
#: audience here would promise a boundary nothing can hold. Control them through
#: which *agents* a group may open instead.
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

#: One audience entry that is satisfied only by holding *every* name in it.
#:
#: A `frozenset` because an entry that literally is a set of names reads as "all
#: of these", it stays hashable so an `Audience` remains comparable, and order
#: carries no meaning -- the listing sorts it before showing it.
Requires = frozenset[str]

#: Who may reach one thing: `"*"` for everyone, or exactly these entries.
#:
#: The tuple is an **or** and an entry may be an **and**: a plain name is held or it is
#: not, and a `Requires` is satisfied only in full. That gives or-of-ands, which is the
#: shape access rules actually take, out of one field and with no rule about how two
#: fields combine.
Audience = Literal["*"] | tuple[str | Requires, ...]


@dataclass(frozen=True)
class Stated:
    """What one definition says about who reaches what."""

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

#: What a call may say about who is making it: the groups held, or the explicit refusal
#: to say.
Held = Sequence[str] | _Unscoped


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


def _singular(field_name: str) -> str:
    """`tools` -> `tool`. `skills` is the one that does not just lose an s."""
    return "skill" if field_name == "skills" else field_name[:-1]


def spell(audience: Audience) -> str:
    """One audience, written the way the formats and the listing write it."""
    if audience == ALL:
        return ALL
    return ", ".join(
        "+".join(sorted(one)) if isinstance(one, frozenset) else one for one in audience
    )


@dataclass(frozen=True)
class AccessReport:
    """What a deployment's policy leaves open, said once at startup."""

    #: Definitions carrying no `groups:` line, and so reachable by everyone, as `(kind,
    #: name)`.
    unrestricted: tuple[tuple[str, str], ...] = ()

    #: Entries naming a group their definition's own audience never mentions, as
    #: `(where, audience)`. Reached only by a caller holding one of each.
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
            said.append("  no groups: line, so reachable by everyone:")
            said.extend(f"    {kind} {name}" for kind, name in self.unrestricted)
        if self.narrowed:
            said.append("  narrows past this definition's own audience,")
            said.append("  so it reaches only callers holding both:")
            said.extend(f"    {where}  [{who}]" for where, who in self.narrowed)
        return tuple(said)


@dataclass(frozen=True)
class Groups:
    """One deployment's group vocabulary: the names, and what each contains."""

    #: Declared name -> that name plus everything it contains, transitively.
    names: Mapping[str, tuple[str, ...]]
    #: Declared name -> the groups a caller must hold for it to apply, for the
    #: names written with `all_of`. Absent for every ordinary group.
    compounds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def mentions(self, audience: Audience) -> frozenset[str]:
        """Every group name an audience touches, following `contains` and `all_of`."""
        if audience == ALL:
            return frozenset()
        seen: set[str] = set()
        queue = [n for one in audience for n in ((one,) if isinstance(one, str) else one)]
        while queue:
            name = queue.pop()
            if name in seen:
                continue
            seen.add(name)
            queue.extend(self.names.get(name, ()))
            queue.extend(self.compounds.get(name, ()))
        return frozenset(seen)

    def expand(self, held: Iterable[str]) -> frozenset[str]:
        """Every group a caller effectively holds, following `contains` then `all_of`."""
        wanted = tuple(held)
        if derived := sorted({name for name in wanted if name in self.compounds}):
            listed = "; ".join(
                f"{name!r} means all of [{', '.join(sorted(self.compounds[name]))}]"
                for name in derived
            )
            msg = (
                f"derived group(s) cannot be held: {listed}. A name written with "
                f"`all_of` is what holding its parts adds up to, not something to "
                f"present -- name the parts instead"
            )
            raise AccessError(msg)
        if unknown := tuple(name for name in wanted if name not in self.names):
            known = ", ".join(sorted(self.names)) or "none"
            msg = (
                f"unknown group(s): {', '.join(sorted(set(unknown)))}; "
                f"this deployment defines {known}"
            )
            raise AccessError(msg)

        reached = {one for name in wanted for one in self.names[name]}
        while gained := {
            name
            for name, parts in self.compounds.items()
            if name not in reached and all(part in reached for part in parts)
        }:
            # `names[name]`, not `name`: a compound may itself be contained in
            # something, and a caller who has just earned it earns that too.
            # Adding only the bare name would make one written into a `contains`
            # chain reach less than the same name written by hand.
            for name in gained:
                reached.update(self.names[name])
        return frozenset(reached)

    def refuse_undeclared(self, audience: Audience, *, where: str, error: type[Exception]) -> None:
        """Refuse a definition naming a group this deployment does not declare."""
        if audience == ALL:
            return
        named = tuple(
            n for one in audience for n in ((one,) if isinstance(one, str) else sorted(one))
        )
        if unknown := tuple(name for name in named if name not in self.names):
            listed = ", ".join(repr(u) for u in sorted(set(unknown)))
            msg = (
                f"{where}: names undeclared group(s) {listed}; "
                f"this deployment defines {', '.join(sorted(self.names)) or 'none'}"
            )
            raise error(msg)

    def narrowing_in(
        self,
        audiences: Mapping[str, Mapping[str, Audience]],
        *,
        groups: Audience,
        where: str,
    ) -> tuple[tuple[str, str], ...]:
        """Entries naming a group this definition's own audience never mentions.

        An entry audience is already an **and** with the definition's, because the
        only way to reach an entry is through the definition holding it --
        `agent_named` refuses a caller who cannot open the agent, and nothing else
        hands out a spec. So `[senior]` under `[analysts, auditors]` means "everyone
        who opens this agent, and is senior", which is a good second requirement.
        """
        if groups == ALL:
            return ()
        admitted = self.mentions(groups)
        return tuple(
            (f"{where}: {_singular(field_name)} {entry}", spell(audience))
            for field_name, entries in audiences.items()
            for entry, audience in entries.items()
            if audience != ALL and not (self.mentions(audience) & admitted)
        )


#: The declared groups and what each contains, beside the ones written `all_of`.
_Vocabulary = tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]


def _vocabulary(raw: object, source: str) -> _Vocabulary:
    """The declared groups, what each contains, and what each requires."""
    if raw is None:
        msg = (
            f"{source}: missing required section 'groups'; it is the closed "
            f"vocabulary every definition's audience is checked against"
        )
        raise AccessError(msg)
    if isinstance(raw, list):
        return {str(name): () for name in raw}, {}
    if not isinstance(raw, Mapping):
        msg = (
            f"{source}: 'groups' is a list of names, or a mapping of name to "
            f"{{contains: [...]}} or {{all_of: [...]}}"
        )
        raise AccessError(msg)

    declared: dict[str, tuple[str, ...]] = {}
    compounds: dict[str, tuple[str, ...]] = {}
    for name, body in raw.items():
        if body is None or body == {}:
            declared[str(name)] = ()
            continue
        if not isinstance(body, Mapping):
            msg = f"{source}: group {name!r} is {{contains: [...]}} or {{all_of: [...]}}, or empty"
            raise AccessError(msg)
        if complaint := fields.unrecognised(body, known={"contains", "all_of"}, noun="key"):
            msg = f"{source}: group {name!r}: {complaint}"
            raise AccessError(msg)
        if "contains" in body and "all_of" in body:
            msg = (
                f"{source}: group {name!r} has both 'contains' and 'all_of'. "
                f"'contains' says what this name grants and 'all_of' says what a "
                f"caller must hold for it to apply -- a group that is both is a "
                f"question with no answer"
            )
            raise AccessError(msg)
        for key, into in (("contains", declared), ("all_of", compounds)):
            if key not in body:
                continue
            listed = body[key] or ()
            # A string is called out because it reads as a list of letters. A
            # mapping is the one that was silently wrong: it is truthy and it
            # iterates, so `all_of: {finance: senior}` became the single name
            # `finance` and threw away what was written beside it. Everything else
            # raised `TypeError` out of the comprehension below, which names
            # neither the file nor the group.
            if isinstance(listed, str) or not isinstance(listed, (list, tuple)):
                msg = (
                    f"{source}: group {name!r}: {key!r} is a list of group names "
                    f"-- got {listed!r}"
                )
                raise AccessError(msg)
            if not listed:
                said = (
                    "a group requiring nothing is reached by everyone, which is "
                    "what a plain group already means"
                    if key == "all_of"
                    else "leave it out to declare a plain group"
                )
                msg = f"{source}: group {name!r}: {key!r} is empty -- {said}"
                raise AccessError(msg)
            into[str(name)] = tuple(str(one) for one in listed)
        # Declared either way: a compound is a name in the vocabulary like any
        # other, and `names` is what says a name exists at all.
        declared.setdefault(str(name), ())
    return declared, compounds


def _closed(declared: Mapping[str, tuple[str, ...]], source: str) -> dict[str, tuple[str, ...]]:
    """Each group's transitive closure, itself included, with cycles refused."""
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
    """One vocabulary document, from its decoded fields."""
    complaint = fields.unrecognised(document, known={"groups"}, declined=MOVED, noun="section")
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)
    declared, compounds = _vocabulary(document.get("groups"), source)
    _refuse_undeclared_parts(declared, compounds, source)
    _refuse_granted_compounds(declared, compounds, source)
    _refuse_compound_loops(compounds, source)
    return Groups(names=_closed(declared, source), compounds=compounds)


def _refuse_granted_compounds(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a `contains` that hands out a compound rather than its parts."""
    for name, holds in declared.items():
        for one in holds:
            if one in compounds:
                parts = ", ".join(compounds[one])
                msg = (
                    f"{source}: group {name!r} contains {one!r}, which is derived "
                    f"rather than held -- it means all of [{parts}]. Handing it "
                    f"over directly is the requirement defeated by the file that "
                    f"declares it; write `contains: [{parts}]` instead, which "
                    f"reaches the same people and says why"
                )
                raise AccessError(msg)


def _refuse_undeclared_parts(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a compound built from a name this file never declares."""
    for name, parts in compounds.items():
        for part in parts:
            if part not in declared:
                msg = (
                    f"{source}: group {name!r} requires {part!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)


def _refuse_compound_loops(compounds: Mapping[str, tuple[str, ...]], source: str) -> None:
    """Refuse a compound that requires itself, directly or through others."""
    walked: set[str] = set()

    def walk(name: str, path: tuple[str, ...]) -> None:
        if name in path:
            loop = " -> ".join((*path[path.index(name) :], name))
            msg = (
                f"{source}: groups require themselves: {loop}. A requirement "
                f"loop can never be entered, so none of these is ever held -- "
                f"one of them has to stop requiring the next"
            )
            raise AccessError(msg)
        if name in walked:
            return
        for part in compounds.get(name, ()):
            walk(part, (*path, name))
        walked.add(name)

    for name in compounds:
        walk(name, ())

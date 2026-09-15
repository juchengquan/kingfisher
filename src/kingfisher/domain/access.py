"""Who reaches what: the source-id vocabulary, and the rule the definitions apply."""

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
#: which *agents* a source id may open instead.
AUDIENCED: Final[tuple[str, ...]] = ("tools", "subagents", "skills")

#: Sections the central format defined, and where each has gone. Refused rather
#: than ignored, because a deployment upgrading has a file full of policy that
#: would otherwise be read and dropped in silence -- which is the single failure
#: this whole area exists to prevent.
#: Where audiences went, said once and shared by the three keys that used to
#: hold them.
_WENT = (
    "audiences live in the definition now: write `source_ids:` in the file itself, "
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
    source_ids: Audience = ALL
    #: Field name -> entry name -> who reaches that entry there.
    entries: Mapping[str, Mapping[str, Audience]] = field(default_factory=dict)

    @property
    def says_nothing(self) -> bool:
        """Whether this definition restricts anyone at all."""
        return self.source_ids == ALL and not self.entries

    def of(self, field_name: str) -> Mapping[str, Audience]:
        """One field's per-entry audiences, or nothing."""
        return self.entries.get(field_name, {})


class AccessError(ValueError):
    """The vocabulary is malformed, or a caller named a source id it does not define."""


class _Unscoped:
    """The type of `UNSCOPED`, so that it is not confusable with a source id list."""

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

#: What a call may say about who is making it: the source ids held, or the explicit refusal
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
        "{" + ", ".join(sorted(one)) + "}" if isinstance(one, frozenset) else one
        for one in audience
    )


@dataclass(frozen=True)
class AccessReport:
    """What a deployment's policy leaves open, said once at startup."""

    #: Definitions carrying no `source_ids:` line, and so reachable by everyone, as `(kind,
    #: name)`.
    unrestricted: tuple[tuple[str, str], ...] = ()

    #: Entries naming a source id their definition's own audience never mentions, as
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
            said.append("  no source_ids: line, so reachable by everyone:")
            said.extend(f"    {kind} {name}" for kind, name in self.unrestricted)
        if self.narrowed:
            said.append("  narrows past this definition's own audience,")
            said.append("  so it reaches only callers holding both:")
            said.extend(f"    {where}  [{who}]" for where, who in self.narrowed)
        return tuple(said)


@dataclass(frozen=True)
class SourceIds:
    """One deployment's source-id vocabulary: the names, and what each covers."""

    #: Declared name -> that name plus everything it covers, transitively.
    names: Mapping[str, tuple[str, ...]]
    #: Declared name -> the source ids a caller must hold for it to apply, for the
    #: names written as a set. Absent for every ordinary source id.
    compounds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def mentions(self, audience: Audience) -> frozenset[str]:
        """Every source id an audience touches, following what names cover and require."""
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
        """Every source id a caller effectively holds: what they cover, then what derives."""
        wanted = tuple(held)
        if derived := sorted({name for name in wanted if name in self.compounds}):
            listed = "; ".join(
                f"{name!r} means all of [{', '.join(sorted(self.compounds[name]))}]"
                for name in derived
            )
            msg = (
                f"derived source id(s) cannot be held: {listed}. A name written as a "
                f"set is what holding its parts adds up to, not something to "
                f"present -- name the parts instead"
            )
            raise AccessError(msg)
        if unknown := tuple(name for name in wanted if name not in self.names):
            known = ", ".join(sorted(self.names)) or "none"
            msg = (
                f"unknown source id(s): {', '.join(sorted(set(unknown)))}; "
                f"this deployment defines {known}"
            )
            raise AccessError(msg)

        reached = {one for name in wanted for one in self.names[name]}
        while gained := {
            name
            for name, parts in self.compounds.items()
            if name not in reached and all(part in reached for part in parts)
        }:
            # `names[name]`, not `name`: a compound may itself be covered by
            # something, and a caller who has just earned it earns that too.
            # Adding only the bare name would make one written into a covers
            # chain reach less than the same name written by hand.
            for name in gained:
                reached.update(self.names[name])
        return frozenset(reached)

    def refuse_undeclared(self, audience: Audience, *, where: str, error: type[Exception]) -> None:
        """Refuse a definition naming a source id this deployment does not declare."""
        if audience == ALL:
            return
        named = tuple(
            n for one in audience for n in ((one,) if isinstance(one, str) else sorted(one))
        )
        if unknown := tuple(name for name in named if name not in self.names):
            listed = ", ".join(repr(u) for u in sorted(set(unknown)))
            msg = (
                f"{where}: names undeclared source id(s) {listed}; "
                f"this deployment defines {', '.join(sorted(self.names)) or 'none'}"
            )
            raise error(msg)

    def narrowing_in(
        self,
        audiences: Mapping[str, Mapping[str, Audience]],
        *,
        source_ids: Audience,
        where: str,
    ) -> tuple[tuple[str, str], ...]:
        """Entries naming a source id this definition's own audience never mentions.

        An entry audience is already an **and** with the definition's, because the
        only way to reach an entry is through the definition holding it --
        `agent_named` refuses a caller who cannot open the agent, and nothing else
        hands out a spec. So `[pii]` under `[sales_db, audit_log]` means "everyone
        who opens this agent, and holds `pii`", which is a good second requirement.
        """
        if source_ids == ALL:
            return ()
        admitted = self.mentions(source_ids)
        return tuple(
            (f"{where}: {_singular(field_name)} {entry}", spell(audience))
            for field_name, entries in audiences.items()
            for entry, audience in entries.items()
            if audience != ALL and not (self.mentions(audience) & admitted)
        )


#: The declared source ids and what each covers, beside the ones written as a set.
_Vocabulary = tuple[dict[str, tuple[str, ...]], dict[str, tuple[str, ...]]]


def _vocabulary(raw: object, source: str) -> _Vocabulary:
    """The declared source ids, what each covers, and what each requires."""
    if raw is None:
        msg = (
            f"{source}: missing required section 'source_ids'; it is the closed "
            f"vocabulary every definition's audience is checked against"
        )
        raise AccessError(msg)
    if isinstance(raw, list):
        return {str(name): () for name in raw}, {}
    if not isinstance(raw, Mapping):
        msg = (
            f"{source}: 'source_ids' is a list of names, or a mapping of name to "
            f"[a, b] for what it covers or {{a, b}} for what it requires"
        )
        raise AccessError(msg)

    _refuse_retired_spelling(raw, source)

    declared: dict[str, tuple[str, ...]] = {}
    compounds: dict[str, tuple[str, ...]] = {}
    for name, body in raw.items():
        if body is None:
            declared[str(name)] = ()
        elif isinstance(body, (list, tuple)):
            declared[str(name)] = _covers(body, name=str(name), source=source)
        elif isinstance(body, Mapping):
            compounds[str(name)] = _requires(body, name=str(name), source=source)
        else:
            msg = (
                f"{source}: source id {name!r} is [a, b] for what it covers, "
                f"{{a, b}} for what it requires, or nothing at all -- got {body!r}"
            )
            raise AccessError(msg)
        # Declared either way: a compound is a name in the vocabulary like any
        # other, and `names` is what says a name exists at all.
        declared.setdefault(str(name), ())
    return declared, compounds


def _covers(body: Sequence[object], *, name: str, source: str) -> tuple[str, ...]:
    """The source ids a name hands out, from the list form."""
    if not body:
        msg = (
            f"{source}: source id {name!r} covers nothing -- leave the body out "
            f"to declare a plain source id"
        )
        raise AccessError(msg)
    # The inline half of the rule `_refuse_granted_compounds` holds for a name.
    # Refused here rather than there because an inline requirement has no name
    # to look up, and a covers list is the one place it reads as legal: a
    # requirement that can be handed over is the requirement defeated by the
    # file imposing it.
    for one in body:
        if isinstance(one, Mapping):
            parts = ", ".join(str(part) for part in one) or "..."
            msg = (
                f"{source}: source id {name!r} covers {{{parts}}}, which is a "
                f"requirement rather than a source id anyone holds. Handing one "
                f"over is the requirement defeated by the file that writes it; "
                f"cover [{parts}] instead, which reaches the same callers"
            )
            raise AccessError(msg)
    return tuple(str(one) for one in body)


def _requires(body: Mapping[object, object], *, name: str, source: str) -> tuple[str, ...]:
    """The source ids a caller must hold together, from the set form."""
    # The shape that was silently wrong before it was refused: `{finance: senior}`
    # is a mapping with a value, not a set of two names, and reading it as a set
    # would take `finance` and throw away what was written beside it.
    if valued := sorted(str(key) for key, value in body.items() if value is not None):
        msg = (
            f"{source}: source id {name!r} requires {{{', '.join(valued)}}} with "
            f"something written after it. A requirement is a set of names -- "
            f"write {{a, b}}, not {{a: b}}"
        )
        raise AccessError(msg)
    return tuple(str(key) for key in body)


#: The bodies the vocabulary took before shapes replaced keywords, and what each is
#: written as now. `A: {}` is here because it was the short form for a plain name;
#: read as a set it requires nothing, which is everyone.
_RETIRED: Final[tuple[str, ...]] = ("contains", "all_of")


def _refuse_retired_spelling(raw: Mapping[object, object], source: str) -> None:
    """Refuse a whole vocabulary written the old way, naming every line to change.

    One refusal for the file rather than one per name. A deployment upgrading has a
    file full of policy and fixes it once: a message per source id makes them restart
    for the next one, and the first refusal a realistic old file earns is a `{}` line
    that says nothing about the `contains` further down.
    """
    changes: list[tuple[str, str]] = []
    for name, body in raw.items():
        if not isinstance(body, Mapping):
            continue
        # `{contains, all_of}` is two names in a set, not a keyword with a value,
        # so the value is what separates a retired spelling from a legal one.
        written = tuple(key for key in _RETIRED if body.get(key) is not None)
        if not (written or body == {}):
            continue
        if not written:
            changes.append((f"{name}: {{}}", f"{name}:"))
            continue
        listed = body[written[0]]
        parts = (
            ", ".join(str(one) for one in listed)
            if isinstance(listed, (list, tuple))
            else "..."
        )
        was = f"{name}: {{{written[0]}: [{parts}]}}"
        now = f"{name}: [{parts}]" if written[0] == "contains" else f"{name}: {{{parts}}}"
        changes.append((was, now))
    if not changes:
        return

    width = max(len(was) for was, _ in changes)
    lines = "\n".join(f"  {was.ljust(width)}  ->  {now}" for was, now in changes)
    msg = (
        f"{source}: this vocabulary is written in the retired spelling. A list is "
        f"what a name covers and a set is what it requires, so the shape says which "
        f"and there is no word to get wrong. {len(changes)} source id(s) to "
        f"change:\n\n{lines}"
    )
    raise AccessError(msg)


def _closed(declared: Mapping[str, tuple[str, ...]], source: str) -> dict[str, tuple[str, ...]]:
    """Each source id's transitive closure, itself included, with cycles refused."""
    for name, covers in declared.items():
        for one in covers:
            if one not in declared:
                msg = (
                    f"{source}: source id {name!r} covers {one!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)

    closure: dict[str, tuple[str, ...]] = {}

    def walk(name: str, path: tuple[str, ...]) -> tuple[str, ...]:
        if name in path:
            loop = " -> ".join((*path[path.index(name) :], name))
            msg = (
                f"{source}: source ids cover themselves: {loop}. Expansion "
                f"follows every link, so a loop would never finish -- one of "
                f"these has to stop covering the next"
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


def parse(document: Mapping[str, object], source: str) -> SourceIds:
    """One vocabulary document, from its decoded fields."""
    complaint = fields.unrecognised(document, known={"source_ids"}, declined=MOVED, noun="section")
    if complaint is not None:
        msg = f"{source}: {complaint}"
        raise AccessError(msg)
    declared, compounds = _vocabulary(document.get("source_ids"), source)
    _refuse_undeclared_parts(declared, compounds, source)
    _refuse_granted_compounds(declared, compounds, source)
    _refuse_compound_loops(compounds, source)
    return SourceIds(names=_closed(declared, source), compounds=compounds)


def _refuse_granted_compounds(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a covers list that hands out a compound rather than its parts."""
    for name, holds in declared.items():
        for one in holds:
            if one in compounds:
                parts = ", ".join(compounds[one])
                msg = (
                    f"{source}: source id {name!r} covers {one!r}, which is derived "
                    f"rather than held -- it means all of {{{parts}}}. Handing it "
                    f"over directly is the requirement defeated by the file that "
                    f"declares it; cover [{parts}] instead, which reaches the same "
                    f"callers and says why"
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
                    f"{source}: source id {name!r} requires {part!r}, which is not "
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
                f"{source}: source ids require themselves: {loop}. A requirement "
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

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

from collections.abc import Iterable, Mapping, Sequence
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

#: One audience entry that is satisfied only by holding *every* name in it.
#:
#: A `frozenset` because an entry that literally is a set of names reads as "all
#: of these", it stays hashable so an `Audience` remains comparable, and order
#: carries no meaning -- the listing sorts it before showing it.
Requires = frozenset[str]

#: Who may reach one thing: `"*"` for everyone, or exactly these entries.
#:
#: The tuple is an **or** and an entry may be an **and**: a plain name is held or
#: it is not, and a `Requires` is satisfied only in full. That gives or-of-ands,
#: which is the shape access rules actually take, out of one field and with no
#: rule about how two fields combine.
#:
#: No `None`. "Nobody" is not a state a definition can be in -- the absence of
#: an audience means it inherits the one around it, and a definition nobody may
#: reach is written by giving it a group nobody holds.
Audience = Literal["*"] | tuple[str | Requires, ...]

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
#: spelled `Sequence[str] | None`. Once a vocabulary exists those two must not
#: collapse: "run without a caller" is a decision somebody made, and "nobody
#: said" is a handler that forgot the boundary. One is honoured, the other is
#: refused.
#:
#: A `Sequence`, not a tuple. It was `tuple[str, ...]` while `for_groups` was
#: the way in and coerced whatever it was handed; `groups=` is the way in now,
#: `["A"]` is the obvious thing to write, and a type that refused it would be
#: refusing the documented form. Every use of this alias is a *parameter*, so
#: widening it loosens no guarantee anything returns.
#:
#: A `str` satisfies `Sequence[str]` and always will, so the type cannot catch
#: `groups="analysts"` -- eight one-letter group names. `Kingfisher.held_for`
#: refuses it at runtime instead.
Held = Sequence[str] | _Unscoped


def reaches(audience: Audience, held: frozenset[str]) -> bool:
    """Whether a caller holding `held` reaches something with this audience.

    Overlap, not containment: a longer list means *more* people, which is what
    everyone reads an access list as meaning.

    Public because several readers ask it, and a private copy in each is one
    convention away from them disagreeing about who reaches what. Said without
    naming them: this listed "both definition formats and the listing", which
    are `reaching`'s callers rather than this one's -- the formats ask that, and
    it asks this. Its own callers have since become three in `application/`, and
    a list of them here goes stale every time a fourth appears.

    A *named* compound needs no case here: `Groups.expand` has already put it
    into `held` if the caller's groups add up to it, so by the time an audience
    is asked, the name is either held or it is not, exactly like any other. Only
    the inline form is resolved here, because it has no name to have been
    derived under.
    """
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


def _singular(field_name: str) -> str:
    """`tools` -> `tool`. `skills` is the one that does not just lose an s.

    A copy of the printer's, which is the lesser of two evils: the report is
    assembled here so that a server and the command say the same thing, and
    `domain/` may not import `presentation/`.
    """
    return "skill" if field_name == "skills" else field_name[:-1]


def spell(audience: Audience) -> str:
    """One audience, written the way the formats and the listing write it.

    `a+b` for a conjunction, sorted so that two runs of the same file say the
    same thing. Shared rather than copied because the listing shows audiences
    and the refusals quote them, and a reader comparing an error against a
    `kingfisher list` should not have to translate between two spellings.
    """
    if audience == ALL:
        return ALL
    return ", ".join(
        "+".join(sorted(one)) if isinstance(one, frozenset) else one for one in audience
    )


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

    #: Entries naming a group their definition's own audience never mentions,
    #: as `(where, audience)`. Reached only by a caller holding one of each.
    #:
    #: Reported rather than refused, and it was refused for two commits. The
    #: refusal could not tell the two readings apart: `[senior]` under
    #: `[analysts, auditors]` is a deliberate second requirement -- everyone who
    #: opens this agent, but this tool wants seniority too -- and `[auditors]`
    #: under `[analysts]` is somebody who meant to widen and has written
    #: something that reaches nobody. Same shape, opposite intents, and only the
    #: author knows which.
    #:
    #: So the information is kept and the veto is not. That is the trade
    #: `unrestricted` above already makes: a thing worth noticing, said once,
    #: where an operator sees it.
    narrowed: tuple[tuple[str, str], ...] = ()

    @property
    def is_clean(self) -> bool:
        return not (self.unrestricted or self.narrowed)

    def lines(self) -> tuple[str, ...]:
        """The report, ready to print, or nothing at all when there is nothing.

        Lines rather than prints, for the reason `presentation.cli.listing`
        gives: a library that writes to stdout cannot be used by a server, and
        both reach this.
        """
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
    """One deployment's group vocabulary: the names, and what each contains.

    A dictionary rather than a policy. Nothing here says who reaches what --
    that is in the definitions. What this buys is the two things a definition
    cannot say for itself: that a name is real, so a typo is refused instead of
    inventing a group nobody holds, and that one name stands for several, so a
    broad group is written once instead of on every line forever.

    `names` maps each declared group to its own transitive closure, itself
    included, worked out when the document was read. Expansion happens once
    rather than on every turn, and a cycle is refused where it is written.

    `compounds` is the other direction, and the only place this file holds
    something with a rule in it: `contains` says what one name *grants*, and
    `all_of` says what a caller must hold for one to *apply*. Still vocabulary
    -- both answer "what does this name mean" -- but the second answers it with
    a condition, which is worth saying out loud.
    """

    #: Declared name -> that name plus everything it contains, transitively.
    names: Mapping[str, tuple[str, ...]]
    #: Declared name -> the groups a caller must hold for it to apply, for the
    #: names written with `all_of`. Absent for every ordinary group.
    compounds: Mapping[str, tuple[str, ...]] = field(default_factory=dict)

    def mentions(self, audience: Audience) -> frozenset[str]:
        """Every group name an audience touches, following `contains` and `all_of`.

        What `refuse_dead` compares, and the reason it needs a vocabulary. A
        bare name mentions itself and everything it contains; a compound
        mentions its parts; a conjunction mentions its members. `ALL` mentions
        nothing, and is never asked.

        A queue rather than recursion, so a vocabulary that loops across the two
        kinds of edge cannot spin here. Each kind is separately acyclic by the
        time this runs, but the union of two acyclic graphs need not be.
        """
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
        """Every group a caller effectively holds, following `contains` then `all_of`.

        Refuses a name the vocabulary does not have. That refusal is the reason
        the vocabulary is closed: silently expanding to nothing would turn a
        typo in a caller's group list into a caller who reaches nothing, which
        looks exactly like a caller who was denied.

        Refuses a compound too, and for a sharper reason. A compound is what
        holding its parts *adds up to*, so accepting it as a claim would let a
        caller present the conclusion instead of the premises -- one assertion
        standing in for the two that `all_of` exists to require.

        Order is the whole of the rule: `contains` runs first, then compounds
        are added against whatever is held afterwards. That is what makes an
        `admin` who contains both parts satisfy a compound of them, rather than
        being mysteriously weaker than the sum of what they reach.
        """
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
        """Refuse a definition naming a group this deployment does not declare.

        The other end of the closed vocabulary. Without it a mistyped audience
        invents a group nobody is in, and the only symptom is a tool quietly
        reachable by no one -- found weeks later by whoever needed it.

        Looks inside a conjunction, because a name typed from memory is no
        likelier to be right for having been written next to another one.
        """
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

        An entry audience is already an **and** with the definition's, because
        the only way to reach an entry is through the definition holding it --
        `agent_named` refuses a caller who cannot open the agent, and nothing
        else hands out a spec. So `[senior]` under `[analysts, auditors]` means
        "everyone who opens this agent, and is senior", which is a perfectly
        good second requirement and has always evaluated correctly.

        This was `refuse_dead` and it refused exactly that. The refusal was
        wrong twice over: it blocked the narrowing above, and the thing it meant
        to catch -- `[auditors]` written under `[analysts]` by somebody trying
        to *widen* -- is the same shape, so no rule can tell them apart. What
        survived is the looking. See `AccessReport.narrowed`.

        Judged on what the names mean rather than how they are spelled, which
        is why it needs the vocabulary: `[analysts]` under `[reviewers]` is not
        narrowing at all when `reviewers` contains `analysts`.

        Silent when the definition is `ALL`: everyone reaches it, so nothing an
        entry says can be narrower than nothing.
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
    """The declared groups, what each contains, and what each requires.

    Two spellings, because the common case has neither key and should not have
    to write an empty mapping to say so. A list is the short form; a mapping is
    the long one. Both produce the same thing.
    """
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
            if isinstance(listed, str):
                msg = f"{source}: group {name!r}: {key!r} is a list of group names"
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
    declared, compounds = _vocabulary(document.get("groups"), source)
    _refuse_undeclared_parts(declared, compounds, source)
    _refuse_granted_compounds(declared, compounds, source)
    _refuse_compound_loops(compounds, source)
    return Groups(names=_closed(declared, source), compounds=compounds)


def _refuse_granted_compounds(
    declared: Mapping[str, tuple[str, ...]], compounds: Mapping[str, tuple[str, ...]], source: str
) -> None:
    """Refuse a `contains` that hands out a compound rather than its parts.

    `admin: {contains: [senior-analysts]}` gives an admin the compound while
    they hold neither `analysts` nor `senior` -- the requirement defeated by the
    file that declares it. It is the same move `expand` already refuses from a
    caller, made one level up: presenting the conclusion instead of the
    premises. Refusing it in only one of the two places would have been the
    inconsistency, not the rule.

    Naming the parts instead reaches exactly the same people through the front
    door, and has the property the shortcut lacks -- `kingfisher list` prints
    what a compound requires, so an admin who satisfies one legibly satisfies it
    for a reason a reader can see.
    """
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
    """Refuse a compound built from a name this file never declares.

    The same rule `_closed` applies to `contains`, applied to the other edge.
    Without it a mistyped part is a requirement nobody can ever meet, and the
    only symptom is a group that quietly derives for no one.
    """
    for name, parts in compounds.items():
        for part in parts:
            if part not in declared:
                msg = (
                    f"{source}: group {name!r} requires {part!r}, which is not "
                    f"declared; this file defines {', '.join(sorted(declared))}"
                )
                raise AccessError(msg)


def _refuse_compound_loops(compounds: Mapping[str, tuple[str, ...]], source: str) -> None:
    """Refuse a compound that requires itself, directly or through others.

    Not for termination -- the fixpoint in `expand` is monotone over a finite
    set and would stop either way. For meaning: a loop of requirements can never
    be entered, so every name in it derives for nobody. That is the "reaches no
    one" failure the closed vocabulary exists to prevent, written in the file
    that defines the vocabulary itself.
    """
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

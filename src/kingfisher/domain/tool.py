"""What a tool is, seen from the domain: an object, and where it came from.

Here rather than in `infrastructure.catalogue.tools` because `ToolRepository` is
a port, and a port
in `domain/ports.py` cannot name a type that lives one layer out. Nothing
foreign travels with it: `tool` is `Any` on purpose and `tool_name` is three
`getattr` calls, so the pure layer stays pure under the same rule
`test_domain_imports_only_the_standard_library_and_itself` enforces.

That `Any` is doing real work, and it is worth being honest about rather than
letting the import scan speak for it. What a `Found` holds is, in practice, a
langchain `BaseTool`. The domain never calls one, never imports the type and
never depends on its shape -- it carries the object from the loader that
imported it to the agent that runs it, and asks it for a name it may not have.
If that ever stops being true, the fix is a domain-owned description of a tool,
not a wider import here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.domain.capabilities import (
    ALL,
    SEPARATOR,
    CapabilityError,
    Selection,
    belongs_in,
    narrowed,
    refuse_unoffered,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# `SEPARATOR` is imported above rather than defined here, where it started:
# skills spell a source the same way, and one separator both kinds import
# beats two that agree by coincidence. Re-exported by the import, so
# `domain.tool.SEPARATOR` still resolves for readers who look here first.


def reference(source: str, name: str) -> str:
    """How a definition writes one tool: where it lives, then what it is called.

    The trailing slash a package's `source` carries is dropped. It earns its
    place in a *listing*, where it says `csv_profile` is a folder rather than a
    file that is not there -- but a reference already says that with `.py`, or
    with its absence, and `csv_profile/::csv_columns` is only noisier for it.
    """
    return f"{source.rstrip('/')}{SEPARATOR}{name}"


def split_reference(text: str) -> tuple[str | None, str]:
    """A written reference into the file it claims and the name it means.

    The name is what everything downstream uses -- a grant, an allowlist, the
    dictionary the agent dispatches through -- so it comes back plain whichever
    form was written. The claim comes back beside it, for whoever checks it, and
    is `None` when the short form was used.

    A trailing slash is accepted and dropped. `--list` prints a package as
    `csv_profile/`, and pasting that in should not be a near-miss that someone
    has to notice.
    """
    claimed, found, name = text.rpartition(SEPARATOR)
    if not found:
        return None, text.strip()
    return claimed.strip().rstrip("/") or None, name.strip()


def tool_name(tool: Any) -> str:
    """What a request names this tool by.

    `BaseTool` carries `.name`; a bare callable is named by the function. Both
    are accepted because `create_deep_agent` accepts both, and a definition
    should not have to know which one deepagents prefers this month.
    """
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


def named(tool: Any) -> bool:
    """Whether this is something `tool_name` can name, rather than describe.

    The two halves of `tool_name` above are the two shapes a tool comes in --
    `BaseTool` carries `.name`, a bare callable carries `.__name__` -- and the
    `repr` beyond them is a last resort so that *naming* never raises. A listing
    needs that; a loader must not lean on it. Measured: a workspace writing
    `TOOLS = ["line_count"]` for the name of its tool got one advertised as
    `'line_count'`, quotes and all, and a build that died with `AttributeError:
    'function' object has no attribute 'name'` naming neither the file nor the
    entry.

    The two attributes are langchain's rule as much as this one's: measured
    against `convert_to_openai_tool`, a plain function is named by `__name__`
    and anything carrying neither -- a `functools.partial`, an instance with
    `__call__` -- raises there. So the fallback stays for naming and this says
    when it was reached, which is the point a loader can still name the file.

    A class is named by `__name__` and passes here, which is correct: it is
    refused a rule earlier, with a message about the parentheses it is missing.
    """
    return bool(getattr(tool, "name", None) or getattr(tool, "__name__", None))


@dataclass(frozen=True)
class Found:
    """One tool and the file it came from, relative to the catalogue.

    The pair rather than either alone, because every caller that wants one
    eventually wants the other: the agent needs the object, and anything that
    has to *say* something about a tool -- a listing, a refusal -- needs
    somewhere a reader can go and open.
    """

    tool: Any
    source: str

    @property
    def name(self) -> str:
        return tool_name(self.tool)

    @property
    def reference(self) -> str:
        """How a definition would name this one, saying where it lives."""
        return reference(self.source, self.name)


def offered(sources: Mapping[str, str], names: Sequence[str]) -> str:
    """What a workspace offers, one per line, with where each one lives.

    A block rather than a tuple. The reader is someone who just mistyped a name
    and needs to scan for the one they meant, and a parenthesised tuple of
    fifteen is the shape nobody finishes reading.

    Names with no known source -- a built-in, or a tool handed straight to
    `build_agent` rather than found on disk -- are listed bare. There is no file
    to name, and a blank column against `read_file` would be noise.

    The source is printed the way a definition writes it, without a package's
    trailing slash, so what a reader sees is what they can paste into a `tools:`
    line. It kept the slash once, which said "folder" at the cost of being a
    near-miss for the one thing anybody does with it.
    """
    if not names:
        return "  (none)"
    width = max(len(name) for name in names)
    return "\n".join(
        f"  {name.ljust(width)}  ({where.rstrip('/')})"
        if (where := sources.get(name))
        else f"  {name}"
        for name in sorted(names)
    )



def written_form(one: Found, *, among: Mapping[str, int]) -> str:
    """How a grant names this tool: flat where the name is its own, else the file."""
    return one.reference if among.get(one.name, 0) > 1 else one.name


def _by_name(found: Sequence[Found]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for one in found:
        counted[one.name] = counted.get(one.name, 0) + 1
    return counted


def duplicated(found: Sequence[Found]) -> tuple[str, ...]:
    """Names more than one of these tools answers to, sorted.

    Asked of a *selection* rather than the catalogue, because that is where it
    matters: two `fetch`es only conflict for whoever ends up holding both, and
    a run that took one of them has no conflict at all.
    """
    return tuple(sorted(name for name, count in _by_name(found).items() if count > 1))


def select(granted: Selection, found: Sequence[Found]) -> tuple[Found, ...]:
    """The tools a grant means, as the objects that will be registered.

    This is what makes two `fetch`es possible, and it is why a grant is resolved
    to *objects* here rather than to names downstream. A name picks a tool out
    of a dictionary and a dictionary holds one entry per key -- so handing over
    a whole catalogue and narrowing it afterwards collapses the pair before any
    narrowing runs. Handing over exactly what was granted does not.

    A free function rather than a method, because the caller that needs it most
    has a selection and a catalogue and no `Offering`: `as_subagent` resolves a
    delegate's own tools, and giving delegation a domain object to hold would
    buy nothing it does not already have.
    """
    if granted is None:
        return ()
    among = _by_name(found)
    if granted == ALL:
        return tuple(found)
    wanted = set(granted)
    return tuple(one for one in found if written_form(one, among=among) in wanted)

def _placed(entry: str, offered: set[str]) -> str:
    """One written entry as the offering spells it, or unchanged if it cannot."""
    if entry in offered:
        return entry
    name = split_reference(entry)[1]
    return name if name in offered else entry


@dataclass(frozen=True)
class Offering:
    """What tools exist to be granted, on which axis, and where each is defined.

    Three values that were threaded separately through five functions --
    `builtin`, `workspace`, `sources` -- and are one fact: what this build has
    to offer. Splitting them at every call site is what let the rule below grow
    a second copy in `delegation`, since each caller assembled its own trio and
    then wrote its own check over them.

    Offered names only. The grants are *derived* rather than stored, and the
    cost of the other choice is visible in `_ToolSurface`, which stores both and
    needed an `unrestricted` flag beside them because a stored grant could no
    longer say whether `ALL` meant "everything was granted" or "nothing was
    narrowed". A value derived on demand cannot go stale against what it came
    from.

    It cannot be built when the tools are read, which is worth saying so nobody
    tries: `builtin` comes from an assembled graph, so this is built after the
    probe, from the `Found` pairs the repository returned earlier.
    """

    builtin: tuple[str, ...] = ()
    workspace: tuple[str, ...] = ()
    sources: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, found: Sequence[Found], *, builtin: tuple[str, ...] = ()) -> Offering:
        """From what the repository returned, plus what the graph registered.

        A name two files both define is offered under its *reference* instead,
        and only then. Two folders may each define a `fetch` -- vendors do not
        coordinate -- and one had to lose before this, at catalogue load, which
        stopped the deployment over a clash no single agent would ever see.

        Flat where a name is unique, which is every catalogue that has no
        collision. The redundant half is dropped from `sources`: a reference
        already says which file, so annotating it with the same path again is
        noise in a listing whose whole job is to be scannable.
        """
        among = _by_name(found)
        return cls(
            builtin=builtin,
            workspace=tuple(written_form(one, among=among) for one in found),
            sources={one.name: one.source for one in found if among[one.name] == 1},
        )

    def carried(self, granted: Selection, found: Sequence[Found]) -> tuple[Found, ...]:
        """What the agent itself holds: everything granted, minus the ambiguous.

        The request's grant is what this run may *draw on* -- itself or through
        a delegate -- and those stopped being the same list once two files could
        each define a `fetch`. A delegate names which one it wants; the agent
        holding the grant cannot, because it dispatches by name and would keep
        one of the two in silence.

        So the pair is dropped rather than resolved, and `ambiguous` is what
        says so out loud. Dropping quietly would be the failure this codebase
        refuses everywhere else -- the point is that it is reported, not that it
        is dropped.
        """
        chosen = select(granted, found)
        clashing = set(duplicated(chosen))
        return tuple(one for one in chosen if one.name not in clashing)

    def ambiguous(self, granted: Selection, found: Sequence[Found]) -> tuple[str, ...]:
        """The names `carried` had to leave behind, for a caller that must say so."""
        return duplicated(select(granted, found))


    def spelt(self, selection: Selection) -> Selection:
        """A written grant in the spelling this offering uses.

        Two vocabularies meet here and used to be compared as plain strings. A
        definition writes `csv_profile::csv_profile` or `csv_profile`, both
        legal and documented as meaning the same thing; an offering canonicalises
        to the *bare* name wherever a name is unique, and keeps the reference
        only for a name two files both define. So the long form of a unique tool
        matched nothing, and every comparison downstream is a set membership:
        `refuse_unknown` raised "unknown tool", and `narrowed` -- which has no
        opinion about what exists -- would have dropped it in silence.

        Measured: `surveyor.yaml` and `analysis/profiler.yaml` both ship writing
        the long form, and neither could be run. The short form works, which is
        why a suite full of it stayed green.

        The claim is deliberately not checked here. `refuse_moved` is what says
        a tool has moved, over `tool_sources`, and it says it far better -- with
        both references and an arrow. This only answers "which of the two
        spellings is this", and an entry it cannot place comes back untouched so
        that `refuse_unknown` quotes what the file actually wrote.
        """
        if selection in (ALL, None):
            return selection
        offered = set(self.workspace)
        return tuple(_placed(one, offered) for one in selection)

    def refuse_unknown(
        self, builtin: Selection, tools: Selection, *, subject: str
    ) -> None:
        """A name on the wrong axis, or on neither. Raises, or says nothing.

        One implementation for two callers, which is the whole reason this
        moved. A request and a subagent asked the same question in two places
        and each answered it in its own words -- `delegation` said so outright:
        "`_refuse_unknown_tools` says the same thing to a request, for the same
        reason". `subject` is how one message serves both, the way
        `refuse_ungranted_models` already does it a file away.

        Naming a built-in under `tools` gets its own sentence rather than
        "unknown tool", because the name plainly exists and the other wording
        sends someone hunting for a bug in kingfisher.
        """
        for asked, own, other, here, there in (
            # Spelt before checking, not after: a definition may write either
            # form and only one of them is what the offering holds.
            (self.spelt(tools), self.workspace, self.builtin, "tools", "builtin_tools"),
            (builtin, self.builtin, self.workspace, "builtin_tools", "tools"),
        ):
            if asked in (ALL, None):
                continue
            if misplaced := tuple(n for n in asked if n in set(other)):
                msg = (
                    f"{subject} names {', '.join(misplaced)} in {here}, "
                    f"but {belongs_in(misplaced, field=there)}"
                )
                raise CapabilityError(msg)
            refuse_unoffered(
                asked,
                offered=own,
                kind=here[:-1],
                subject=subject,
                listing=f"\n{offered(self.sources, own)}",
            )

    def refuse_moved(self, claims: Mapping[str, str], *, subject: str) -> None:
        """A definition that said where a tool lives, about one that has moved.

        Here rather than beside the definition it came from, because what it
        needs is `sources` -- and taking a `SubagentSpec` would have `tool`
        import `subagent` while `subagent` imports this. It takes the claims
        alone, and `subject` names the reader's file the way `refuse_unknown`
        does.

        Only entries that made a claim are checked, so a definition written the
        short way is untouched. The claim can only ever be wrong about
        *location*: two tools of one name never both load, so this says "it
        moved" and never "you meant the other one".

        A name this workspace does not offer at all is left alone --
        `refuse_unknown` says that better, with the full listing, and saying it
        twice in two voices helps nobody.
        """
        moved = [
            (name, claimed, self.sources[name].rstrip("/"))
            for name, claimed in claims.items()
            if name in self.sources and self.sources[name].rstrip("/") != claimed
        ]
        if not moved:
            return
        lines = "\n".join(
            f"  {reference(claimed, name)}  ->  {reference(actual, name)}"
            for name, claimed, actual in sorted(moved)
        )
        msg = (
            f"{subject} says where its tools live, and "
            f"{'one has' if len(moved) == 1 else 'some have'} moved:\n{lines}\n"
            f"Update the definition, or drop the path and write the name alone."
        )
        raise CapabilityError(msg)

    def permitted(self, builtin: Selection, tools: Selection) -> tuple[str, ...] | None:
        """Every tool name a request may call, or `None` for no restriction.

        Two axes, one allowlist. The middleware filters a single flat list by
        name, so the grants meet here as a union -- but they are resolved apart,
        each against its own offered set, which is the whole point of the split:
        `tools=("http_fetch",)` no longer costs a caller `read_file`.

        `None` means neither axis was narrowed, so no allowlist is added at all.
        Not the same as an empty one, and the difference is what `ToolAllowlist`
        would enforce.
        """
        if builtin == ALL and tools == ALL:
            return None
        # Spelt here too, and this is the one that was missed first time: the
        # grant resolved correctly while the *allowlist* was still built from
        # the written form, so the tool was registered and then refused at the
        # moment it was called. Measured, not read -- the definition loaded, the
        # run started, and `csv_profile` was simply absent from "Available
        # tools" when the model reached for it.
        tools = self.spelt(tools)
        # The workspace half comes back to bare names, because that is what the
        # middleware compares against: it filters by `tool.name`, and a tool's
        # name is `fetch` however a grant spelled it. Safe to flatten precisely
        # because `refuse_ambiguous` ran first -- within one agent the names are
        # unique, so two spellings can never land on one entry here.
        granted = narrowed(tools, by=self.workspace) or ()
        return (
            *(narrowed(builtin, by=self.builtin) or ()),
            *(split_reference(one)[1] for one in granted),
        )


def ceiling(
    asked_builtin: Selection,
    asked_tools: Selection,
    *,
    granted_builtin: Selection,
    granted_tools: Selection,
    subject: str,
) -> Selection:
    """Every tool a delegate may call, from the two lists it may narrow.

    Not a method on `Offering`, and the reason is worth stating because putting
    it there is the obvious move and it is wrong: a delegate is narrowed by what
    the *request was granted*, not by what the workspace offers. Those differ
    exactly when a request narrowed something, which is the case this exists
    for. An `Offering.ceiling` would silently widen a delegate back to the
    workspace.

    Answers `ALL` for "narrowed by nobody" where `Offering.permitted` answers
    `None`. Two consumers, two conventions: a delegate's selection is narrowed
    again downstream, a request's is handed to a middleware. Folding them would
    make one of the two lie.
    """
    from_builtin = narrowed(asked_builtin, by=granted_builtin)
    from_workspace = narrowed(asked_tools, by=granted_tools)
    if from_builtin == ALL and from_workspace == ALL:
        return ALL
    if ALL in (from_builtin, from_workspace):
        # Quiet if unguarded: `ALL` is the string `"*"`, so unpacking it into
        # the union contributes a tool *named* `*` and drops the axis it stood
        # for. An allowlist is one flat set of names and cannot say "all of
        # those, plus these".
        msg = (
            f"{subject}: one tool axis resolved to {ALL!r} while the other named "
            f"tools ({from_builtin!r} / {from_workspace!r}). Resolve both against "
            f"what is offered before calling this, or neither"
        )
        raise ValueError(msg)
    return (*(from_builtin or ()), *(from_workspace or ()))


def claimed_sources(written: Selection) -> Mapping[str, str]:
    """Where each entry said its tool lives, for the entries that said.

    Keyed by name rather than kept as a list, because that is how it is asked:
    the checker holds the real sources by name and wants to know what this
    definition claimed for that one. Entries written the short way are absent,
    which is how "made no claim" is told from "claimed and was right".
    """
    if written in (ALL, None):
        return MappingProxyType({})
    claimed = {}
    for entry in written:
        where, name = split_reference(entry)
        if where is not None:
            claimed[name] = where
    return MappingProxyType(claimed)

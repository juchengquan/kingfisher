"""What a tool is, seen from the domain: an object, and where it came from."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.domain.access import jointly, reaches
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
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from kingfisher.domain.access import Audience

# `SEPARATOR` is imported above rather than defined here, where it started:
# skills spell a source the same way, and one separator both kinds import
# beats two that agree by coincidence. Re-exported by the import, so
# `tools.spec.SEPARATOR` still resolves for readers who look here first.


def reference(source: str, name: str) -> str:
    """How a definition writes one tool: where it lives, then what it is called."""
    return f"{source.rstrip('/')}{SEPARATOR}{name}"


def split_reference(text: str) -> tuple[str | None, str]:
    """A written reference into the file it claims and the name it means."""
    claimed, found, name = text.rpartition(SEPARATOR)
    if not found:
        return None, text.strip()
    return claimed.strip().rstrip("/") or None, name.strip()


def tool_name(tool: Any) -> str:
    """What a request names this tool by."""
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


def named(tool: Any) -> bool:
    """Whether this is something `tool_name` can name, rather than describe.

    The two halves of `tool_name` above are the two shapes a tool comes in --
    `BaseTool` carries `.name`, a bare callable carries `.__name__` -- and the `repr`
    beyond them is a last resort so that *naming* never raises. A listing needs that;
    a loader must not lean on it. Measured: a workspace writing `TOOLS =
    ["line_count"]` for the name of its tool got one advertised as `'line_count'`,
    quotes and all, and a build that died with `AttributeError: 'function' object has
    no attribute 'name'` naming neither the file nor the entry.

    The two attributes are langchain's rule as much as this one's: measured against
    `convert_to_openai_tool`, a plain function is named by `__name__` and anything
    carrying neither -- a `functools.partial`, an instance with `__call__` -- raises
    there. So the fallback stays for naming and this says when it was reached, which
    is the point a loader can still name the file.
    """
    return bool(getattr(tool, "name", None) or getattr(tool, "__name__", None))


#: Where `kingfisher.tool` keeps who may use a tool. Read by name, because the class that
#: defines it subclasses a langchain tool and this package may not import langchain.
AUDIENCE_ATTRIBUTE = "kingfisher_source_ids"


def own_audience(tool: Any) -> Audience:
    """Who this tool admits by itself: `"*"` for every tool `kingfisher.tool` did not make."""
    return getattr(tool, AUDIENCE_ATTRIBUTE, ALL)


@dataclass(frozen=True)
class Found:
    """One tool and the file it came from, relative to the catalogue."""

    tool: Any
    source: str
    #: Who the tool admits by itself, whichever definition grants it -- an **and** on
    #: every grant, so an agent's entry can narrow it and never widen it.
    source_ids: Audience = ALL

    @property
    def name(self) -> str:
        return tool_name(self.tool)

    @property
    def reference(self) -> str:
        """How a definition would name this one, saying where it lives."""
        return reference(self.source, self.name)


def offered(sources: Mapping[str, str], names: Sequence[str]) -> str:
    """What a workspace offers, one per line, with where each one lives."""
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
    """Names more than one of these tools answers to, sorted."""
    return tuple(sorted(name for name, count in _by_name(found).items() if count > 1))


def out_of_reach(found: Sequence[Found], held: frozenset[str] | None) -> frozenset[str]:
    """The tools whose own audience shuts this caller out, as a grant would write them."""
    if held is None:
        return frozenset()
    among = _by_name(found)
    return frozenset(
        written_form(one, among=among) for one in found if not reaches(one.source_ids, held)
    )


def admitted(granted: Callable[[str], bool], *, shut: frozenset[str]) -> Callable[[str], bool]:
    """A grant's answer about one tool, less the tools `out_of_reach` names."""
    return lambda name: granted(name) and name not in shut


def own_audiences(found: Iterable[Found]) -> dict[str, Audience]:
    """Each tool's own audience by the name a call dispatches on, where it states one.

    Two tools answering to one name are joined the strict way, because whoever reads
    this sees the name alone and cannot tell which of the two ran.
    """
    own: dict[str, Audience] = {}
    for one in found:
        if one.source_ids != ALL:
            own[one.name] = jointly((own.get(one.name, ALL), one.source_ids))
    return own


def select(granted: Selection, found: Sequence[Found]) -> tuple[Found, ...]:
    """The tools a grant means, as the objects that will be registered."""
    if granted is None:
        return ()
    among = _by_name(found)
    if granted == ALL:
        return tuple(found)
    wanted = set(granted)
    return tuple(one for one in found if written_form(one, among=among) in wanted)


def placed(entry: str, offered: Iterable[str]) -> str:
    """One written entry as the offering spells it, or unchanged if it cannot.

    Public because two readers compare a written grant against the catalogue's own
    names: `spelt` below, over a whole selection on its way into a build, and `offers`
    in the domain, one name at a time. `offers` compared them unspelt, so the long form
    `formats.md` documents -- `csv_profile::csv_profile`, which the shipped `surveyor`
    is written with -- matched nothing, and a scoped caller was told they reached no
    tools at all while the build handed them one.

    A name two sources offer is spelt long by the offering itself, so a written short
    form stays short and matches neither: ambiguous is not a thing a caller is told
    they hold.
    """
    kept = set(offered)
    if entry in kept:
        return entry
    name = split_reference(entry)[1]
    return name if name in kept else entry


@dataclass(frozen=True)
class Offering:
    """What tools exist to be granted, on which axis, and where each is defined."""

    builtin: tuple[str, ...] = ()
    workspace: tuple[str, ...] = ()
    sources: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, found: Sequence[Found], *, builtin: tuple[str, ...] = ()) -> Offering:
        """From what the repository returned, plus what the graph registered."""
        among = _by_name(found)
        return cls(
            builtin=builtin,
            workspace=tuple(written_form(one, among=among) for one in found),
            sources={one.name: one.source for one in found if among[one.name] == 1},
        )

    def carried(self, granted: Selection, found: Sequence[Found]) -> tuple[Found, ...]:
        """What the agent itself holds: everything granted, minus the ambiguous."""
        chosen = select(granted, found)
        clashing = set(duplicated(chosen))
        return tuple(one for one in chosen if one.name not in clashing)

    def ambiguous(self, granted: Selection, found: Sequence[Found]) -> tuple[str, ...]:
        """The names `carried` had to leave behind, for a caller that must say so."""
        return duplicated(select(granted, found))

    def spelt(self, selection: Selection) -> Selection:
        """A written grant in the spelling this offering uses.

        Measured: `surveyor.yaml` and `analysis/profiler.yaml` both ship writing the
        long form, and neither could be run. The short form works, which is why a
        suite full of it stayed green.
        """
        if selection in (ALL, None):
            return selection
        offered = set(self.workspace)
        return tuple(placed(one, offered) for one in selection)

    def refuse_unknown(
        self,
        builtin: Selection,
        tools: Selection,
        *,
        subject: str,
        in_reach: Callable[[str], bool] | None = None,
    ) -> None:
        """A name on the wrong axis, or on neither. Raises, or says nothing.

        `in_reach` narrows what the refusal *lists*, never what it judges: a file
        naming a tool nothing offers is the file's fault whoever is asking, and a name
        out of one caller's reach is still a real name. Unnarrowed, the listing handed
        a caller holding one source id every workspace tool and its file -- measured,
        three names and three paths where the agent they reach granted one. Only the
        workspace axis takes it; a built-in is the same set for everybody.
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
            shown = own
            if in_reach is not None and here == "tools":
                shown = tuple(name for name in own if in_reach(name))
            refuse_unoffered(
                asked,
                offered=own,
                kind=here[:-1],
                subject=subject,
                listing=f"\n{offered(self.sources, shown)}",
            )

    def moved(self, claims: Mapping[str, str]) -> tuple[tuple[str, str, str], ...]:
        """Each `(name, where the definition says it is, where it is)` that disagrees.

        Asked as well as refused, because `doctor` reports what startup refuses and a
        second walk over the same claims is a second chance to answer differently.
        """
        return tuple(
            sorted(
                (name, claimed, self.sources[name].rstrip("/"))
                for name, claimed in claims.items()
                if name in self.sources and self.sources[name].rstrip("/") != claimed
            )
        )

    def refuse_moved(self, claims: Mapping[str, str], *, subject: str) -> None:
        """A definition that said where a tool lives, about one that has moved."""
        moved = self.moved(claims)
        if not moved:
            return
        lines = "\n".join(
            f"  {reference(claimed, name)}  ->  {reference(actual, name)}"
            for name, claimed, actual in moved
        )
        msg = (
            f"{subject} says where its tools live, and "
            f"{'one has' if len(moved) == 1 else 'some have'} moved:\n{lines}\n"
            f"Update the definition, or drop the path and write the name alone."
        )
        raise CapabilityError(msg)

    def permitted(self, builtin: Selection, tools: Selection) -> tuple[str, ...] | None:
        """Every tool name a request may call, or `None` for no restriction."""
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
        # middleware compares against: it filters by `tool.name`, and a tool's name is
        # `fetch` however a grant spelled it. Safe to flatten because `carried` has
        # already dropped every clashing pair from what the agent holds, so within one
        # agent the names are unique and two spellings can never land on one entry here.
        granted = narrowed(tools, by=self.workspace) or ()
        return (
            *(narrowed(builtin, by=self.builtin) or ()),
            *(split_reference(one)[1] for one in granted),
        )


def claimed_sources(written: Selection) -> Mapping[str, str]:
    """Where each entry said its tool lives, for the entries that said."""
    if written in (ALL, None):
        return MappingProxyType({})
    claimed = {}
    for entry in written:
        where, name = split_reference(entry)
        if where is not None:
            claimed[name] = where
    return MappingProxyType(claimed)

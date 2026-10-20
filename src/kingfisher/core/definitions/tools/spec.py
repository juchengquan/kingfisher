"""What a tool is, as a definition: what its file wrote, and what the loader found."""

from __future__ import annotations

from dataclasses import KW_ONLY, dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import (
    ALL,
    SEPARATOR,
    CapabilityError,
    Selection,
    belongs_in,
    jointly,
    narrowed,
    reaches,
    refuse_unoffered,
    zoned,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from kingfisher.core.access import Audience


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
    """Whether `tool_name` can name this, rather than fall back to its `repr`.

    The fallback is so a listing never raises; a loader must not lean on it. The two
    attributes are langchain's rule too: `convert_to_openai_tool` raises on anything with
    neither, such as a `functools.partial` or an instance with `__call__`.
    """
    return bool(getattr(tool, "name", None) or getattr(tool, "__name__", None))


@dataclass(frozen=True)
class ToolSpec:
    """One entry of a tool file's `TOOLS`, saying what the tool alone cannot.

    The tool is any form `TOOLS` holds bare. What kingfisher reads is kept here and never
    put on the tool, whose `metadata` LangChain hands to every callback.
    """

    tool: Any
    _: KW_ONLY
    name: str | None = None
    description: str | None = None
    #: Written as a definition's own `source_ids:` line, and read by the same reader.
    source_ids: Sequence[str | AbstractSet[str]] = (ALL,)
    datazone: str | None = None


@dataclass(frozen=True)
class FoundTool:
    """One tool and the file it came from, relative to the catalogue."""

    tool: Any
    source: str
    #: Who the tool admits, whichever definition grants it: an **and** on every grant, and
    #: the only lock the tool has, since a `tools:` entry takes none.
    source_ids: Audience = ALL
    #: The one datazone a caller must be in to use it, or `None` for any.
    datazone: str | None = None

    @property
    def audience(self) -> Audience:
        """Who the tool admits by itself: its source ids, and its datazone as well."""
        return zoned(self.source_ids, self.datazone)

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


def written_form(one: FoundTool, *, among: Mapping[str, int]) -> str:
    """How a grant names this tool: flat where the name is its own, else the file."""
    return one.reference if among.get(one.name, 0) > 1 else one.name


def _by_name(found: Sequence[FoundTool]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for one in found:
        counted[one.name] = counted.get(one.name, 0) + 1
    return counted


def duplicated(found: Sequence[FoundTool]) -> tuple[str, ...]:
    """Names more than one of these tools answers to, sorted."""
    return tuple(sorted(name for name, count in _by_name(found).items() if count > 1))


def out_of_reach(found: Sequence[FoundTool], held: frozenset[str] | None) -> frozenset[str]:
    """The tools whose own audience shuts this caller out, as a grant would write them."""
    if held is None:
        return frozenset()
    among = _by_name(found)
    return frozenset(
        written_form(one, among=among) for one in found if not reaches(one.audience, held)
    )


def audienced(found: Sequence[FoundTool]) -> dict[str, FoundTool]:
    """The tools that say who they admit, as a grant would write them."""
    among = _by_name(found)
    return {written_form(one, among=among): one for one in found if one.audience != ALL}


def admitted(granted: Callable[[str], bool], *, shut: frozenset[str]) -> Callable[[str], bool]:
    """A grant's answer about one tool, less the tools `out_of_reach` names."""
    return lambda name: granted(name) and name not in shut


def own_audiences(found: Iterable[FoundTool]) -> dict[str, Audience]:
    """Each tool's own audience by the name a call dispatches on, where it states one.

    Two tools answering to one name are joined the strict way, because whoever reads
    this sees the name alone and cannot tell which of the two ran.
    """
    own: dict[str, Audience] = {}
    for one in found:
        if one.audience != ALL:
            own[one.name] = jointly((own.get(one.name, ALL), one.audience))
    return own


def select(granted: Selection, found: Sequence[FoundTool]) -> tuple[FoundTool, ...]:
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

    Public so `offers` compares a grant spelt as the build spells it. A name two sources
    offer is spelt long, so a short form matches neither: ambiguous is not held.
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
    offered: tuple[str, ...] = ()
    sources: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, found: Sequence[FoundTool], *, builtin: tuple[str, ...] = ()) -> Offering:
        """From what the repository returned, plus what the graph registered."""
        among = _by_name(found)
        return cls(
            builtin=builtin,
            offered=tuple(written_form(one, among=among) for one in found),
            sources={one.name: one.source for one in found if among[one.name] == 1},
        )

    def carried(self, granted: Selection, found: Sequence[FoundTool]) -> tuple[FoundTool, ...]:
        """What the agent itself holds: everything granted, minus the ambiguous."""
        chosen = select(granted, found)
        clashing = set(duplicated(chosen))
        return tuple(one for one in chosen if one.name not in clashing)

    def ambiguous(self, granted: Selection, found: Sequence[FoundTool]) -> tuple[str, ...]:
        """The names `carried` had to leave behind, for a caller that must say so."""
        return duplicated(select(granted, found))

    def spelt(self, selection: Selection) -> Selection:
        """A written grant in the spelling this offering uses."""
        if selection in (ALL, None):
            return selection
        offered = set(self.offered)
        return tuple(placed(one, offered) for one in selection)

    def refuse_unknown(
        self,
        builtin: Selection,
        tools: Selection,
        *,
        subject: str,
        in_reach: Callable[[str], bool] | None = None,
    ) -> None:
        """Refuse a name on the wrong axis, or on neither.

        `in_reach` narrows what the refusal *lists*, never what it judges: a tool nothing
        offers is the file's fault whoever asks. Built-ins are the same for everybody.
        """
        for asked, own, other, here, there in (
            # Spelt first: a definition may write either form, and the offering holds one.
            (self.spelt(tools), self.offered, self.builtin, "tools", "builtin_tools"),
            (builtin, self.builtin, self.offered, "builtin_tools", "tools"),
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

        Asked as well as refused, so `doctor` reports what startup refuses from one walk.
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
        # Spelt here too, or a long-form grant is registered and then refused when called.
        tools = self.spelt(tools)
        # Bare names, which the allowlist compares with `tool.name`. Safe because `carried`
        # has dropped every clashing pair, so within one agent two spellings cannot meet.
        granted = narrowed(tools, by=self.offered) or ()
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

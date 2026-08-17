"""What a tool is, seen from the domain: an object, and where it came from.

Here rather than in `tool_store` because `ToolRepository` is a port, and a port
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
from typing import TYPE_CHECKING, Any

from kingfisher.domain.capabilities import (
    ALL,
    CapabilityError,
    Selection,
    belongs_in,
    narrowed,
    refuse_unoffered,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def tool_name(tool: Any) -> str:
    """What a request names this tool by.

    `BaseTool` carries `.name`; a bare callable is named by the function. Both
    are accepted because `create_deep_agent` accepts both, and a definition
    should not have to know which one deepagents prefers this month.
    """
    return getattr(tool, "name", None) or getattr(tool, "__name__", None) or repr(tool)


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


def offered(sources: Mapping[str, str], names: Sequence[str]) -> str:
    """What a workspace offers, one per line, with where each one lives.

    A block rather than a tuple. The reader is someone who just mistyped a name
    and needs to scan for the one they meant, and a parenthesised tuple of
    fifteen is the shape nobody finishes reading.

    Names with no known source -- a built-in, or a tool handed straight to
    `build_agent` rather than found on disk -- are listed bare. There is no file
    to name, and a blank column against `read_file` would be noise.
    """
    if not names:
        return "  (none)"
    width = max(len(name) for name in names)
    return "\n".join(
        f"  {name.ljust(width)}  ({where})" if (where := sources.get(name)) else f"  {name}"
        for name in sorted(names)
    )


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
        """From what the repository returned, plus what the graph registered."""
        return cls(
            builtin=builtin,
            workspace=tuple(one.name for one in found),
            sources={one.name: one.source for one in found},
        )

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
            (tools, self.workspace, self.builtin, "tools", "builtin_tools"),
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
        return (
            *(narrowed(builtin, by=self.builtin) or ()),
            *(narrowed(tools, by=self.workspace) or ()),
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

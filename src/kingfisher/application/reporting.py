"""What a run tells the caller it did not have.

Three reports and the event list that carries them, kept together because they
answer one question -- *what did this turn not get, and why* -- and apart from
the service because none of them touches it. Every input is decided by the time
they run, so each can be checked on its own.

The rule they share is stated once here and applied three times below: a caller
is told what their *grant* left out, never what their *groups* denied them. The
second would hand somebody the exact list of what they cannot reach, which is
what filtering assets out of listings and refusals exists to prevent. An asset
out of reach was never offered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.domain.access import AUDIENCED
from kingfisher.domain.capabilities import Capabilities, Selection, withheld
from kingfisher.domain.result import RunEvent
from kingfisher.infrastructure.harness.agent import (
    available_skills,
    defined_subagents,
    registered_tools,
    workspace_tool_names,
)

if TYPE_CHECKING:
    from pathlib import Path

    from kingfisher.config import Config
    from kingfisher.domain.agent import AgentSpec
    from kingfisher.infrastructure.catalogue import Definitions


def _named(selection: Selection) -> set[str]:
    """A selection as a set of names, with the two ends read as empty.

    `ALL` and `None` name nothing that can be *lost*: one is everything and the
    other is nothing, and neither changes under group narrowing -- so the
    difference this feeds is empty either way, which is the right answer.
    """
    return set(selection) if isinstance(selection, tuple) else set()


def withheld_by_kind(  # noqa: PLR0913 -- five of these are the five places
    # "what the workspace offers" comes from, and none is derivable from
    # another: the grant, the config, the session, the built graph and the
    # catalogue. The last two are who is asking. Folding any of them into a
    # parameter object would hide which source a kind is measured against,
    # which is the one thing a reader of this function needs to see.
    allowed: Capabilities,
    cfg: Config,
    session_dir: Path,
    graph: Any,
    catalogue: Definitions,
    *,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What this request left out, per kind, skipping the kinds it left nothing.

    **`middleware` is deliberately not among them**, and the reason is about
    this report rather than about that axis. What goes here is what a caller
    could have asked for differently -- a tool, a skill, a delegate they may
    grant next time. A caller cannot register a middleware: the names come from
    whatever constructed `Kingfisher`, so telling them one was withheld names
    something they have no way to act on.

    It is the one axis where a shortfall can pass unremarked --
    `approved_middleware` raises for a name a request withheld, but a
    definition that wrote `middleware: ["*"]` resolves quietly smaller, and
    quietly to nothing. That is the trade this absence accepts, written here
    because this is where the next reader will come looking for it.

    Three axes, one rule. Each differs only in where "what the workspace offers"
    comes from, and none of the three is knowable without asking the thing that
    assembled the agent -- which is why a grant goes stale in the first place.

    The catalogue is passed rather than re-derived, so what a caller is told it
    did not grant is measured against the same directories the agent was built
    from.

    Each "what the workspace offers" is a thunk, not a value, and that is a cost
    rather than a style: three of the four walk a directory, and an axis left at
    its default is skipped a line later without ever needing the answer. Written
    eagerly, the subagent walk happened on every turn of every run -- which
    stayed invisible only while that axis defaulted to none and this function
    was the sole reader.
    """
    default = Capabilities()
    workspace = tuple(workspace_tool_names(cfg, catalogue=catalogue))

    # What this agent would have held for *someone*, less what it holds for
    # this caller: exactly the names group narrowing took away, per field.
    #
    # Computed rather than asked of the policy, because there is no policy to
    # ask any more -- an audience is a property of the definition, so the only
    # thing that knows what a caller lost is the definition resolved twice.
    denied: dict[str, frozenset[str]] = {}
    if agent is not None and held is not None:
        everyone, theirs = agent.declares(None), agent.declares(held)
        denied = {
            name: frozenset(_named(getattr(everyone, name)) - _named(getattr(theirs, name)))
            for name in AUDIENCED
        }

    def visible(kind: str | None, names: tuple[str, ...]) -> tuple[str, ...]:
        """`names`, less what this caller's groups took away.

        Applied to what the workspace *offers*, before the comparison, and the
        ordering is the whole of it. This function names every offered thing a
        grant left out -- so measured against the unfiltered catalogue it would
        hand a caller the exact list of what their groups denied them, which is
        precisely what filtering them out of listings and refusals exists to
        avoid. An asset out of reach is not withheld from this caller; as far
        as they are concerned it was never offered.

        Only what *group narrowing* removed, and that precision matters. A tool
        the agent simply never declared is still reported, because that is a
        fact about the agent rather than about who is calling -- and it is what
        this report has always said.

        `kind` is `None` for the axes no audience controls, which are
        unfiltered and keep reporting exactly what they did.
        """
        if kind is None or not (lost := denied.get(kind)):
            return names
        return tuple(name for name in names if name not in lost)

    offered = (
        # Built-ins and workspace tools are granted apart, so they are reported
        # apart: "3 tool(s) not granted" meant nothing when it could have been
        # either kind.
        # `or ()` for the unreadable case, which cannot happen to a graph
        # `build_agent` made -- and if it ever does, a run report listing no
        # built-ins is a better outcome than a turn that will not start.
        # `test_a_real_build_is_readable` is what notices instead.
        ("builtin tool", "builtin_tools", None, lambda: tuple(
            n for n in registered_tools(graph) or () if n not in set(workspace)
        )),
        ("tool", "tools", "tools", lambda: workspace),
        ("skill", "skills", None, lambda: available_skills(cfg, session_dir, catalogue=catalogue)),
        (
            "subagent",
            "subagents",
            "subagents",
            lambda: tuple(defined_subagents(cfg, session_dir, catalogue=catalogue)),
        ),
    )
    found = []
    for what, field, kind, names_of in offered:
        granted = getattr(allowed, field)
        # Silent when the request left an axis alone. `subagents` defaults to
        # none, so reporting every axis at its default would put a line about
        # undeclared delegates on every run -- which is the noise this event
        # exists to avoid being.
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=visible(kind, tuple(names_of()))):
            found.append((what, left_out))
    return tuple(found)


def delegate_only(allowed: Capabilities, cfg: Config, *, catalogue: Any) -> tuple[str, ...]:
    """Names this run was granted that only a delegate can actually ask for.

    Computed from the catalogue rather than threaded out of `build_agent`,
    because the graph has already dropped them by the time it exists -- which is
    exactly why it has to be said from somewhere that still knows.
    """
    from kingfisher.domain.tool import Offering  # noqa: PLC0415
    from kingfisher.infrastructure.catalogue import Definitions  # noqa: PLC0415

    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return Offering.of(found).ambiguous(allowed.tools, found)


def opening_events(  # noqa: PLR0913, PLR0917 -- one parameter per warning
    # kind, and folding them into a bag would only move the list somewhere
    # a reader has to go and find it.
    turn_dir: str,
    unprotected: tuple[str, ...],
    placement: Any,
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = (),
    indistinct: tuple[tuple[str, str], ...] = (),
    delegate_only: tuple[str, ...] = (),
) -> tuple[RunEvent, ...]:
    """What the caller is told before the model is reached.

    A function because it is one: nothing here touches the service, and every
    input is already decided by the time it runs. `_prepare` was 123 lines and
    this was the part of it that could be checked on its own.
    """
    events: list[RunEvent] = []
    if unprotected:
        events.append(RunEvent(kind="protect_failed", text="; ".join(unprotected)))
    # A grant is a whitelist, so it means less than the workspace holds and says
    # so nowhere. Told here rather than discovered when the model reaches for
    # one and is refused halfway through a turn. One line per kind, because a
    # single line naming three kinds is the one nobody finishes reading.
    for what, names in withheld:
        events.append(
            RunEvent(
                kind="withheld",
                text=f"{len(names)} {what}(s) not granted: {', '.join(names)}",
            )
        )
    # Granted, and still not in the agent's own hands. Two files may each define
    # a `fetch`, and an agent dispatches by name -- so the pair goes to whichever
    # delegate names one, and the agent holding the grant gets neither.
    #
    # Said out loud because the alternative is the failure this codebase refuses
    # everywhere: quietly holding less than was asked for. It is deliberately
    # *not* folded into `withheld`, which means "you did not ask for this" --
    # here the caller did ask, and the answer is "name which one, in a delegate".
    if delegate_only:
        events.append(
            RunEvent(
                kind="delegate_only",
                text=(
                    f"{len(delegate_only)} tool name(s) more than one file defines, "
                    f"so this agent holds none of them -- a subagent that names one "
                    f"gets it: {', '.join(delegate_only)}"
                ),
            )
        )
    # A delegate that meant to run elsewhere and did not. Said here because
    # nothing later will: it builds, it answers, and an answer from the model
    # it was supposed to be checking looks exactly like a good one.
    for name, why in indistinct:
        events.append(RunEvent(kind="indistinct", text=f"{name} {why}", agent=name))
    if placement.placed:
        # Replacement is the one dangerous case -- durable data, silently
        # overwritten -- so it is named rather than assumed.
        replaced = f" ({len(placement.replaced)} replaced)" if placement.replaced else ""
        events.append(
            RunEvent(kind="data_placed", text=f"{', '.join(placement.placed)}{replaced}")
        )
    events.append(RunEvent(kind="run_start", text=turn_dir))
    return tuple(events)

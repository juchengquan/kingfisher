"""What a run tells the caller it did not have."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.access.capabilities import Capabilities, withheld
from kingfisher.core.access.reach import handed_work_by, offers
from kingfisher.core.agent.activation import (
    activatable_skills,
    available_skills,
    defined_subagents,
)
from kingfisher.core.agent.kinds.tools import (
    registered_tools,
    workspace_tool_names,
)
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.tools.spec import Offering, admitted, out_of_reach, placed
from kingfisher.core.result import RunEvent

if TYPE_CHECKING:
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.agents.spec import AgentSpec


def withheld_by_kind(  # noqa: PLR0913 -- four independent sources of what is offered
    # (grant, config, graph, catalogue); a bag would hide which one a kind is measured by.
    allowed: Capabilities,
    cfg: Config,
    graph: Any,
    catalogue: Definitions,
    *,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What this request left out, per kind, skipping the kinds it left nothing.

    Not `middlewares`: a caller cannot register one, so naming one withheld names
    something they cannot act on. The catalogue is passed rather than re-derived, so
    this is measured against the directories the agent was built from.
    """
    default = Capabilities()
    workspace = tuple(workspace_tool_names(cfg, catalogue=catalogue))
    # A skill may be written bare or as `source::name`, and the agent file and registry
    # may differ: compared as written, an audience `catalogue::audit` hides no `audit`.
    identity = activatable_skills(cfg, catalogue=catalogue).identity

    def subagents() -> tuple[str, ...]:
        """Every subagent this run could have been handed work by, for this caller.

        Its `source_ids:` must admit them and the agent this run opened must name it:
        asking only the first would report a subagent nothing activates as withheld, which
        says it exists. The listing asks `handed_work_by` too.
        """
        defined = defined_subagents(cfg, catalogue=catalogue)
        # No branch on `held`: unscoped, `handed_work_by` answers every subagent. A scoped
        # run with no spec (an injected graph) reaches none; kingfisher did not wire them.
        return handed_work_by([agent] if agent is not None else [], defined, held)

    # The agent this run opened, not the subagents it reaches: a subagent only narrows
    # what reached it, so one declaring an ungranted tool names one reached by no path.
    reached_agents = [] if agent is None else [agent]
    # Spelt on tools as on skills: a grant may write the long form a catalogue spells short.
    spelt = set(workspace)
    offers_tool = admitted(
        offers("tools", held, reached_agents, same=lambda one: placed(one, spelt)),
        shut=out_of_reach(catalogue.tools.found, held),
    )
    offers_skill = offers("skills", held, reached_agents, same=identity)

    offered = (
        # Built-ins and workspace tools are granted apart, so reported apart. `or ()`: a
        # graph that cannot be read lists no built-ins rather than failing the turn.
        ("builtin tool", "builtin_tools", lambda: tuple(
            n for n in registered_tools(graph) or () if n not in set(workspace)
        )),
        # Measured against what a definition in reach offers, not the whole catalogue:
        # else a caller who narrowed their grant is told every name nothing in reach offers.
        ("tool", "tools", lambda: tuple(n for n in workspace if offers_tool(n))),
        # `cfg` as well as the request: with skills switched off the agent is told of
        # none, and a report of one "not granted" says the others were.
        (
            "skill",
            "skills",
            lambda: tuple(
                name
                for name in (
                    available_skills(cfg, catalogue=catalogue) if cfg.skills_enabled else ()
                )
                if offers_skill(name)
            ),
        ),
        ("subagent", "subagents", subagents),
    )
    found = []
    for what, field, names_of in offered:
        granted = getattr(allowed, field)
        # Skips the work, not the answer: every axis defaults to `ALL`, where nothing is
        # withheld, and `names_of()` reads the catalogue.
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=tuple(names_of())):
            found.append((what, left_out))
    return tuple(found)


def subagent_only(allowed: Capabilities, cfg: Config, *, catalogue: Any) -> tuple[str, ...]:
    """Names this run was granted that only a subagent can actually ask for."""
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return Offering.of(found).ambiguous(allowed.tools, found)


def opening_events(  # noqa: PLR0913, PLR0917 -- one parameter per warning kind
    turn_id: str,
    unprotected: tuple[str, ...],
    placement: Any,
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = (),
    indistinct: tuple[tuple[str, str], ...] = (),
    subagent_only: tuple[str, ...] = (),
) -> tuple[RunEvent, ...]:
    """What the caller is told before the model is reached."""
    events: list[RunEvent] = []
    if unprotected:
        events.append(RunEvent(kind="protect_failed", text="; ".join(unprotected)))
    # A grant means less than the workspace holds and says so nowhere: told here, not
    # halfway through a turn. One line per kind, as one line naming three goes unread.
    for what, names in withheld:
        events.append(
            RunEvent(
                kind="withheld",
                text=f"{len(names)} {what}(s) not granted: {', '.join(names)}",
            )
        )
    # Granted and still not the agent's: two files may each define `fetch` and dispatch is
    # by name, so a subagent naming one gets it and the agent holding the grant neither.
    if subagent_only:
        events.append(
            RunEvent(
                kind="subagent_only",
                text=(
                    f"{len(subagent_only)} tool name(s) more than one file defines, "
                    f"so this agent holds none of them -- a subagent that names one "
                    f"gets it: {', '.join(subagent_only)}"
                ),
            )
        )
    # Said here because nothing later will: an answer from the model the subagent was
    # meant to check looks like a good one.
    for name, why in indistinct:
        events.append(RunEvent(kind="indistinct", text=f"{name} {why}", agent=name))
    if placement.placed:
        # Named, because a replacement is a caller's file silently overwritten.
        replaced = f" ({len(placement.replaced)} replaced)" if placement.replaced else ""
        events.append(
            RunEvent(kind="inputs_placed", text=f"{', '.join(placement.placed)}{replaced}")
        )
    events.append(RunEvent(kind="run_start", text=turn_id))
    return tuple(events)

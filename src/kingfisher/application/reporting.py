"""What a run tells the caller it did not have."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.tools.spec import Offering, admitted, out_of_reach, placed
from kingfisher.domain.access import AUDIENCED, handed_work_by, offers
from kingfisher.domain.capabilities import Capabilities, Selection, withheld
from kingfisher.domain.result import RunEvent
from kingfisher.infrastructure.harness.activation import (
    activatable_skills,
    available_skills,
    defined_subagents,
)
from kingfisher.infrastructure.harness.kinds.tools import (
    registered_tools,
    workspace_tool_names,
)

if TYPE_CHECKING:
    from kingfisher.config import Config
    from kingfisher.core.definitions.agents.spec import AgentSpec


def _named(selection: Selection) -> set[str]:
    """A selection as a set of names, with the two ends read as empty."""
    return set(selection) if isinstance(selection, tuple) else set()


def _as_written(name: str) -> str | None:
    """A name that has one spelling, compared as it stands."""
    return name


def withheld_by_kind(  # noqa: PLR0913 -- four of these are the four places
    # "what the workspace offers" comes from, and none is derivable from
    # another: the grant, the config, the built graph and the catalogue. The
    # last two are who is asking. Folding any of them into a parameter object
    # would hide which source a kind is measured against, which is the one
    # thing a reader of this function needs to see.
    allowed: Capabilities,
    cfg: Config,
    graph: Any,
    catalogue: Definitions,
    *,
    agent: AgentSpec | None = None,
    held: frozenset[str] | None = None,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What this request left out, per kind, skipping the kinds it left nothing.

    **`middlewares` is deliberately not among them**, and the reason is about this
    report rather than about that axis. What goes here is what a caller could have
    asked for differently -- a tool, a skill, a subagent they may grant next time. A
    caller cannot register a middleware: the names come from whatever constructed
    `Kingfisher`, so telling them one was withheld names something they have no way
    to act on.

    The catalogue is passed rather than re-derived, so what a caller is told it did
    not grant is measured against the same directories the agent was built from.
    """
    default = Capabilities()
    workspace = tuple(workspace_tool_names(cfg, catalogue=catalogue))
    # A skill may be written bare or as `source::name`, and the agent file and the
    # registry need not have chosen the same one -- so skills are compared by the one
    # skill each spelling means, or an audience written `catalogue::audit` would hide
    # nothing from a listing that says `audit`.
    spelling = {"skills": activatable_skills(cfg, catalogue=catalogue).identity}

    # What this agent would have held for *someone*, less what it holds for
    # this caller: exactly the names source-id narrowing took away, per field.
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
        """`names`, less what this caller's source ids took away.

        Applied to what the workspace *offers*, before the comparison, and the
        ordering is the whole of it. This function names every offered thing a grant
        left out -- so measured against the unfiltered catalogue it would hand a
        caller the exact list of what their source ids denied them, which is precisely
        what filtering them out of listings and refusals exists to avoid. An asset
        out of reach is not withheld from this caller; as far as they are concerned
        it was never offered.
        """
        if kind is None or not (lost := denied.get(kind)):
            return names
        same = spelling.get(kind, _as_written)
        gone = {same(name) for name in lost} - {None}
        return tuple(name for name in names if same(name) not in gone)

    def subagents() -> tuple[str, ...]:
        """Every subagent this run could have been handed work by, for this caller.

        Two conditions: the subagent's own `source_ids:` has to admit them, and the
        agent this run opened has to name it. `denied` can see neither -- it is what
        the agent's own line removed from a name it does name -- and asking only the
        first left a subagent nothing activates reported as withheld, which says it
        exists. `handed_work_by` is the one answer the listing asks too.
        """
        defined = defined_subagents(cfg, catalogue=catalogue)
        # No branch on `held` of its own: with no vocabulary `handed_work_by` answers
        # every subagent, which is what this said before and what an unscoped reader is
        # owed. A scoped run with no spec to ask -- an injected graph, which resolves
        # none -- reaches nothing that can be shown reachable, and kingfisher did not
        # wire that graph's subagents either.
        return handed_work_by([agent] if agent is not None else [], defined, held)

    # The agent this run opened, and not the subagents it reaches: a subagent can only
    # narrow what reached it, so one declaring a tool the agent was not granted
    # contributes a name this caller gets by no path. `offers` says what the ceiling
    # does with it. Built once, because a predicate per row would ask twice.
    reached_agents = [] if agent is None else [agent]
    # `same` on both axes, for the reason the listing gives: a grant may write the
    # long form a tool's own catalogue spells short, and comparing them unspelt named
    # nothing.
    spelt = set(workspace)
    offers_tool = admitted(
        offers("tools", held, reached_agents, same=lambda one: placed(one, spelt)),
        shut=out_of_reach(catalogue.tools.found, held),
    )
    offers_skill = offers("skills", held, reached_agents, same=spelling["skills"])

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
        # Tools and skills are measured against what a definition in reach offers,
        # not against the whole catalogue less what this agent's own line removed.
        # `visible` was all they had, and it only ever subtracted the agent's
        # narrowing -- so a caller who narrowed their own grant was told the name of
        # every tool and skill no definition they reach offers them, which is the one
        # thing filtering them out of listings and refusals exists to prevent. The
        # subagent row below has had the same treatment since it was written.
        ("tool", "tools", None, lambda: tuple(n for n in workspace if offers_tool(n))),
        # `cfg` as well as the request, which is where the two axes meet everywhere
        # else: with the switch off the agent is told about no skills at all, and a
        # report of one "not granted" says the others were. The third reader of this
        # switch to have asked only the request -- the subagent branch and `doctor`
        # were the first two, and the entry recording those says why.
        (
            "skill",
            "skills",
            None,
            lambda: tuple(
                name
                for name in (
                    available_skills(cfg, catalogue=catalogue) if cfg.skills_enabled else ()
                )
                if offers_skill(name)
            ),
        ),
        ("subagent", "subagents", "subagents", subagents),
    )
    found = []
    for what, field, kind, names_of in offered:
        granted = getattr(allowed, field)
        # Skips the work, not the answer: every axis defaults to `ALL`, and
        # `withheld(ALL, ...)` is empty, so this changes no report. What it saves is
        # `names_of()`, which for skills and subagents reads the catalogue. It used to
        # say `subagents` defaults to none, which the dataclass contradicts.
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=visible(kind, tuple(names_of()))):
            found.append((what, left_out))
    return tuple(found)


def subagent_only(allowed: Capabilities, cfg: Config, *, catalogue: Any) -> tuple[str, ...]:
    """Names this run was granted that only a subagent can actually ask for."""
    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return Offering.of(found).ambiguous(allowed.tools, found)


def opening_events(  # noqa: PLR0913, PLR0917 -- one parameter per warning
    # kind, and folding them into a bag would only move the list somewhere
    # a reader has to go and find it.
    turn_id: str,
    unprotected: tuple[str, ...],
    placement: Any,
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = (),
    indistinct: tuple[tuple[str, str], ...] = (),
    subagent_only: tuple[str, ...] = (),
) -> tuple[RunEvent, ...]:
    """What the caller is told before the model is reached.

    A function because it is one: nothing here touches the service, and every
    input is already decided by the time it runs. Setup was 123 lines once, and
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
    # Granted, and still not in the agent's own hands. Two files may each define a
    # `fetch`, and an agent dispatches by name -- so the pair goes to whichever subagent
    # names one, and the agent holding the grant gets neither.
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
    # A subagent that meant to run elsewhere and did not. Said here because
    # nothing later will: it builds, it answers, and an answer from the model
    # it was supposed to be checking looks exactly like a good one.
    for name, why in indistinct:
        events.append(RunEvent(kind="indistinct", text=f"{name} {why}", agent=name))
    if placement.placed:
        # Replacement is the one dangerous case -- a caller's file, silently
        # overwritten -- so it is named rather than assumed.
        replaced = f" ({len(placement.replaced)} replaced)" if placement.replaced else ""
        events.append(
            RunEvent(kind="inputs_placed", text=f"{', '.join(placement.placed)}{replaced}")
        )
    # The turn's name, where this used to be its directory. A turn has none now --
    # it works in the session's `/scratchpad` like every other turn -- so the one fact
    # left worth opening with is which turn a caller is watching.
    events.append(RunEvent(kind="run_start", text=turn_id))
    return tuple(events)

"""What a run tells the caller it did not have, and which of a caller's names open
anything at all."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .access import (
    ALL,
    Audience,
    Capabilities,
    Opened,
    Opening,
    activated,
    handed_work_by,
    jointly,
    offers,
    stated,
    withheld,
)
from .agent.activation import (
    activatable_skills,
    available_skills,
    defined_subagents,
    for_the_caller,
)
from .agent.kinds.tools import (
    offered_tool_names,
    private_tools,
    registered_tools,
)
from .definitions.catalogue import Definitions
from .definitions.subagents.spec import opens_skills
from .definitions.tools.spec import Offering, admitted, out_of_reach, placed
from .result import RunEvent

if TYPE_CHECKING:

    from .config import Config
    from .definitions.agents.spec import AgentSpec


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
    offered_tools = tuple(offered_tool_names(cfg, catalogue=catalogue))
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
    spelt = set(offered_tools)
    offers_tool = admitted(
        offers("tools", held, reached_agents, same=lambda one: placed(one, spelt)),
        shut=out_of_reach(catalogue.tools.found, held),
    )
    shut_skills = catalogue.skills_shut(held)
    granted_skill = offers("skills", held, reached_agents, same=identity)

    def offers_skill(name: str) -> bool:
        return granted_skill(name) and identity(name) not in shut_skills

    offered = (
        # Built-ins and offered tools are granted apart, so reported apart. `or ()`: a
        # graph that cannot be read lists no built-ins rather than failing the turn.
        ("builtin tool", "builtin_tools", lambda: tuple(
            n for n in registered_tools(graph) or () if n not in set(offered_tools)
        )),
        # Measured against what a definition in reach offers, not the whole catalogue:
        # else a caller who narrowed their grant is told every name nothing in reach offers.
        ("tool", "tools", lambda: tuple(n for n in offered_tools if offers_tool(n))),
        # `cfg` and `read_file` as well as the request: with skills switched off, or no
        # way to open one, the agent is told of none, and one "not granted" says others were.
        (
            "skill",
            "skills",
            lambda: tuple(
                name
                for name in (
                    available_skills(cfg, catalogue=catalogue)
                    if cfg.skills_enabled and opens_skills(allowed.builtin_tools)
                    else ()
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


def _entries(audiences: tuple[Audience, ...]) -> tuple[frozenset[str], ...]:
    """These audiences as one: the name sets that each satisfy all of them.

    Through `jointly` even where there is one: it keeps only the ways no other needs
    less than, and a name carried only by a dominated entry is never why anything
    opened -- `[A, {A, B}]` is opened by A alone, and B is not a reason for it.
    """
    if (ways := jointly(audiences)) == ALL:
        return ()
    return tuple(one if isinstance(one, frozenset) else frozenset((one,)) for one in ways)


class _Reasons:
    """What a walk has found so far: the names in an entry this caller satisfies, and
    the names one short of satisfying one.

    Both halves off the same entries in one pass, so they cannot come apart about what
    an audience asks for -- the fault `offers` and the withheld report were split to
    stop. Not a dataclass: `field` is a loop variable in this module.
    """

    def __init__(self, held: frozenset[str]) -> None:
        self.held = held
        self.satisfied: set[str] = set()
        self.short: set[str] = set()

    def see(self, *audiences: Audience) -> None:
        """One thing's own audience, joint with the path that reaches it."""
        entries = _entries(audiences)
        if not entries:
            return
        if any(entry <= self.held for entry in entries):
            self.satisfied |= {
                name for entry in entries if entry <= self.held for name in entry
            }
            return
        # Only where the audience is shut: an entry one short of a thing already reached
        # names nothing this caller would gain.
        for entry in entries:
            if len(missing := entry - self.held) == 1:
                self.short |= missing


def what_opens(
    asked: Capabilities,
    cfg: Config,
    catalogue: Definitions,
    *,
    agent: AgentSpec,
    held: frozenset[str],
) -> Opened:
    """Which of this caller's names open something under one agent, and which would.

    `asked` is the grant a run would use -- the deployment's under the request's -- so
    this answers about a request the deployment would serve rather than about the locks
    alone: an empty `opens` does not tell a lock from a withholding grant.

    Taken for no caller, deliberately. `declares(held)` drops what this caller cannot
    reach, which is the answer being computed: asked for them, what an agent they cannot
    open lists is gone before its locks can be read, and `would` could never name the id
    that would open it. What a definition offers is one question and who
    reaches it is the other, and only the audiences below answer the second.
    """
    offered = for_the_caller(agent, asked, held=None)
    reasons = _Reasons(held)
    # The agent first, and every audience below it joint with the path reaching it: a
    # zoned tool under an agent this caller cannot open is two names short, so nothing
    # claims its zone alone would open it. Load-bearing now that the selections above
    # are taken for no caller -- without the join, a shut agent's tools and subagents
    # would each report their own missing name as one that opens something.
    path = stated(agent).audience
    reasons.see(path)
    found = catalogue.tools.found
    offering = Offering.of(found)
    # `spelt` first, as a run spells a grant: `carried` matches the catalogue's spelling,
    # so a `file.py::name` entry would skip that tool's lock.
    for one in offering.carried(offering.spelt(offered.tools), found):
        reasons.see(path, one.audience)
    for audience in _skills_told(cfg, catalogue, allowed=offered):
        reasons.see(path, audience)

    defined = defined_subagents(cfg, catalogue=catalogue)
    for name in activated(offered.subagents, defined):
        spec = defined[name]
        # Its own line is the only place a subagent says who reaches it, and it may
        # require a datazone, which an agent may not.
        under = jointly((path, stated(spec).audience))
        reasons.see(under)
        shared = spec.declares(None)
        for one in offering.carried(offering.spelt(shared.tools), found):
            reasons.see(under, one.audience)
        # Its own folder's tools, which no grant reaches, still answer to their own
        # locks. Asked unscoped: filtered for this caller they would be invisible here,
        # which is the question.
        for one in private_tools(catalogue, name, held=None):
            reasons.see(under, one.audience)
        for audience in _skills_told(cfg, catalogue, allowed=shared):
            reasons.see(under, audience)
    return Opened(opens=Opening.of(reasons.satisfied), would=Opening.of(reasons.short))


def _skills_told(
    cfg: Config,
    catalogue: Definitions,
    *,
    allowed: Capabilities,
) -> tuple[Audience, ...]:
    """The lock on each skill this definition could actually open.

    Nothing where no skill can be opened at all -- switched off for the deployment, or
    no read tool in the grant -- because an audience on a line the model is never told
    opens nothing real, and granting the name it asks for would change nothing. The
    same two gates the withheld report puts in front of that axis.
    """
    if not cfg.skills_enabled or not opens_skills(allowed.builtin_tools):
        return ()
    identity = activatable_skills(cfg, catalogue=catalogue).identity
    # By the skill each spelling means, as the withheld report compares them: the lock
    # written on `catalogue::audit` is the lock on `audit`.
    by_skill = {meant: lock.audience for meant, lock in catalogue.locked_skills.items()}
    named = (
        available_skills(cfg, catalogue=catalogue)
        if allowed.skills == ALL
        else (allowed.skills or ())
    )
    return tuple(by_skill.get(identity(one) or one, ALL) for one in named)


def subagent_only(allowed: Capabilities, *, catalogue: Definitions) -> tuple[str, ...]:
    """Names this run was granted that only a subagent can actually ask for."""
    found = catalogue.tools.found
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
    # A grant means less than the definitions offer and says so nowhere: told here, not
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

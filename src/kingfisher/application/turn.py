"""The shape of one turn: what it carries, and the two ways it can end early."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.domain.result import PendingDecision, RunEvent
from kingfisher.infrastructure.harness import runtime
from kingfisher.layout import SCRATCH, SCRATCH_ROUTE

if TYPE_CHECKING:
    from kingfisher.config import Config
    from kingfisher.domain.transcript import Message


@dataclass(frozen=True)
class Prepared:
    """Everything a turn needs before the model is reached, once nothing can refuse it."""

    graph: Any
    message: str
    session: Any
    turn: Any
    logger: Any
    config: dict[str, Any]
    events: tuple[RunEvent, ...]
    deadline: float
    timeout_s: float
    #: The backend the session's files are reached through. The same object the
    #: graph runs on, so what was placed is what the agent reads, and what is
    #: collected is what it wrote.
    files: Any = None
    #: What kingfisher keeps about the session under `/.harness`, read and written
    #: through `files`.
    harness: Any = None
    #: The saver this service opened for the turn, closed when the turn ends, or None
    #: when it opened nothing -- an injected instance is the deployment's to close.
    release: Any = None
    #: The saver itself, which `release` is only sometimes. A turn that stops at a
    #: gate has to write what this holds before the turn lets go of it.
    saver: Any = None
    #: What a graph kingfisher built is driven with, or `None` for one the caller
    #: built -- that graph's context is whatever its builder decided, which is not
    #: known here.
    context: Any = None
    #: What was said in this session before now. The graph's saver holds one
    #: turn and nothing after it, so this is where a conversation comes from.
    history: tuple[Message, ...] = ()
    #: Answers to a turn that stopped for them, in langgraph's own shape, or `None`
    #: for a turn that is asking something new. The two are alternatives rather than
    #: additions: a resume continues a graph mid-superstep and has no message to add.
    resume: dict[str, Any] | None = None
    #: The agent this turn's graph was built from, by name, or `None` where none was
    #: resolved -- an injected graph under no policy. The *resolved* one, not the one
    #: the request named: a session's agent is fixed when it opens, so every turn after
    #: the first names nothing and the two answers are only the same on turn one.
    #: Written beside a pause, so a resume naming a different agent can be refused.
    agent_name: str | None = None
    #: Tools an earlier turn was waiting on that this one superseded. Carried to the
    #: end of the turn as well as announced at its start, because `run` drains the
    #: stream for a result and would otherwise be the one caller never told.
    discarded: tuple[str, ...] = ()
    #: Paths `protect_data` could not harden. Reported to the caller rather
    #: than raised, so they cross the boundary instead of stopping at it.
    unprotected: tuple[str, ...] = ()
    #: What this request's `data` placed in `/data`, and what that replaced.
    placement: Any = None
    #: `(what, names)` for each thing this workspace offers that the request did
    #: not grant -- tools, skills, subagents. Crosses rather than stopping: a
    #: withheld name is a fact about the run, not a refusal.
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: `(name, why)` for each delegate that asked to run elsewhere and did not.
    #: A fact about the run, like `withheld` -- nothing is wrong enough to stop
    #: for, and nothing else would ever say it.
    indistinct: tuple[tuple[str, str], ...] = ()
    #: Tool names more than one file defines, which the agent holding the
    #: grant therefore cannot hold. Reported rather than dropped in silence.
    delegate_only: tuple[str, ...] = ()


def turn_message(task: str, placed: tuple[str, ...]) -> str:
    """The task, plus this turn's facts and nothing more."""
    # Named because `/data` changed under a session the agent may already have
    # looked at. It is also where a caller's files for *this* request land now --
    # they used to have a turn directory of their own, and this line said where.
    arrived = f" New files in /data: {', '.join(placed)}." if placed else ""
    # Both names for the one directory. `system.md` states the rule -- drop the
    # leading slash for the shell -- and stating it there was not enough: over
    # ten runs of one task the agent passed the virtual path to `execute` 4
    # times, each failing and costing roughly three times the whole task to
    # recover. The 6 that used the shell form first never failed.
    #
    # It said this about a per-turn `/runs/<turn>`; the directory is the session's
    # `/scratchpad` now and the sentence is unchanged in kind, because what was
    # measured was the agent's handling of the two spellings rather than anything
    # about which directory it was being handed.
    return (
        f"{task}\n\n"
        f"{SCRATCH_ROUTE} is yours to work in (from the shell, {SCRATCH})."
        f"{arrived}"
    )


def consume(
    namespace: Any,
    mode: str,
    chunk: Any,
    answer: str,
    delegates: runtime.Delegates,
) -> tuple[str, tuple[RunEvent, ...]]:
    """One stream chunk into (answer so far, events to emit)."""
    if (text := runtime.answer_in(namespace, mode, chunk)) is not None:
        answer = text
    return answer, tuple(runtime.events_in(namespace, mode, chunk, delegates))


def decision_needed(waiting: tuple[PendingDecision, ...]) -> RunEvent:
    """The event a turn that stopped at a gate owes its caller.

    Carries the calls on `tools`/`args`, which is the shape `model_call` already
    publishes -- a consumer rendering one can render this. The ids are not on the
    event: they are on `RunResult.pending`, which is what a resume is built from, and
    a second copy on the event is a second thing to keep parallel.
    """
    return RunEvent(
        kind="decision_needed",
        text=f"waiting on {', '.join(sorted({item.tool for item in waiting}))}",
        tools=tuple(item.tool for item in waiting),
        args=tuple(item.args for item in waiting),
    )


def decision_discarded(tools: tuple[str, ...]) -> RunEvent:
    """The event a turn owes for the gate it superseded."""
    return RunEvent(
        kind="decision_discarded",
        text=f"a pending decision on {', '.join(tools)} was dropped by this turn",
        tools=tools,
    )


def overrun(prepared: Prepared) -> RunEvent | None:
    """The cut-short event once a turn is out of time, else nothing."""
    if monotonic() <= prepared.deadline:
        return None
    return RunEvent(kind="cut_short", text=f"turn stopped after {prepared.timeout_s}s")


def out_of_steps(cfg: Config) -> RunEvent:
    """The same event for the other bound on a turn."""
    return RunEvent(
        kind="cut_short",
        text=(
            f"turn stopped after {cfg.recursion_limit} steps "
            f"(raise KINGFISHER_RECURSION_LIMIT)"
        ),
    )

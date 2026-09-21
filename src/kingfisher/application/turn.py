"""The shape of one turn: what it carries, and the two ways it can end early."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.domain.result import RunEvent
from kingfisher.infrastructure.harness import runtime
from kingfisher.layout import SCRATCH, SCRATCH_ROUTE

if TYPE_CHECKING:
    from kingfisher.config import Config
    from kingfisher.domain.request import Request
    from kingfisher.domain.transcript import Message


@dataclass(frozen=True)
class Admitted:
    """A request that has passed everything able to reject it."""

    request: Request
    session: Any
    graph: Any
    #: Paths `protect_data` could not harden. Reported to the caller rather
    #: than raised, so they cross the boundary instead of stopping at it.
    unprotected: tuple[str, ...]
    placement: Any
    #: The saver this service opened for the turn, or None when it opened
    #: nothing -- an injected instance is the deployment's to close.
    release: Any = None
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


@dataclass(frozen=True)
class Prepared:
    """Everything a turn needs before the model is reached."""

    graph: Any
    message: str
    session: Any
    turn: Any
    logger: Any
    config: dict[str, Any]
    events: tuple[RunEvent, ...]
    deadline: float
    timeout_s: float
    #: Closed when the turn ends. See `_checkpointer_for`.
    release: Any = None
    #: What was said in this session before now. The graph's saver holds one
    #: turn and nothing after it, so this is where a conversation comes from.
    history: tuple[Message, ...] = ()


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
    # `/scratch` now and the sentence is unchanged in kind, because what was
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

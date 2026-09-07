"""The shape of one turn: what it carries, and the two ways it can end early.

Two records and four small functions, together because they describe a turn rather
than run one. `Admitted` and `Prepared` are the seam `service` is built around --
everything able to *refuse* a request happens before the first, and everything that
creates happens after it, which was a claim in a docstring until the halves became
separate functions with a type between them.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.domain.result import RunEvent
from kingfisher.infrastructure.harness import runtime

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
    #: Content resolved from `input_refs`, held until the turn exists.
    #:
    #: Fetched during admission because a ref that will not resolve must refuse
    #: the request, and written in `_open_turn` because a turn's `input/` is not
    #: there yet. The bytes wait in between rather than the refusal moving.
    fetched_inputs: Any = None
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


def turn_message(task: str, turn: Any, placed: tuple[str, ...], has_inputs: bool) -> str:
    """The task, plus this turn's facts and nothing more."""
    # Named because `/data` changed under a session the agent may already have
    # looked at.
    arrived = f" New files in /data: {', '.join(placed)}." if placed else ""
    supplied = (
        f" Files supplied with this request are in {turn.virtual_input_dir}."
        if has_inputs
        else ""
    )
    # Both names for the one directory. `system.md` states the rule -- drop the
    # leading slash for the shell -- and stating it there was not enough: over
    # ten runs of one task the agent passed the virtual path to `execute` 4
    # times, each failing and costing roughly three times the whole task to
    # recover. The 6 that used the shell form first never failed. This line is
    # already per-turn, so unlike the system prompt it costs no cache to say.
    return (
        f"{task}\n\n"
        f"Your run directory for this task is {turn.virtual_dir} "
        f"(from the shell, {turn.shell_dir}).{supplied}{arrived}"
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

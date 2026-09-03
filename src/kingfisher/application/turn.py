"""The shape of one turn: what it carries, and the two ways it can end early.

Two records and four small functions, together because they describe a turn
rather than run one. `Admitted` and `Prepared` are the seam `service` is built
around -- everything able to *refuse* a request happens before the first, and
everything that creates happens after it, which was a claim in a docstring until
the halves became separate functions with a type between them.

The rest is what a turn needs and the service does not: the message the model is
given, one stream chunk read the same way by both loops, and the two bounds a
turn can hit. Both bounds produce the same `cut_short` event on purpose -- one is
seconds and the other is steps, and until they were written alike one of them
came out as a stack trace.
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
    """A request that has passed everything able to reject it.

    The boundary is the whole point of the type. `_admit` may raise -- an
    unknown session, a quota, a file that is not there, middleware this request
    may not use -- and it runs before any turn directory exists. `_open_turn`
    creates one and never refuses.

    That ordering was a claim in a docstring, and it was false: `--data` naming
    a missing file left nothing behind, while `--input` naming one left `t001`,
    because the inputs were copied after the turn was allocated. Making the
    halves separate functions with one type between them is what turns the
    claim into something a reader can check.
    """

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
    """Everything a turn needs before the model is reached.

    Extracted so the orchestration sequence exists once. `stream` and `astream`
    differ only in how they iterate the graph -- a dozen lines each -- and the
    hundred lines of ordering that matter are not written twice to drift apart.
    """

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
    """The task, plus this turn's facts and nothing more.

    What the task should *produce* is the task's business: asking for a written
    report is one kind of request among many, and a general agent should not
    carry one convention's filenames in its plumbing. They lived in the system
    prompt once, which made every greeting deliberate over two files nobody
    wanted.

    These facts reach the model here rather than in the system prompt because
    they are run-scoped: the prompt is the cached prefix, and putting a turn
    directory in it would move that prefix on every session.
    """
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
    """One stream chunk into (answer so far, events to emit).

    Both loops are offered every chunk and each mode ignores the ones that are
    not its own. Written once so the sync and async loops cannot come to
    disagree about which mode carries the answer -- or, now, about which agent
    a chunk came from, which is a second thing they could have drifted on.
    """
    if (text := runtime.answer_in(namespace, mode, chunk)) is not None:
        answer = text
    return answer, tuple(runtime.events_in(namespace, mode, chunk, delegates))


def overrun(prepared: Prepared) -> RunEvent | None:
    """The cut-short event once a turn is out of time, else nothing.

    Checked between chunks, which is the only place there is to stop. What the
    turn produced is already on disk and in the manifest, so ending here keeps
    the work and loses only the steps that had not happened yet.
    """
    if monotonic() <= prepared.deadline:
        return None
    return RunEvent(kind="cut_short", text=f"turn stopped after {prepared.timeout_s}s")


def out_of_steps(cfg: Config) -> RunEvent:
    """The same event for the other bound on a turn.

    Two bounds, and until this they behaved nothing alike. `turn_timeout_s` is
    checked between chunks and ends the turn as a `RunResult` whose
    `stop_reason` is `max_duration`; `recursion_limit` is enforced inside
    langgraph's own loop and came out
    as a `GraphRecursionError` through every caller -- the driver printed a
    stack trace, and the HTTP surface had no mapping for it at all.

    Observed on a run that had already written its report and validated the
    markup: the file was on disk, and what the caller got was a traceback with
    no path in it. Nothing about running out of steps is less ordinary than
    running out of seconds, so it is reported the same way and the work
    survives the same way.

    Names the setting because the two bounds are raised in different places and
    "turn stopped" alone sends the reader to the wrong one.
    """
    return RunEvent(
        kind="cut_short",
        text=(
            f"turn stopped after {cfg.recursion_limit} steps "
            f"(raise KINGFISHER_RECURSION_LIMIT)"
        ),
    )

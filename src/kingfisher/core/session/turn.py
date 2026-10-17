"""The shape of one turn: what it carries, and the two ways it can end early."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import DEFAULT_LAYOUT, Layout
from kingfisher.core.result import LedgerEntry, PendingDecision, RunEvent
from kingfisher.core.session import runtime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from kingfisher.core.config import Config
    from kingfisher.core.session.store import KeptLedger
    from kingfisher.core.session.transcript import Message


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
    #: The saver the turn ran on. A turn that stops at a gate has to write what this
    #: holds before the turn lets go of it.
    saver: Any = None
    #: What a graph kingfisher built is driven with, or `None` for one the caller
    #: built -- that graph's context is whatever its builder decided, which is not
    #: known here.
    context: Any = None
    #: What was said in this session before now. The graph's saver holds one
    #: turn and nothing after it, so this is where a conversation comes from.
    history: tuple[Message, ...] = ()
    #: What this turn's tools returned, gathered as the graph runs and kept when it
    #: ends, or `None` for a graph the caller built, which nothing here can hook.
    ledger: Any = None
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
    #: Paths `protect_inputs` could not harden. Reported to the caller rather
    #: than raised, so they cross the boundary instead of stopping at it.
    unprotected: tuple[str, ...] = ()
    #: What this request's `inputs` placed in `/inputs`, and what that replaced.
    placement: Any = None
    #: `(what, names)` for each thing this workspace offers that the request did
    #: not grant -- tools, skills, subagents. Crosses rather than stopping: a
    #: withheld name is a fact about the run, not a refusal.
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: `(name, why)` for each subagent that asked to run elsewhere and did not.
    #: A fact about the run, like `withheld` -- nothing is wrong enough to stop
    #: for, and nothing else would ever say it.
    indistinct: tuple[tuple[str, str], ...] = ()
    #: Tool names more than one file defines, which the agent holding the
    #: grant therefore cannot hold. Reported rather than dropped in silence.
    subagent_only: tuple[str, ...] = ()


def turn_message(
    task: str,
    placed: tuple[str, ...],
    earlier: KeptLedger | None = None,
    *,
    layout: Layout = DEFAULT_LAYOUT,
) -> str:
    """The task, plus this turn's facts and nothing more.

    `earlier` is the ledger as the turn found it, or `None` for a graph the deployment
    supplied, which records nothing and need not have kingfisher's prompt to explain it.
    `layout` names the session folders for this deployment; the service passes its own.
    """
    # Named because `/inputs` changed under a session the agent may already have
    # looked at. It is also where a caller's files for *this* request land now --
    # they used to have a turn directory of their own, and this line said where.
    arrived = f" New files in {layout.inputs_route}: {', '.join(placed)}." if placed else ""
    # Here and never in the system prompt, which is the cached prefix: a line that
    # changes every turn there would miss the cache on every call.
    shown = ledger_shown(earlier) if earlier is not None else ""
    ledger = f"\n\n{shown}" if shown else ""
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
        f"{layout.scratch_route} is yours to work in (from the shell, {layout.scratchpad})."
        f"{arrived}"
        f"{ledger}"
    )


_KB = 1024

#: How many of the latest exchanges that recorded anything the index lists in full.
IN_FULL = 5


def ledger_shown(earlier: KeptLedger) -> str:
    """What a new message says of the ledger: the delta, the index, or nothing.

    The index once a turn since the last message ended on a summarised conversation:
    the delta lists only what is new, and the messages that listed the rest are what
    the summary replaced. Read off the summariser's own mark rather than estimated
    from the conversation's size, which would be a copy of its threshold to drift
    whenever the model or its settings change.
    """
    if not earlier.summarised:
        return ledger_delta(earlier.since) if earlier.since else ""
    return ledger_index(earlier.exchanges) if any(earlier.exchanges) else ""


def ledger_delta(entries: Sequence[LedgerEntry]) -> str:
    """Entries the ledger recorded, a line each."""
    return _block("ledger_delta", _listed(entries))


def ledger_index(exchanges: Sequence[Sequence[LedgerEntry]]) -> str:
    """Every exchange that recorded anything: the latest few as the delta lists them,
    and each one before those as a line of its ids and a count by tool.
    """
    recorded = [one for one in exchanges if one]
    older, recent = recorded[:-IN_FULL], recorded[-IN_FULL:]
    return _block(
        "ledger_index",
        [*map(_rolled_up, older), *(line for one in recent for line in _listed(one))],
    )


def _block(tag: str, lines: Sequence[str]) -> str:
    return "\n".join([f"<{tag}>", *lines, f"</{tag}>"])


def _rolled_up(entries: Sequence[LedgerEntry]) -> str:
    """One exchange as a line: the ids it spans, then each tool, most results first."""
    first, last = entries[0].id, entries[-1].id
    span = first if first == last else f"{first}..{last}"
    counted = sorted(Counter(one.tool for one in entries).items(), key=lambda c: (-c[1], c[0]))
    return f"{span}: {', '.join(f'{n} {tool}' for tool, n in counted)}"


def _listed(entries: Sequence[LedgerEntry]) -> list[str]:
    """A line an entry.

    Each `task` answer is followed by its run's entries, indented: the parent is shown
    where they are and never what they hold. A run whose `task` never answered -- a
    pause superseded, a turn cut short -- has nothing to sit under, so its entries
    follow the rest, each saying why.
    """
    under: dict[str, list[LedgerEntry]] = {}
    for entry in entries:
        if entry.run is not None:
            under.setdefault(entry.run, []).append(entry)
    lines: list[str] = []
    for entry in entries:
        if entry.run is None:
            lines.append(_delta_line(entry))
            lines.extend(f"  {_delta_line(one)}" for one in under.pop(entry.call, ()))
    lines.extend(
        f"{_delta_line(one)}  (its task never answered)" for one in entries if one.run in under
    )
    return lines


def _delta_line(entry: LedgerEntry) -> str:
    called = f"{entry.tool} {entry.args}" if entry.args else entry.tool
    return f"{entry.id} {called} -> {_size(entry.size)}  {entry.path or 'not kept'}"


def _size(size: int) -> str:
    """Bytes under a KB, the nearest KB under an MB, and MB to a tenth.

    The unit is chosen after rounding, so a size just short of an MB never reads as
    1024 KB.
    """
    if size < _KB:
        return f"{size} B"
    if (kb := (size + _KB // 2) // _KB) < _KB:
        return f"{kb} KB"
    return f"{size / (_KB * _KB):.1f} MB"


def consume(  # noqa: PLR0913 -- the chunk, the answer so far, and two things the
    # stream has taught the turn: which subagent is which, and which messages it holds.
    namespace: Any,
    mode: str,
    chunk: Any,
    answer: str,
    subagent_names: runtime.SubagentNames,
    *,
    seen: runtime.SeenMessages | None = None,
) -> tuple[str, tuple[RunEvent, ...]]:
    """One stream chunk into (answer so far, events to emit)."""
    if (text := runtime.answer_in(namespace, mode, chunk)) is not None:
        answer = text
    return answer, tuple(runtime.events_in(namespace, mode, chunk, subagent_names, seen=seen))


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

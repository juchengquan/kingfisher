"""The shape of one turn: what it carries, and the ways it can end early."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.recorded import readable_size
from kingfisher.core.layout import Layout
from kingfisher.core.result import LedgerEntry, MemoryEntry, PendingDecision, RunEvent

from . import runtime

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from kingfisher.core.config import Config

    from .store import KeptLedger
    from .transcript import Message


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
    #: The backend the session's files are reached through: the one the graph runs on,
    #: so what is placed and collected is what the agent read and wrote.
    files: Any = None
    #: What kingfisher keeps about the session under `/.harness`, through `files`.
    harness: Any = None
    #: The saver the turn ran on, which a turn stopping at a gate writes out before
    #: letting it go.
    saver: Any = None
    #: What a graph kingfisher built is driven with, or `None` for one the caller built,
    #: whose context is not known here.
    context: Any = None
    #: What was said in this session before now: the saver holds one turn only, so
    #: this is where a conversation comes from.
    history: tuple[Message, ...] = ()
    #: What this turn's tools returned, kept when it ends, or `None` for a graph the
    #: caller built, which nothing here can hook.
    ledger: Any = None
    #: Answers to a turn that stopped for them, in langgraph's shape, or `None`. Never
    #: beside a message: a resume continues a graph mid-superstep.
    resume: dict[str, Any] | None = None
    #: The agent this turn's graph was built from, by name, or `None` where none was
    #: resolved. The *resolved* one, not the requested: a turn after the first names
    #: none. Written beside a pause, so a resume naming a different agent can be refused.
    agent_name: str | None = None
    #: Tools an earlier turn was waiting on that this one superseded. Carried to the end
    #: as well as announced: `run` drains the stream, so its caller would otherwise never hear.
    discarded: tuple[str, ...] = ()
    #: The token the session's claim answered, which the turn's ending gives back: a
    #: claim is released only by the token that took it.
    claim: str = ""
    #: The memory file's entries as the turn found them, numbered; `None` where memory is
    #: off for this turn, so its ending reads nothing.
    memory: tuple[MemoryEntry, ...] | None = None
    #: The task as the caller wrote it, without what kingfisher adds to its message: the
    #: memory writer is shown what the user said and nothing the turn told the agent.
    task: str = ""
    #: The request's `context`, which no saved memory entry may contain.
    caller_context: Mapping[str, str] = field(default_factory=dict)
    #: Builds the model the memory writer runs on, or `None` for a graph the caller built.
    #: Built only when the writer runs, so a writer model that cannot be built fails that
    #: run and not the turn.
    memory_model: Callable[[], Any] | None = None


def turn_message(
    task: str,
    placed: tuple[str, ...],
    earlier: KeptLedger | None = None,
    *,
    layout: Layout,
    note: str = "",
) -> str:
    """The task and the caller's note, plus this turn's facts and nothing more.

    `earlier` is the ledger as the turn found it, or `None` for a graph the deployment
    supplied, which records nothing.
    """
    # Beside the task, as the caller wrote it: it is the caller's, and kingfisher's lines
    # after it are about where the work is done.
    noted = f"{note}\n\n" if note else ""
    # Named because the inputs changed under a session the agent may already have seen.
    arrived = f" New files in {layout.inputs_route}: {', '.join(placed)}." if placed else ""
    # Here, never in the system prompt: a line changing every turn in the cached prefix
    # would miss the cache on every call.
    shown = ledger_shown(earlier) if earlier is not None else ""
    ledger = f"\n\n{shown}" if shown else ""
    # Both spellings, though the system prompt states the rule: told only there, the
    # agent passed the virtual path to `execute` in 4 runs of 10, each failure costing
    # about three times the whole task.
    return (
        f"{task}\n\n"
        f"{noted}"
        f"{layout.scratch_route} is yours to work in (from the shell, {layout.scratchpad})."
        f"{arrived}"
        f"{ledger}"
    )


#: How many of the latest exchanges that recorded anything the index lists in full.
IN_FULL = 5


def ledger_shown(earlier: KeptLedger) -> str:
    """What a new message says of the ledger: the delta, the index, or nothing.

    The index once a turn since the last message ended summarised, since the summary
    replaced the messages that listed the rest. Read off the summariser's own mark, not
    estimated from size, which would copy its threshold and drift.
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
    """A line an entry, each `task` answer followed by its run's entries, indented.

    A run whose `task` never answered -- a pause superseded, a turn cut short -- has
    nothing to sit under, so its entries follow the rest, each saying why.
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
    kept = entry.path or ("expired" if entry.expired_in is not None else "not kept")
    return f"{entry.id} {called} -> {readable_size(entry.size)}  {kept}"


def consume(  # noqa: PLR0913 -- a chunk, the answer so far, and what the stream taught
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

    The calls ride on `tools`/`args`, as `model_call` publishes them. The ids are only
    on `RunResult.pending`, which a resume is built from, so there is one copy to keep.
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


def model_unavailable(why: str) -> RunEvent:
    """The same event for a turn whose model the provider could not serve."""
    return RunEvent(
        kind="cut_short",
        text=(
            f"turn stopped: the model could not answer ({why}); run it again on this "
            f"session to continue"
        ),
    )


def out_of_tokens(reply: Any) -> RunEvent:
    """The same event for a turn whose agent stopped on a reply cut off at its cap."""
    return RunEvent(
        kind="cut_short",
        text=(
            f"turn stopped: the agent's reply reached {runtime.output_limit(reply)} "
            f"(max_tokens in models.yaml)"
        ),
    )

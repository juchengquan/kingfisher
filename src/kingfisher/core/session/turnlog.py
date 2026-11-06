"""Every turn a session ran, as first written: its messages, each tagged with the turn, and
each turn with the one it followed.

The transcript is the conversation the next turn is built from, so a summary replaces its
front. This keeps what it replaced: the conversation at the end of any turn is its
ancestors' and its own lines replayed in order, where a turn's opening line keeps the first
`from` messages of what its parent left, a message is appended, and a message carrying a
`cut` is a summary standing for everything before that position.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.recorded import expired_handle, stamped_id
from kingfisher.core.layout import TURNS_FILE

from . import runtime
from .steps import Steps
from .store import RESUMED, HarnessFiles, KeptLedger, read_off_path
from .transcript import Message, as_record, from_record

if TYPE_CHECKING:
    from kingfisher.core.request import Decision
    from kingfisher.core.result import LedgerEntry

#: On a turn's opening line, the turn it followed: `null` for the first, and for the first
#: after a rewind to the start.
PARENT = "parent"

#: On an asked turn's opening line, the task as the caller wrote it: the message the model
#: was sent also carries what kingfisher told it, which sending the task again adds anew.
TASK = "task"

#: On an asked turn's opening line, how many of its parent's messages it began from. Fewer
#: than its parent left where the transcript was found short; replayed whole, the turn
#: would be credited with messages its model never saw.
FROM = "from"

#: On a summary, the position in the conversation so far that it stands for everything
#: before, counted with the turn's own messages appended.
CUT = "cut"

#: On a resume's line for one answer: the decision as the pause named it, a position in the
#: pause and not a call; `call_id` beside it is the call it answered.
DECISION = "decision"


@dataclass(frozen=True)
class Resuming:
    """What the pause a resume answers left, read before the resume runs on it."""

    #: How many messages the paused state held: the resume's own begin after them.
    held: int
    #: The summary the paused state already carried, its text and cut, which the paused
    #: turn logged; a resume ending on it summarised nothing.
    carried: tuple[str, int] | None
    #: A line for each answer, naming the call it answered.
    answered: tuple[dict[str, Any], ...]


def resuming(graph: Any, config: Any, saver: Any, decisions: Sequence[Decision]) -> Resuming | None:
    """What the pause in `saver` holds, or `None` for a graph that keeps no state, which
    writes no transcript either."""
    read = getattr(graph, "get_state", None)
    if read is None:
        return None
    paused = read(config)
    calls = runtime.calls_paused(paused, saver)
    return Resuming(
        held=len(runtime.held_in(paused)),
        carried=_identity(runtime.summary_in(paused)),
        answered=tuple(
            {DECISION: one.call_id, "call_id": calls.get(one.call_id), "answer": one.action}
            for one in decisions
        ),
    )


def _identity(summary: tuple[Any, int] | None) -> tuple[str, int] | None:
    """What tells one summary from another: a summary message carries no id."""
    return None if summary is None else (str(getattr(summary[0], "content", "")), summary[1])


def _turn_lines(  # noqa: PLR0913 -- what the turn was asked, and what its state holds
    turn: str,
    parent: str | None,
    *,
    task: str,
    history: int,
    resumed: Resuming | None,
    snapshot: Any,
) -> list[dict[str, Any]]:
    """This turn's lines: its opening, a resume's answers, the messages it added and the
    summary it ended on. `history` is how many messages its transcript held as it began,
    `resumed` set for a resume.
    """
    held = runtime.held_in(snapshot)
    if resumed is None:
        opening: dict[str, Any] = {"turn": turn, PARENT: parent, TASK: task, FROM: history}
        began, carried = history, None
    else:
        opening = {"turn": turn, PARENT: parent, RESUMED: True}
        began, carried = resumed.held, resumed.carried
    lines = [
        opening,
        *({"turn": turn, **answer} for answer in (resumed.answered if resumed else ())),
        *({"turn": turn, **as_record(one)} for one in runtime.as_transcript(held[began:])),
    ]
    summary = runtime.summary_in(snapshot)
    if summary is not None and _identity(summary) != carried:
        written, cut = summary
        (said,) = runtime.as_transcript([written])
        # A position in what the log replays, which counts records rather than messages,
        # and after a summary it carried opens on that summary rather than on the cut.
        at = (
            1 + len(runtime.as_transcript(held[carried[1] : cut]))
            if carried is not None
            else len(runtime.as_transcript(held[:cut]))
        )
        lines.append({"turn": turn, **as_record(said), CUT: at})
    return lines


def _without_expired(
    lines: Sequence[tuple[str, dict[str, Any]]], rows: Mapping[str, LedgerEntry]
) -> list[tuple[str, dict[str, Any]]]:
    """`lines` with each result `rows` names, by id, swapped for its expired handle, so
    `KINGFISHER_KEEP_RESULTS_ROUNDS` bounds what this file keeps as well."""
    kept: list[tuple[str, dict[str, Any]]] = []
    for raw, line in lines:
        content = line.get("content")
        row = rows.get(stamped_id(content) or "") if isinstance(content, str) else None
        if row is None or line.get("call_id") != row.call or content == expired_handle(row):
            kept.append((raw, line))
            continue
        # Patched as parsed, so a key another version wrote survives.
        swapped = {**line, "content": expired_handle(row)}
        kept.append((json.dumps(swapped, ensure_ascii=False), swapped))
    return kept


def _read(held: bytes) -> list[tuple[str, dict[str, Any]]]:
    """Each line as written beside what it holds. A line that does not parse is dropped,
    as the ledger drops one: a backend has no rename, so a cut write can end anywhere."""
    lines = []
    for raw in held.decode("utf-8", errors="replace").splitlines():
        try:
            line = json.loads(raw)
        except ValueError:
            continue
        if isinstance(line, dict):
            lines.append((raw, line))
    return lines


def keep_turns(  # noqa: PLR0913 -- the turn, what it began from, and what it ended on
    harness: HarnessFiles,
    *,
    turn: str,
    task: str,
    history: Sequence[Message],
    resumed: Resuming | None,
    snapshot: Any,
    ledger: KeptLedger | None,
) -> Steps[None]:
    """Add this turn to the log. `ledger` is the ledger as this turn writes it back, or
    `None` where it keeps none.
    """
    held = yield from harness.fetch(TURNS_FILE)
    lines = [] if held is None else _read(held)
    if ledger is not None:
        expired = {
            entry.id: entry
            for group in (*ledger.all_exchanges, *ledger.all_unclosed)
            for entry in group
            if entry.expired_in is not None
        }
        lines = _without_expired(lines, expired)
    off_path = ledger.off_path if ledger is not None else (yield from read_off_path(harness))
    # The last turn a rewind did not leave behind: after one, the turn rewound to.
    parent = next(
        (
            line["turn"]
            for _, line in reversed(lines)
            if PARENT in line and line["turn"] not in off_path
        ),
        None,
    )
    added = _turn_lines(
        turn, parent, task=task, history=len(history), resumed=resumed, snapshot=snapshot
    )
    written = [raw for raw, _ in lines] + [json.dumps(one, ensure_ascii=False) for one in added]
    yield from harness.store(TURNS_FILE, ("\n".join(written) + "\n").encode("utf-8"))


def read_turn_log(harness: HarnessFiles) -> Steps[list[dict[str, Any]]]:
    """Every line of the log, as written; none where no turn wrote one."""
    held = yield from harness.fetch(TURNS_FILE)
    return [] if held is None else [line for _, line in _read(held)]


def logged_turns(harness: HarnessFiles) -> Steps[frozenset[str]]:
    """Every turn id the log names, on every branch."""
    lines = yield from read_turn_log(harness)
    return frozenset(line["turn"] for line in lines if isinstance(line.get("turn"), str))


#: Each turn's opening line and the lines after it, by its id.
Logged = Mapping[str, tuple[dict[str, Any], list[dict[str, Any]]]]


def by_turn(lines: Sequence[dict[str, Any]]) -> Logged:
    """Each turn's opening line and the lines after it, by its id."""
    turns: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    for line in lines:
        if PARENT in line and isinstance(line.get("turn"), str):
            turns[line["turn"]] = (line, [])
        elif line.get("turn") in turns:
            turns[line["turn"]][1].append(line)
    return turns


def path_to(turns: Logged, target: str) -> tuple[list[str], bool]:
    """The turns from the first to `target`, each the parent of the next, and whether the
    walk back came round to a turn already on it; the path then starts where it did."""
    path = [target]
    seen = {target}
    while isinstance(parent := turns[path[-1]][0].get(PARENT), str) and parent in turns:
        # Two turns under one id, which a session could log before such ids were refused:
        # the second's line takes the first's place, and the walk back comes round to it.
        if parent in seen:
            return list(reversed(path)), True
        path.append(parent)
        seen.add(parent)
    return list(reversed(path)), False


def replayed(turns: Logged, path: Sequence[str]) -> tuple[Message, ...]:
    """The conversation the last turn of `path` ended on, rebuilt from the log alone: each
    turn's parent's conversation kept to its `from`, its messages appended, and a summary
    standing for everything before its `cut`."""
    said: list[Message] = []
    for turn in path:
        opening, body = turns[turn]
        if isinstance(opening.get(FROM), int):
            said = said[: opening[FROM]]
        for line in body:
            if "role" not in line:
                continue
            message = from_record(line)
            cut = line.get(CUT)
            said = [message, *said[cut:]] if isinstance(cut, int) else [*said, message]
    return tuple(said)

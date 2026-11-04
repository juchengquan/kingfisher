"""Putting a session back where an earlier turn left it, keeping the turns after it."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import INPUTS_SEEN, TRANSCRIPT_FILE
from kingfisher.core.result import Rewound, TurnRecord

from .file_history import read_history, restore_state
from .model import START, RewindError, Start
from .store import (
    RESUMED,
    KeptLedger,
    clear_pause,
    pending_from_mark,
    read_ledger,
    read_pause_mark,
    write_off_path,
    write_transcript,
)
from .turnlog import IMPORTED, PARENT, TASK, Logged, by_turn, path_to, read_turn_log, replayed

if TYPE_CHECKING:
    from .steps import Steps


def turn_records(
    lines: list[dict[str, Any]], off_path: frozenset[str | None]
) -> tuple[TurnRecord, ...]:
    """Every turn the log holds, oldest first, each marked if it is on the session's path."""
    turns = by_turn(lines)
    # The turns ran in the order the log holds them, so the last one a rewind did not leave
    # behind is where the session is.
    head = [turn for turn in turns if turn not in off_path]
    current = set(path_to(turns, head[-1])) if head else set()
    return tuple(
        TurnRecord(
            id=turn,
            parent=opening.get(PARENT),
            task=opening.get(TASK),
            resumed=bool(opening.get(RESUMED)),
            imported=bool(opening.get(IMPORTED)),
            current=turn in current,
        )
        # A conversation imported under no turn has no id to be named by.
        for turn, (opening, _) in turns.items()
        if turn is not None
    )


def rewound(harness: Any, to: str | Start) -> Steps[Rewound]:
    """Put the session back where `to` left it, under a claim the caller holds."""
    turns = by_turn((yield from read_turn_log(harness)))
    ledger = yield from read_ledger(harness)
    if to is START:
        path: list[str | None] = []
    elif to in turns:
        path = path_to(turns, to)
    elif to in ledger.turns:
        msg = (
            f"turn {to!r} ran before this session kept a log of its turns, so there is "
            "nothing to rebuild its conversation from"
        )
        raise RewindError(msg)
    else:
        msg = f"this session has no turn {to!r}"
        raise RewindError(msg)
    mark = yield from read_pause_mark(harness)
    discarded = pending_from_mark(mark) if mark is not None else ()
    # Superseded, as a new request supersedes it: the pause was asked on a conversation the
    # session is no longer at, and a resume answering it would run calls from another one.
    if mark is not None:
        yield from clear_pause(harness)
    # The path before the conversation: a turn arriving after a write that died between the
    # two follows the turn rewound to, and finds the ledger's count of the conversation and
    # the conversation it reads apart, which it reports.
    yield from write_off_path(harness, _off_path(turns, path, ledger))
    said = replayed(turns, path) if path else ()
    if said:
        yield from write_transcript(harness, said)
    else:
        yield from harness.drop(TRANSCRIPT_FILE)
    yield from restore_state(harness, (yield from read_history(harness)), path, (INPUTS_SEEN,))
    return Rewound(to=None if to is START else to, discarded=discarded)


def _off_path(turns: Logged, path: list[str | None], ledger: KeptLedger) -> frozenset[str | None]:
    """Every turn the log holds that is not on `path`; and, where the conversation imported
    from before the log is not on it either, every turn the ledger closed before then."""
    on_path = set(path)
    off: set[str | None] = {turn for turn in turns if turn not in on_path}
    imported = next((turn for turn, (opening, _) in turns.items() if opening.get(IMPORTED)), None)
    if imported is not None and imported not in on_path:
        off |= _closed_up_to(ledger, imported)
    return frozenset(off)


def _closed_up_to(ledger: KeptLedger, turn: str) -> set[str | None]:
    """The turns the ledger closed up to and including `turn`, which ran before the log."""
    closed: set[str | None] = set()
    for line in ledger.lines:
        row = json.loads(line)
        if "id" not in row and isinstance(row.get("turn"), str):
            closed.add(row["turn"])
            if row["turn"] == turn:
                return closed
    return set()

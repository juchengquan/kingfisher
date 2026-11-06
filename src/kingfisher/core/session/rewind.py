"""Putting a session back where an earlier turn left it, keeping the turns after it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import INPUTS_SEEN, MEMORY_WRITER, TRANSCRIPT_FILE
from kingfisher.core.result import Rewound, TurnFiles, TurnRecord

from .file_history import BEFORE_FIRST, NO_RECORD, read_file_history, restore_files, restore_state
from .model import START, RewindError, Start
from .store import (
    RESUMED,
    Drift,
    HarnessFiles,
    clear_pause,
    pending_from_mark,
    read_ledger,
    read_pause_mark,
    write_drift,
    write_off_path,
    write_transcript,
)
from .turnlog import PARENT, TASK, by_turn, path_to, read_turn_log, replayed

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.layout import Layout

    from .steps import Steps


def turn_records(
    lines: list[dict[str, Any]],
    off_path: frozenset[str],
    files: Mapping[str, TurnFiles],
) -> tuple[TurnRecord, ...]:
    """Every turn the log holds, oldest first, each marked if it is on the session's path
    and with what it did to its files where the file history has its line."""
    turns = by_turn(lines)
    # The turns ran in the order the log holds them, so the last one a rewind did not leave
    # behind is where the session is.
    head = [turn for turn in turns if turn not in off_path]
    current = set(path_to(turns, head[-1])[0]) if head else set()
    return tuple(
        TurnRecord(
            id=turn,
            parent=opening.get(PARENT),
            task=opening.get(TASK),
            resumed=bool(opening.get(RESUMED)),
            current=turn in current,
            files=files.get(turn),
        )
        for turn, (opening, _) in turns.items()
    )


def rewound(files: Any, layout: Layout, to: str | Start) -> Steps[Rewound]:
    """Put the session back where `to` left it, under a claim the caller holds."""
    harness = HarnessFiles(files)
    turns = by_turn((yield from read_turn_log(harness)))
    ledger = yield from read_ledger(harness)
    path: list[str]
    if to is START:
        path = []
    elif to in turns:
        path, looped = path_to(turns, to)
        if looped:
            msg = (
                f"turn {to!r} cannot be rebuilt: two turns of this session ran under one id, "
                "and the turns before it lead back into each other"
            )
            raise RewindError(msg)
    elif to in ledger.turns:
        # Not "no turn": the ledger, written before the log, names a turn that died between
        # the two, or one the session ran before it kept a log.
        msg = f"turn {to!r} wrote no conversation to go back to"
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
    yield from write_off_path(harness, frozenset(turns.keys() - set(path)))
    said = replayed(turns, path) if path else ()
    if said:
        yield from write_transcript(harness, said)
    else:
        yield from harness.drop(TRANSCRIPT_FILE)
    records = yield from read_file_history(harness)
    yield from restore_state(harness, records, path, (INPUTS_SEEN, MEMORY_WRITER))
    restored, not_restored = yield from restore_files(files, harness, layout, records, path)
    # Told to the next turn only where its conversation remembers the files: after a rewind
    # to the start it remembers none, and finds them as they are.
    unsure = {NO_RECORD, BEFORE_FIRST}
    yield from write_drift(
        harness,
        Drift(
            changed=tuple(one for one, why in not_restored.items() if why not in unsure),
            unsure=tuple(one for one, why in not_restored.items() if why in unsure),
        )
        if path
        else Drift(),
    )
    return Rewound(
        to=None if to is START else to,
        discarded=discarded,
        restored=restored,
        not_restored=not_restored,
    )


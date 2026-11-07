"""Putting a session back where an earlier turn left it, keeping the turns after it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import ARTIFACTS_SEEN, INPUTS_SEEN, MEMORY_WRITER
from kingfisher.core.result import Rewound, TurnFiles, TurnRecord

from .file_history import read_file_history, restore_state, rewind_files
from .model import START, RewindError, RewindPart, Start
from .store import (
    RESUMED,
    HarnessFiles,
    clear_pause,
    pending_from_mark,
    read_ledger,
    read_off_path,
    read_pause_mark,
    write_drift,
    write_off_path,
)
from .turnlog import PARENT, by_turn, path_to, read_turn_log, task_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.layout import Layout

    from .steps import Steps
    from .turnlog import Logged


def current_path(turns: Logged, off_path: frozenset[str]) -> list[str]:
    """The turns from the first to where the session is, each the parent of the next."""
    # The turns ran in the order the log holds them, so the last one a rewind did not leave
    # behind is where the session is.
    head = [turn for turn in turns if turn not in off_path]
    return path_to(turns, head[-1])[0] if head else []


def turn_records(
    lines: list[dict[str, Any]],
    off_path: frozenset[str],
    files: Mapping[str, TurnFiles],
) -> tuple[TurnRecord, ...]:
    """Every turn the log holds, oldest first, each marked if it is on the session's path
    and with what it did to its files where the file history has its line."""
    turns = by_turn(lines)
    current = set(current_path(turns, off_path))
    return tuple(
        TurnRecord(
            id=turn,
            parent=opening.get(PARENT),
            task=task_of(body),
            resumed=bool(opening.get(RESUMED)),
            current=turn in current,
            files=files.get(turn),
        )
        for turn, (opening, body) in turns.items()
    )


def rewound(
    files: Any, layout: Layout, to: str | Start, only: RewindPart | None = None
) -> Steps[Rewound]:
    """Put the session back where `to` left it, or only its files or its conversation, under
    a claim the caller holds."""
    if only not in (None, "files", "conversation"):
        # Before anything is written: taken for neither, a misspelt part takes back both.
        msg = f"a rewind takes back 'files' or 'conversation' alone, or both, not {only!r}"
        raise ValueError(msg)
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
    # The files alone too: a resume carries no message to tell the agent they changed.
    if mark is not None:
        yield from clear_pause(harness)
    # The turn the next one follows, whose conversation remembers the files as it left them.
    remembered = (
        path if only != "files" else current_path(turns, (yield from read_off_path(harness)))
    )
    records = yield from read_file_history(harness)
    # Before the path, and written again with the files: a rewind dying between would leave
    # the next turn comparing the files with another turn's listing. Without one, it copies
    # every file it finds.
    yield from harness.drop(ARTIFACTS_SEEN)
    if only != "files":
        # After the pause and before the files: the next turn's conversation is rebuilt along
        # the path and the ledger counts only the turns on it, so a rewind dying after this
        # leaves the session at the turn rewound to, holding the files and the record of the
        # inputs the turns after it left, as a rewind that restored none would.
        yield from write_off_path(harness, frozenset(turns.keys() - set(path)))
        yield from restore_state(harness, records, path, (INPUTS_SEEN,))
    if only != "conversation":
        yield from restore_state(harness, records, path, (MEMORY_WRITER,))
    restored, not_restored, drift = yield from rewind_files(
        files,
        harness,
        layout,
        records,
        path=None if only == "conversation" else path,
        remembered=remembered,
    )
    yield from write_drift(harness, drift)
    return Rewound(
        to=None if to is START else to,
        discarded=discarded,
        restored=restored,
        not_restored=not_restored,
    )


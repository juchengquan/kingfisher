"""Putting a session back where an earlier turn left it, keeping the turns after it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import (
    ARTIFACTS_SEEN,
    HARNESS_ROUTE,
    INPUTS_SEEN,
    KEPT_FILES,
    MEMORY_WRITER,
)
from kingfisher.core.result import Rewound, TurnFiles, TurnRecord

from .copying import copied_through
from .file_history import (
    STATE_FOLDER,
    TURN,
    UNDER,
    artifacts_now,
    read_file_history,
    removed_in,
    state_in,
    unlisted_in,
    versions_in,
)
from .files import walk_artifacts
from .model import START, RewindError, RewindPart, Start
from .steps import changing
from .store import (
    RESUMED,
    Drift,
    HarnessFiles,
    clear_pause,
    pending_from_mark,
    read_ledger,
    read_pause_mark,
    write_drift,
    write_listing,
    write_off_path,
)
from .turnlog import PARENT, by_turn, path_to, read_log, task_of, times_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.core.layout import Layout

    from .steps import Steps
    from .turnlog import TurnLog


def turn_records(log: TurnLog, files: Mapping[str, TurnFiles]) -> tuple[TurnRecord, ...]:
    """Every turn the log holds, oldest first, each marked if it is on the session's path
    and with what it did to its files where the file history has its line."""
    turns = by_turn([line for _, line in log.lines])
    current = set(log.path)
    return tuple(
        TurnRecord(
            id=turn,
            parent=opening.get(PARENT),
            task=task_of(body),
            resumed=bool(opening.get(RESUMED)),
            current=turn in current,
            files=files.get(turn),
            started=started,
            ended=ended,
        )
        for turn, (opening, body) in turns.items()
        for started, ended in (times_of(opening, body),)
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
    log = yield from read_log(harness)
    turns = by_turn([line for _, line in log.lines])
    ledger = yield from read_ledger(harness, log.off_path)
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
    remembered = path if only != "files" else list(log.path)
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


def restore_state(
    harness: HarnessFiles,
    records: list[dict[str, Any]],
    path: list[str],
    names: tuple[str, ...],
) -> Steps[tuple[str, ...]]:
    """Put back each of `names` as the last turn of `path` to keep it left it, and drop one
    no turn of `path` kept; the names dropped.

    Dropped rather than left: what the agent heard of the inputs folder, or what the writer
    has not read, on another branch, is a conversation this one never had.
    """
    on_path = set(path)
    kept = [state_in(one) for one in records if one.get(TURN) in on_path]
    dropped = []
    for name in names:
        entry = next((one[name] for one in reversed(kept) if name in one), None)
        held = (
            (yield from harness.fetch(f"{KEPT_FILES}/{entry[UNDER]}/{STATE_FOLDER}/{name}"))
            if entry is not None
            else None
        )
        if held is None:
            yield from harness.drop(name)
            dropped.append(name)
        else:
            yield from harness.store(name, held)
    return tuple(dropped)


#: Why a file has no version to go back to, where no copy failed but a record is missing.
NO_RECORD = "no record says what the folders held when that turn ended"
BEFORE_FIRST = "nothing records what the folders held before the first turn"
COPIES_OFF = "a turn on the way kept no record of it, KINGFISHER_REWIND_MAX_BYTES being 0"


def rewind_files(  # noqa: PLR0913 -- the folders, both turns, and what records them
    files: Any,
    harness: HarnessFiles,
    layout: Layout,
    records: list[dict[str, Any]],
    *,
    path: list[str] | None,
    remembered: list[str],
) -> Steps[tuple[tuple[str, ...], dict[str, str], Drift]]:
    """Put the artifact folders back as the last turn of `path` left them, or leave them for
    `None`, and say which files are not as the last turn of `remembered`, whose conversation
    the next turn follows, left them: the paths put back, each that could not be with why,
    and those.

    The last listing becomes the folders as that turn left them, which the next turn compares
    with to find what it changed: compared with them as they are, it would record no file it
    did not touch, and a rewind back to it would rebuild such a file as another turn left it.
    A file found as that turn left it is listed as it is now, or a size and time a copy back
    changed would read as the next turn's change.
    """
    walked = yield from walk_artifacts(files, layout)
    now, _ = yield from artifacts_now(files, layout, walked)
    if now is None:
        cut = dict.fromkeys(layout.artifact_dirs, "a listing of it came back cut short")
        folders = tuple(sorted(layout.artifact_dirs)) if remembered else ()
        # The listing left dropped, the next turn copies every file it finds.
        return (), cut if path is not None else {}, Drift(unsure=folders)
    marks = {where: mark for where, (mark, _) in now.items()}
    restored: tuple[str, ...] = ()
    not_restored: dict[str, str] = {}
    if path is not None:
        restored, not_restored, marks = yield from _restore_files(
            files, layout, records, path, marks
        )
    memory_file = layout.memory_file_route.lstrip("/")
    then = _held_at(records, remembered, memory_file)
    if then is None:
        # The listing left dropped, the next turn copies every file it finds. After a rewind to
        # the start the conversation remembers no file, and finds them as they are; else
        # nothing says what it remembers.
        return restored, not_restored, Drift(unsure=tuple(sorted(marks)) if remembered else ())
    held, unknown = then
    listing = {where: mark for where, (_, mark, _) in held.items()}
    changed: list[str] = []
    # Named by its folder, not file by file: a folder of hundreds would fill the next turn's
    # message.
    unsure = [
        folder
        for folder in sorted(unknown)
        if any(_unknown_in(one, {folder}, memory_file) for one in marks)
    ]
    relisted: dict[str, str | None] | None = None
    for where in sorted({*marks, *listing}):
        if _unknown_in(where, unknown, memory_file):
            continue
        if where not in marks or where not in listing:
            changed.append(where)
            continue
        # A listing of names marks no file, and reading each back would cost a rewind what
        # every turn's end costs such a backend: told it may have changed, the agent reads it.
        if marks[where] is None or listing[where] is None:
            unsure.append(where)
        elif marks[where] != listing[where]:
            changed.append(where)
        elif where in restored:
            if relisted is None:
                rewalked = yield from walk_artifacts(files, layout)
                again, _ = yield from artifacts_now(files, layout, rewalked)
                relisted = {one: mark for one, (mark, _) in (again or {}).items()}
            listing[where] = relisted.get(where) or listing[where]
    yield from write_listing(harness, ARTIFACTS_SEEN, listing)
    return restored, not_restored, Drift(changed=tuple(changed), unsure=tuple(unsure))


def _restore_files(
    files: Any,
    layout: Layout,
    records: list[dict[str, Any]],
    path: list[str],
    marks: dict[str, str | None],
) -> Steps[tuple[tuple[str, ...], dict[str, str], dict[str, str | None]]]:
    """Put each file in the artifact folders, listed with `marks`, back as the last turn of
    `path` left it: the paths put back, a file that turn did not have removed among them,
    each path that could not be, with why, and the files there after, each put back marked
    as that turn recorded it.

    A file whose version there is unknown is left as it is: rewritten by a guess, it would
    be lost to every later rewind as well.
    """
    memory_file = layout.memory_file_route.lstrip("/")
    found = _held_at(records, path, memory_file)
    if found is None:
        return (), dict.fromkeys(sorted(marks), NO_RECORD if path else BEFORE_FIRST), marks
    held, unknown = found
    after = dict(marks)
    restored: list[str] = []
    not_restored: dict[str, str] = {}
    # Named by its folder, not file by file: a folder of hundreds would fill the next turn's
    # message.
    for folder in sorted(unknown):
        if any(_unknown_in(one, {folder}, memory_file) for one in marks):
            not_restored[folder] = COPIES_OFF
    for where in sorted({*held, *marks}):
        if _unknown_in(where, unknown, memory_file):
            continue
        version = held.get(where)
        done, why = yield from _put_back(files, where, version, marks.get(where))
        if done:
            restored.append(where)
            if version is None:
                del after[where]
            else:
                after[where] = version[1]
        elif why is not None:
            not_restored[where] = why
    return tuple(restored), not_restored, after


def _put_back(
    files: Any,
    where: str,
    version: tuple[str | None, str | None, str | None] | None,
    mark_now: str | None,
) -> Steps[tuple[bool, str | None]]:
    """Put `where` back as `version`, `None` for a turn that did not have it: whether it
    changed, and why not where it could not."""
    if version is None:
        # Whether or not a copy holds it, as a file rewritten is: the folders are put back as
        # the turn left them, and a version too large to copy is the deployment's to keep.
        yield changing(files, "delete", f"/{where}")
        return True, None
    under, mark, why = version
    if mark is not None and mark_now == mark:
        return False, None
    if under is None:
        return False, why or "its version has no copy"
    _, refused, _ = yield from copied_through(
        files, f"{HARNESS_ROUTE}{KEPT_FILES}/{under}/{where}", f"/{where}", None
    )
    return (True, None) if refused is None else (False, refused)


def _held_at(
    records: list[dict[str, Any]], path: list[str], memory_file: str
) -> tuple[dict[str, tuple[str | None, str | None, str | None]], set[str]] | None:
    """Each file the artifact folders held when the last turn of `path` ended, as the
    folder of its copy or `None`, its mark, and why there is no copy, and the folders whose
    files besides the memory file are unknown there; `None` where every file is unknown,
    because that turn or one before it since the first to keep a record has none.
    """
    by_turn = {record[TURN]: record for record in records}
    first = next((at for at, turn in enumerate(path) if turn in by_turn), None)
    if first is None or any(turn not in by_turn for turn in path[first:]):
        return None
    held: dict[str, tuple[str | None, str | None, str | None]] = {}
    unknown: set[str] = set()
    for turn in path[first:]:
        record = by_turn[turn]
        if unlisted := unlisted_in(record):
            unknown |= unlisted
            held = {where: one for where, one in held.items()
                    if not _unknown_in(where, unlisted, memory_file)}
        else:
            # A turn listing the folders after one that did not names every file in them,
            # what it compared against holding the memory file alone.
            unknown = set()
        for gone in removed_in(record):
            held.pop(gone, None)
        held.update(versions_in(record))
    return held, unknown


def _unknown_in(where: str, folders: set[str], memory_file: str) -> bool:
    """Whether `where` is one of the files a turn at 0 did not list in `folders`."""
    return where != memory_file and where.partition("/")[0] in folders

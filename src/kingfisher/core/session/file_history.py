"""What each turn left in the artifact folders, kept under `/.harness` so a session can be
put back where any turn left it: a copy of each version, and a line a turn saying which."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
from collections.abc import Mapping
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.permitted_backend import host_path
from kingfisher.core.layout import (
    ARTIFACTS_SEEN,
    FILE_HISTORY,
    HARNESS_ROUTE,
    INPUTS_SEEN,
    KEPT_FILES,
    MEMORY_WRITER,
)
from kingfisher.core.logged import raised, warn

from .files import artifacts_seen
from .memory import without_any
from .steps import Steps, changing, on_host, reading
from .store import HarnessFiles, read_listing, read_off_path, write_listing
from .turnlog import PARENT, read_turn_log

if TYPE_CHECKING:
    from pathlib import Path

    from kingfisher.core.config import Config
    from kingfisher.core.layout import Layout
    from kingfisher.core.result import MemoryEntry

    from .turn import Prepared

# The keys of a line in `FILE_HISTORY`, which a rewind replays in the order the turns ran.
TURN = "turn"
#: The folder under `KEPT_FILES` holding the turn's copies, as `folder_for` named it.
UNDER = "under"
#: Each version the turn copied, by its path in the session: `{MARK, BYTES}`, and
#: `DROPPED_IN` once a later turn removed the copy for space.
KEPT = "kept"
#: Each version the turn found and has no copy of: `{MARK, WHY}`.
NOT_KEPT = "not_kept"
#: The paths there when the last turn ended and gone when this one did.
REMOVED = "removed"
#: Each of `STATE_KEPT` as the turn left it, by name: `{UNDER, SHA}`, `UNDER` the folder
#: holding its copy, an earlier turn's where it was the same.
STATE = "state"
SHA = "sha256"
MARK = "mark"
BYTES = "bytes"
WHY = "why"
DROPPED_IN = "dropped_in"

#: Why a version has no copy, where kingfisher decided it rather than a copy failing.
LATE = "the turn ended past its deadline, when its claim may have no time left for a copy"
HELD_BY_STORE = "a MemoryStore keyed the turn, so the memory file was a copy of the user's"
OVER_BUDGET = "it alone is larger than KINGFISHER_REWIND_MAX_BYTES"

#: The reasons a later turn would meet again, so it is not asked to try: the rest are asked
#: again by the next turn, which copies the version if it is still there.
_FINAL = frozenset({HELD_BY_STORE, OVER_BUDGET})

#: A turn id used as its own folder: one segment, and none a case-folding disk would take
#: for another's.
_PLAIN = re.compile(r"[a-z0-9-]{1,64}")

#: What the harness keeps of a session's past that a rewind puts back with its
#: conversation: the inputs folder as the agent last heard of it, and what the memory
#: writer has not read yet.
STATE_KEPT = (INPUTS_SEEN, MEMORY_WRITER)
#: Beside a turn's copies: a folder name no artifact folder can take, a layout's names
#: being plain segments.
STATE_FOLDER = ".state"
_DIGESTED = re.compile(r"_[0-9a-f]{32}")

# Not blocking, or a FIFO left under a listed name would hold the ending on its open.
_SOURCE = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CHUNK = 1024 * 1024
# macOS's value, which Linux's is not: only the macOS clone may use it.
_AT_FDCWD = -2
_FICLONE = 0x40049409


def folder_for(turn: str) -> str:
    """Where `turn`'s copies go under `KEPT_FILES`: its id where that is a plain lower-case
    segment, else a digest of it, which no plain id can spell.

    A caller names its own turns, and `T1` beside `t1` would share a folder on macOS.
    """
    if _PLAIN.fullmatch(turn):
        return turn
    return f"_{hashlib.sha256(turn.encode('utf-8')).hexdigest()[:32]}"


def read_file_history(harness: HarnessFiles) -> Steps[list[dict[str, Any]]]:
    """Every line of the history, oldest turn first; none where no turn wrote one."""
    held = yield from harness.fetch(FILE_HISTORY)
    if held is None:
        return []
    records: list[dict[str, Any]] = []
    for line in held.decode("utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            # A backend has no rename, so a crash mid-write cuts a line, as the ledger's.
            continue
        if isinstance(record, dict) and isinstance(record.get(TURN), str):
            records.append(record)
    return records


def _under(record: dict[str, Any]) -> str | None:
    """The record's folder, or `None` for one no `folder_for` gave."""
    under = record.get(UNDER)
    if isinstance(under, str) and (_PLAIN.fullmatch(under) or _DIGESTED.fullmatch(under)):
        return under
    return None


def _entries(record: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    held = record.get(key)
    if not isinstance(held, dict):
        return {}
    return {path: one for path, one in held.items() if isinstance(one, dict)}


def _named(record: dict[str, Any]) -> set[str]:
    """Every path the record says anything of."""
    removed = record.get(REMOVED)
    gone = [path for path in removed if isinstance(path, str)] if isinstance(removed, list) else []
    return {*_entries(record, KEPT), *_entries(record, NOT_KEPT), *gone}


def _held(records: list[dict[str, Any]]) -> list[tuple[int, str, int]]:
    """`(line, path, bytes)` for each copy the history still holds.

    A size the backend's listing did not give counts as nothing.
    """
    held = []
    for at, record in enumerate(records):
        if _under(record) is None:
            continue
        for path, kept in _entries(record, KEPT).items():
            if kept.get(DROPPED_IN) is None:
                size = kept.get(BYTES)
                held.append((at, path, size if isinstance(size, int) else 0))
    return held


def kept_bytes(harness: HarnessFiles) -> Steps[int]:
    """What this session's copies of earlier versions come to, by its own history.

    A clone counts its whole size although it shares its blocks: subtracted from a size that
    counts it whole too, it is not counted twice, and against the budget it overcounts,
    which keeps a session under it.
    """
    return sum(size for _, _, size in _held((yield from read_file_history(harness))))


def dropped_for_space(
    records: list[dict[str, Any]],
    budget: int | None,
    parents: Mapping[str, str | None] | None = None,
) -> list[tuple[int, str]]:
    """The copies to remove, as `(line, path)`, to bring what they hold within `budget`.

    By the newest turn each can still restore, oldest first. A copy restores its own turn
    and every one after until its file next changes or goes, and one whose file has not
    since is the version there now, so it goes last. Not by age: a file written once in
    the first turn would go first, though every rewind needs it.

    `parents`, each turn's, where a rewind has made the turns a tree: then "after" is down
    every branch from the copy's turn, not later in the order they ran, or a version still
    current on the path would go first for having changed on a branch left behind.
    """
    if budget is None:
        return []
    mentioned: dict[str, list[int]] = {}
    for at, record in enumerate(records):
        for path in _named(record):
            mentioned.setdefault(path, []).append(at)
    ranked = []
    for at, path, size in _held(records):
        if parents is not None:
            restores_up_to = _reach(records, at, path, parents)
        else:
            later = [one for one in mentioned[path] if one > at]
            restores_up_to = later[0] - 1 if later else len(records)
        ranked.append((restores_up_to, at, path, size))
    over = sum(size for *_, size in ranked) - budget
    dropped = []
    for _, at, path, size in sorted(ranked):
        if over <= 0:
            break
        dropped.append((at, path))
        over -= size
    return dropped


def keep_turn_files(prepared: Prepared, cfg: Config) -> Steps[None]:
    """Copy each file in the artifact folders that changed since the last turn ended, and add
    this turn's line to the history: what it kept, what it could not and why, what went."""
    turn, harness = prepared.turn.id, prepared.harness
    now = yield from artifacts_seen(prepared.files, cfg.layout)
    if now is None:
        # No line rather than a wrong one: each file the listing missed would read as
        # removed, and a rewind would delete it.
        warn(
            "turn_files_not_kept",
            f"the files turn {turn} left were not kept: a listing came back cut short",
        )
        return
    before = (yield from read_listing(harness, ARTIFACTS_SEEN)) or {}
    records = yield from read_file_history(harness)
    under = folder_for(turn)
    # Once, before any copy: until the deadline the claim has a whole superstep's bound left,
    # which no copy on this host comes near, and past it as little as nothing.
    late = monotonic() > prepared.deadline
    kept: dict[str, dict[str, Any]] = {}
    not_kept: dict[str, dict[str, Any]] = {}
    for path, (mark, size) in sorted(now.items()):
        # A file whose listing gives no mark is copied every turn: it cannot be told unchanged.
        if mark is not None and before.get(path) == mark:
            continue
        why = LATE if late else _not_to_copy(path, size, prepared, cfg)
        if why is None:
            copied, why = yield from _copied(
                prepared.files, f"/{path}", f"{HARNESS_ROUTE}{KEPT_FILES}/{under}/{path}", size
            )
            if why is None:
                kept[path] = {MARK: mark, BYTES: copied}
                continue
            # Not the file, which the agent named, nor `why`, which may quote the backend: the
            # history line keeps both, inside the session.
            folder = path.partition("/")[0]
            warn(
                "turn_file_not_copied",
                f"turn {turn} kept no copy of a file under /{folder}; its line in "
                f"{FILE_HISTORY} says why",
                folder=folder,
            )
        not_kept[path] = {MARK: mark, WHY: why}
    state = yield from _kept_state(harness, records, under)
    records.append(
        _line(
            turn,
            under,
            kept=kept,
            not_kept=not_kept,
            removed=sorted(before.keys() - now.keys()),
            state=state,
        )
    )
    # The tree only where a rewind made one: until then the order the turns ran is it.
    parents = (
        {
            line["turn"]: line.get(PARENT)
            for line in (yield from read_turn_log(harness))
            if PARENT in line and isinstance(line.get("turn"), str)
        }
        if cfg.rewind_max_bytes is not None and (yield from read_off_path(harness))
        else None
    )
    dropped = dropped_for_space(records, cfg.rewind_max_bytes, parents)
    for at, path in dropped:
        records[at][KEPT][path][DROPPED_IN] = turn
    # Patched as parsed, so a key another version wrote survives.
    lines = "".join(f"{json.dumps(one, ensure_ascii=False)}\n" for one in records)
    yield from harness.store(FILE_HISTORY, lines.encode("utf-8"))
    # Once the history no longer names them: removed first, a crash would leave it promising
    # a copy that is not there.
    for at, path in dropped:
        yield from harness.drop(f"{KEPT_FILES}/{records[at][UNDER]}/{path}")
    # A version this turn failed to copy is left unmarked, so the next turn tries again.
    seen = {
        path: None if path in not_kept and not_kept[path][WHY] not in _FINAL else mark
        for path, (mark, _) in now.items()
    }
    if seen != before:
        yield from write_listing(harness, ARTIFACTS_SEEN, seen)


def _reach(
    records: list[dict[str, Any]], at: int, path: str, parents: Mapping[str, str | None]
) -> int:
    """The newest line whose turn the copy on line `at` still restores, down every branch
    from its turn until `path` next changes there: the last line, the turn now ending,
    where it is that turn's version, which no other copy outranks."""
    children: dict[str | None, list[str]] = {}
    for turn, parent in parents.items():
        children.setdefault(parent, []).append(turn)
    line = {record[TURN]: number for number, record in enumerate(records)}
    newest = at
    waiting = list(children.get(records[at][TURN], ()))
    while waiting:
        turn = waiting.pop()
        number = line.get(turn)
        if number is not None and path in _named(records[number]):
            continue
        if number is not None:
            newest = max(newest, number)
        waiting.extend(children.get(turn, ()))
    return newest


def _not_to_copy(path: str, size: int | None, prepared: Prepared, cfg: Config) -> str | None:
    """Why this version gets no copy, before one is tried, or `None`."""
    if prepared.memory_key is not None and path == cfg.layout.memory_file_route.lstrip("/"):
        # The store loads the user's memory over the file at every turn's start, so a copy
        # could restore nothing, and it would hold the user's memory out of the store's reach.
        return HELD_BY_STORE
    if cfg.rewind_max_bytes is not None and (size or 0) > cfg.rewind_max_bytes:
        return OVER_BUDGET
    return None


def _line(  # noqa: PLR0913 -- a line's keys, each by name
    turn: str,
    under: str,
    *,
    kept: dict[str, dict[str, Any]],
    not_kept: dict[str, dict[str, Any]],
    removed: list[str],
    state: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """A turn's line in the history, each key only where it holds something."""
    line: dict[str, Any] = {TURN: turn, UNDER: under}
    for key, held in ((KEPT, kept), (NOT_KEPT, not_kept), (REMOVED, removed), (STATE, state)):
        if held:
            line[key] = held
    return line


def _copied(
    backend: Any, source: str, destination: str, size: int | None
) -> Steps[tuple[int | None, str | None]]:
    """What the copy of `source` at `destination` came to, or why there is none.

    On this host where the backend names both ends here; else by the backend's own `copy`;
    else read and written whole, which holds the file in this process's memory.
    """
    whence = yield on_host(_on_this_host, backend, source)
    where = yield on_host(_on_this_host, backend, destination)
    if whence is not None and where is not None:
        try:
            return (yield on_host(copied_on_host, whence, where)), None
        except OSError as exc:
            return None, exc.strerror or str(exc)
    own = getattr(backend, "copy", None)
    if callable(own):
        try:
            refused = yield on_host(own, source, destination)
        except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
            return None, f"{type(backend).__name__}.copy raised {type(exc).__name__}: {exc}"
        return (None, str(refused)) if refused else (size, None)
    (got,) = yield reading(backend, "download_files", [source])
    if got.error or got.content is None:
        return None, got.error or "the backend handed back no content"
    (put,) = yield changing(backend, "upload_files", [(destination, got.content)])
    return (None, put.error) if put.error else (len(got.content), None)


def _on_this_host(backend: Any, path: str) -> tuple[Path | None, Path] | None:
    try:
        return host_path(backend, path)
    except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
        error_type, error_at = raised(exc)
        warn(
            "host_path_failed",
            f"{type(backend).__name__}.host_path raised {error_type} at {error_at}",
            plugin=type(backend).__name__,
            method="host_path",
            error_type=error_type,
            error_at=error_at,
        )
        return None


def copied_on_host(source: tuple[Path | None, Path], destination: tuple[Path | None, Path]) -> int:
    """Copy one file on this host, as a clone where the filesystem makes one; its size.

    Never through a link: the shell may leave one in `/outputs` naming any file this process
    can read, and a rewind would hand what it named back to the agent.
    """
    root, path = source
    # As `PermittedBackend.on_this_host` judges a tool's path: a root that answered is
    # resolved, so anything else is a link below it.
    if root is not None and path.resolve() != path:
        raise OSError(errno.ELOOP, "it is or runs through a link", str(path))
    target = destination[1]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    held = os.open(path, _SOURCE)
    try:
        found = os.fstat(held)
        if not stat.S_ISREG(found.st_mode):
            raise OSError(errno.EINVAL, "it is not a regular file", str(path))
        try:
            _clone(held, target)
        except OSError:
            # Refused by a filesystem that makes no clones, as ext4 and tmpfs refuse it.
            target.unlink(missing_ok=True)
            with os.fdopen(os.dup(held), "rb") as read, target.open("xb") as write:
                shutil.copyfileobj(read, write, _CHUNK)
        return found.st_size
    finally:
        os.close(held)


def _clone(source: int, target: Path) -> None:
    """Make `target` share the open file `source`'s blocks until either changes, or raise
    `OSError`."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.fclonefileat(source, _AT_FDCWD, os.fsencode(target), 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        return
    if sys.platform == "linux":
        import fcntl  # noqa: PLC0415 -- POSIX only, and asked only here

        made = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            fcntl.ioctl(made, _FICLONE, source)
        finally:
            os.close(made)
        return
    raise OSError(errno.ENOTSUP, "no clones on this platform", str(target))


def forget_in_copies(harness: HarnessFiles, layout: Layout, entry: MemoryEntry) -> Steps[None]:
    """Take `entry` out of every kept copy of the memory file, so no rewind brings back what a
    caller forgot."""
    memory_file = layout.memory_file_route.lstrip("/")
    for record in (yield from read_file_history(harness)):
        under = _under(record)
        # A dropped copy too: its removal may have failed, and the shell can read what is left.
        if under is None or memory_file not in _entries(record, KEPT):
            continue
        name = f"{KEPT_FILES}/{under}/{memory_file}"
        held = yield from harness.fetch(name)
        if held is None:
            continue
        text = held.decode("utf-8", errors="replace")
        if (without := without_any(text, entry)) != text:
            yield from harness.store(name, without.encode("utf-8"))


def _kept_state(
    harness: HarnessFiles, records: list[dict[str, Any]], under: str
) -> Steps[dict[str, dict[str, str]]]:
    """Each of `STATE_KEPT` as this turn left it, copied beside its files unless the last
    copy of it is the same, which is named instead."""
    kept: dict[str, dict[str, str]] = {}
    for name in STATE_KEPT:
        held = yield from harness.fetch(name)
        if held is None:
            continue
        digest = hashlib.sha256(held).hexdigest()
        last = next((one for one in map(_state_of, reversed(records)) if name in one), {})
        if last.get(name, {}).get(SHA) == digest:
            kept[name] = last[name]
            continue
        yield from harness.store(f"{KEPT_FILES}/{under}/{STATE_FOLDER}/{name}", held)
        kept[name] = {UNDER: under, SHA: digest}
    return kept


def _state_of(record: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The record's kept state, by name, each with a folder `folder_for` could have named."""
    held = record.get(STATE)
    if not isinstance(held, dict):
        return {}
    return {
        name: one
        for name, one in held.items()
        if isinstance(one, dict)
        and isinstance(one.get(UNDER), str)
        and (_PLAIN.fullmatch(one[UNDER]) or _DIGESTED.fullmatch(one[UNDER]))
    }


def restore_state(
    harness: HarnessFiles,
    records: list[dict[str, Any]],
    path: list[str | None],
    names: tuple[str, ...],
) -> Steps[tuple[str, ...]]:
    """Put back each of `names` as the last turn of `path` to keep it left it, and drop one
    no turn of `path` kept; the names dropped.

    Dropped rather than left: what the agent heard of the inputs folder, or what the writer
    has not read, on another branch, is a conversation this one never had.
    """
    on_path = set(path)
    kept = [_state_of(one) for one in records if one.get(TURN) in on_path]
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


def restore_files(
    files: Any,
    harness: HarnessFiles,
    layout: Layout,
    records: list[dict[str, Any]],
    path: list[str | None],
) -> Steps[tuple[tuple[str, ...], dict[str, str]]]:
    """Put each file in the artifact folders back as the last turn of `path` left it: the
    paths put back, a file that turn did not have removed among them, and each path that
    could not be, with why.

    A file whose version there is unknown is left as it is: removed or rewritten on a
    guess, it would be lost to every later rewind as well.
    """
    now = yield from artifacts_seen(files, layout)
    if now is None:
        return (), dict.fromkeys(layout.artifact_dirs, "a listing of it came back cut short")
    held = _held_at(records, path)
    if held is None:
        return (), dict.fromkeys(sorted(now), NO_RECORD if path else BEFORE_FIRST)
    restored: list[str] = []
    not_restored: dict[str, str] = {}
    for where in sorted({*held, *now}):
        if where not in held:
            yield changing(files, "delete", f"/{where}")
            restored.append(where)
            continue
        under, mark, why = held[where]
        if where in now and mark is not None and now[where][0] == mark:
            continue
        if under is None:
            not_restored[where] = why or "its version has no copy"
            continue
        _, refused = yield from _copied(
            files, f"{HARNESS_ROUTE}{KEPT_FILES}/{under}/{where}", f"/{where}", None
        )
        if refused is None:
            restored.append(where)
        else:
            not_restored[where] = refused
    # As the folders are now, so the next turn copies only what it changes after this.
    after = yield from artifacts_seen(files, layout)
    if after is not None:
        listed = {one: mark for one, (mark, _) in after.items()}
        yield from write_listing(harness, ARTIFACTS_SEEN, listed)
    return tuple(restored), not_restored


def _held_at(
    records: list[dict[str, Any]], path: list[str | None]
) -> dict[str, tuple[str | None, str | None, str | None]] | None:
    """Each file the artifact folders held when the last turn of `path` ended, as the
    folder of its copy or `None`, its mark, and why there is no copy; `None` where that is
    unknown, because that turn or one before it since the first to keep a record has none.
    """
    by_turn = {record[TURN]: record for record in records}
    first = next((at for at, turn in enumerate(path) if turn in by_turn), None)
    if first is None or any(turn not in by_turn for turn in path[first:]):
        return None
    held: dict[str, tuple[str | None, str | None, str | None]] = {}
    for turn in path[first:]:
        record = by_turn[turn]
        removed = record.get(REMOVED)
        for gone in removed if isinstance(removed, list) else ():
            held.pop(gone, None)
        under = _under(record)
        for where, kept in _entries(record, KEPT).items():
            dropped = kept.get(DROPPED_IN)
            if dropped is not None:
                why = f"its copy was dropped for space in turn {dropped}"
                held[where] = (None, kept.get(MARK), why)
            else:
                held[where] = (under, kept.get(MARK), None if under else "its copy has no folder")
        for where, missing in _entries(record, NOT_KEPT).items():
            held[where] = (None, missing.get(MARK), str(missing.get(WHY) or "it has no copy"))
    return held

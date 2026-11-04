"""What each turn left in the artifact folders, kept under `/.harness` so a session can be
put back where any turn left it: a copy of each version, and a line a turn saying which."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import logging
import os
import re
import shutil
import stat
import sys
from time import monotonic
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.permitted_backend import host_path
from kingfisher.core.layout import ARTIFACTS_SEEN, FILE_HISTORY, HARNESS_ROUTE, KEPT_FILES
from kingfisher.core.logged import raised

from .files import artifacts_seen
from .memory import without_any
from .steps import Steps, changing, on_host, reading
from .store import read_listing, write_listing

if TYPE_CHECKING:
    from pathlib import Path

    from kingfisher.core.config import Config
    from kingfisher.core.layout import Layout
    from kingfisher.core.result import MemoryEntry

    from .turn import Prepared

_log = logging.getLogger(__name__)

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


def read_history(harness: Any) -> Steps[list[dict[str, Any]]]:
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


def kept_bytes(harness: Any) -> Steps[int]:
    """What this session's copies of earlier versions come to, by its own history.

    A clone counts its whole size although it shares its blocks: subtracted from a size that
    counts it whole too, it is not counted twice, and against the budget it overcounts,
    which keeps a session under it.
    """
    return sum(size for _, _, size in _held((yield from read_history(harness))))


def dropped_for_space(
    records: list[dict[str, Any]], budget: int | None
) -> list[tuple[int, str]]:
    """The copies to remove, as `(line, path)`, to bring what they hold within `budget`.

    By the newest turn each can still restore, oldest first. A copy restores its own turn
    and every one after until its file next changes or goes, and one whose file has not
    since is the version there now, so it goes last. Not by age: a file written once in
    the first turn would go first, though every rewind needs it.
    """
    if budget is None:
        return []
    mentioned: dict[str, list[int]] = {}
    for at, record in enumerate(records):
        for path in _named(record):
            mentioned.setdefault(path, []).append(at)
    ranked = []
    for at, path, size in _held(records):
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
        _log.warning("the files turn %s left were not kept: a listing came back cut short", turn)
        return
    before = (yield from read_listing(harness, ARTIFACTS_SEEN)) or {}
    records = yield from read_history(harness)
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
            _log.warning(
                "turn %s kept no copy of a file under /%s; its line in %s says why",
                turn,
                path.partition("/")[0],
                FILE_HISTORY,
            )
        not_kept[path] = {MARK: mark, WHY: why}
    records.append(_line(turn, under, kept, not_kept, sorted(before.keys() - now.keys())))
    dropped = dropped_for_space(records, cfg.rewind_max_bytes)
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


def _not_to_copy(path: str, size: int | None, prepared: Prepared, cfg: Config) -> str | None:
    """Why this version gets no copy, before one is tried, or `None`."""
    if prepared.memory_key is not None and path == cfg.layout.memory_file_route.lstrip("/"):
        # The store loads the user's memory over the file at every turn's start, so a copy
        # could restore nothing, and it would hold the user's memory out of the store's reach.
        return HELD_BY_STORE
    if cfg.rewind_max_bytes is not None and (size or 0) > cfg.rewind_max_bytes:
        return OVER_BUDGET
    return None


def _line(
    turn: str,
    under: str,
    kept: dict[str, dict[str, Any]],
    not_kept: dict[str, dict[str, Any]],
    removed: list[str],
) -> dict[str, Any]:
    """A turn's line in the history, each key only where it holds something."""
    line: dict[str, Any] = {TURN: turn, UNDER: under}
    for key, held in ((KEPT, kept), (NOT_KEPT, not_kept), (REMOVED, removed)):
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
        _log.warning("%s.host_path raised %s at %s", type(backend).__name__, *raised(exc))
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


def forget_in_copies(harness: Any, layout: Layout, entry: MemoryEntry) -> Steps[None]:
    """Take `entry` out of every kept copy of the memory file, so no rewind brings back what a
    caller forgot."""
    memory_file = layout.memory_file_route.lstrip("/")
    for record in (yield from read_history(harness)):
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

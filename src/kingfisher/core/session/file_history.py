"""What each turn left in the artifact folders, kept under `/.harness` so a session can be
put back where any turn left it: a copy of each version, and a line a turn saying which."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from contextlib import suppress
from time import monotonic
from typing import TYPE_CHECKING, Any, NamedTuple

from kingfisher.core.layout import (
    ARTIFACTS_SEEN,
    FILE_HISTORY,
    HARNESS_ROUTE,
    INPUTS_SEEN,
    KEPT_FILES,
    MEMORY_WRITER,
)
from kingfisher.core.logged import warn
from kingfisher.core.result import TurnFiles

from .copying import SAME, UNCOUNTED, content_mark, copied_through
from .files import Seen, Walked
from .memory import without_any
from .memory_writer import unread_dropped
from .steps import Steps, reading
from .store import HarnessFiles, read_listing, write_listing
from .turnlog import turn_parents

if TYPE_CHECKING:
    from kingfisher.core.config import Config
    from kingfisher.core.layout import Layout
    from kingfisher.core.result import MemoryEntry

    from .turn import Prepared

# The keys of a line in `FILE_HISTORY`, which a rewind replays in the order the turns ran.
TURN = "turn"
#: The folder under `KEPT_FILES` holding the turn's copies, as `folder_for` named it.
UNDER = "under"
#: Each version the turn copied, by its path in the session: `{MARK, BYTES}`, and
#: `DROPPED_IN` once a later turn removed the copy for space, or `OUTSIDE_BUDGET` for the
#: memory file's, which no budget removes.
KEPT = "kept"
#: Each version the turn found and has no copy of: `{MARK, WHY}`.
NOT_KEPT = "not_kept"
#: The paths there when the last turn ended and gone when this one did.
REMOVED = "removed"
#: The artifact folders the turn did not list, `KINGFISHER_REWIND_MAX_BYTES` being 0: of
#: their files only the memory file is named, and the rest are unknown at this turn.
UNLISTED = "unlisted"
#: Set where the turn compared the folders with nothing, so its line names every file it
#: listed: a rewind can start from it, whatever the turns before it recorded.
WHOLE = "whole"
#: Each of `STATE_KEPT` as the turn left it, by name: `{UNDER, SHA}`, `UNDER` the folder
#: holding its copy, an earlier turn's where it was the same.
STATE = "state"
SHA = "sha256"
MARK = "mark"
BYTES = "bytes"
WHY = "why"
DROPPED_IN = "dropped_in"
OUTSIDE_BUDGET = "outside_budget"

#: Why a version has no copy, where kingfisher decided it rather than a copy failing.
LATE = "the turn ended past its deadline, when its claim may have no time left for a copy"
HELD_BY_STORE = "a MemoryStore keyed the turn, so the memory file was a copy of the user's"
OVER_BUDGET = "it alone is larger than KINGFISHER_REWIND_MAX_BYTES"
UNREAD_MEMORY = "the memory file could not be read"

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


def _a_folder(under: Any) -> bool:
    """Whether `under` is a folder `folder_for` could have named."""
    return isinstance(under, str) and bool(_PLAIN.fullmatch(under) or _DIGESTED.fullmatch(under))


def _under(record: dict[str, Any]) -> str | None:
    """The record's folder, or `None` for one no `folder_for` gave."""
    under = record.get(UNDER)
    return under if _a_folder(under) else None


def _entries(record: dict[str, Any], key: str) -> dict[str, dict[str, Any]]:
    held = record.get(key)
    if not isinstance(held, dict):
        return {}
    return {path: one for path, one in held.items() if isinstance(one, dict)}


def _named(record: dict[str, Any]) -> set[str]:
    """Every path the record says anything of."""
    return {*_entries(record, KEPT), *_entries(record, NOT_KEPT), *removed_in(record)}


def _held(records: list[dict[str, Any]]) -> list[tuple[int, str, int]]:
    """`(line, path, bytes)` for each copy the history still holds.

    A size the backend's listing did not give counts as nothing.
    """
    held = []
    for at, record in enumerate(records):
        if _under(record) is None:
            continue
        for path, kept in _entries(record, KEPT).items():
            if kept.get(DROPPED_IN) is None and not kept.get(OUTSIDE_BUDGET):
                size = kept.get(BYTES)
                held.append((at, path, size if isinstance(size, int) else 0))
    return held


def kept_bytes(harness: HarnessFiles) -> Steps[int]:
    """What this session's copies of earlier versions come to, by its own history, the
    memory file's left out as the budget leaves them out.

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
    tree = None if parents is None else _tree(records, parents)
    ranked = []
    for at, path, size in _held(records):
        if tree is not None:
            restores_up_to = _reach(records, at, path, tree)
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


def keep_turn_files(prepared: Prepared, cfg: Config, walked: Walked) -> Steps[None]:
    """Copy each file in the artifact folders that changed since the last turn ended, and add
    this turn's line to the history: what it kept, what it could not and why, what went.
    `walked` is the folders as the turn's end found them."""
    turn, harness = prepared.turn.id, prepared.harness
    # At 0 nothing the agent made is copied, so the listing goes unused: on a backend listing
    # no marks, each file would otherwise be read back to be told unchanged.
    off = cfg.rewind_max_bytes == 0
    now, memory = yield from artifacts_now(prepared.files, cfg.layout, None if off else walked)
    if now is None:
        # No line rather than a wrong one: each file the listing missed would read as
        # removed, and a rewind would delete it.
        warn(
            "turn_files_not_kept",
            f"the files turn {turn} left were not kept: a listing came back cut short",
        )
        return
    stored = (yield from read_listing(harness, ARTIFACTS_SEEN)) or {}
    records = yield from read_file_history(harness)
    parent = prepared.log.current
    if parent is not None and all(record[TURN] != parent for record in records):
        # The listing is an older turn's, and no line says what the turns since did: compared
        # with nothing, every file is named, or no rewind past them could put one back.
        before = {}
    else:
        # Unlisted, every other file would read as removed.
        before = {path: mark for path, mark in stored.items() if not off or path in now}
    unlisted = sorted(cfg.layout.artifact_dirs) if off else []
    under = folder_for(turn)
    try:
        not_kept, dropped = yield from _copies_recorded(
            prepared,
            cfg,
            under,
            records=records,
            now=now,
            memory=memory,
            before=before,
            unlisted=unlisted,
        )
    except Exception:
        # Before the error goes on: no line will name these copies, so no rewind reads them,
        # no limit counts them and `forget` would miss them. The folder is this turn's own,
        # an id never being taken twice.
        with suppress(OSError):
            yield from harness.drop(f"{KEPT_FILES}/{under}")
        raise
    # Once the history no longer names them: removed first, a crash would leave it promising
    # a copy that is not there.
    for at, path in dropped:
        yield from harness.drop(f"{KEPT_FILES}/{records[at][UNDER]}/{path}")
    # A version this turn failed to copy is left unmarked, so the next turn tries again.
    seen = {
        path: None if path in not_kept and not_kept[path][WHY] not in _FINAL else mark
        for path, (mark, _) in now.items()
    }
    # Against what was stored, so a turn at 0 forgets the folders' other files: when copies
    # resume, the first turn copies every file, the copies kept before 0 having been dropped.
    if seen != stored:
        yield from write_listing(harness, ARTIFACTS_SEEN, seen)


def _copies_recorded(  # noqa: PLR0913 -- the history, and the folders as found and as last listed
    prepared: Prepared,
    cfg: Config,
    under: str,
    *,
    records: list[dict[str, Any]],
    now: dict[str, Seen],
    memory: bytes | None,
    before: dict[str, str | None],
    unlisted: list[str],
) -> Steps[tuple[dict[str, dict[str, Any]], list[tuple[int, str]]]]:
    """The turn's copies made and its line added to `records` and written: what was not
    copied, and the copies dropped for space, which the caller removes."""
    turn, harness = prepared.turn.id, prepared.harness
    memory_file = cfg.layout.memory_file_route.lstrip("/")
    # Once, before any copy: until the deadline the claim has a whole superstep's bound left,
    # which no copy on this host comes near, and past it as little as nothing.
    late = monotonic() > prepared.deadline
    budgeted = cfg.rewind_max_bytes is not None
    kept: dict[str, dict[str, Any]] = {}
    not_kept: dict[str, dict[str, Any]] = {}
    # Before the copies, which mark a file the listing did not by what its contents read.
    _warn_unmarked(turn, now, budgeted=budgeted)
    for path, (listed, size) in sorted(now.items()):
        mark = listed
        if mark is not None and before.get(path) == mark:
            continue
        why = LATE if late else _not_to_copy(path, size, prepared, cfg)
        if why is None:
            if path == memory_file:
                # From the bytes its mark was read from, so the copy is the version it names.
                copied, why = yield from _kept_whole(harness, under, path, memory)
            else:
                destination = f"{HARNESS_ROUTE}{KEPT_FILES}/{under}/{path}"
                # A file the listing gives no mark is told unchanged by its contents, where
                # the copy reads them; on the other routes it is copied every turn.
                last = before.get(path) if listed is None else None
                copied, why, read = yield from copied_through(
                    prepared.files, f"/{path}", destination, size, counted=budgeted, last=last
                )
                if listed is None and read is not None:
                    # Saved as the listing's mark, so the next turn compares its contents with
                    # these. Only where the listing gave none: beside one, the two would differ.
                    mark = read
                    now[path] = (read, size)
                if why == SAME:
                    continue
            if why is None:
                kept[path] = {MARK: mark, BYTES: copied}
                if path == memory_file:
                    kept[path][OUTSIDE_BUDGET] = True
                continue
            # `turn_files_unmarked` says it once for the turn, where this would say it a file.
            if why != UNCOUNTED:
                # Not the file, which the agent named, nor `why`, which may quote the backend:
                # the history line keeps both, inside the session.
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
            unlisted=unlisted,
            whole=not before,
        )
    )
    # The tree only where a rewind made one: until then the order the turns ran is it.
    parents = (
        (yield from turn_parents(harness))
        if cfg.rewind_max_bytes is not None and prepared.log.off_path
        else None
    )
    dropped = dropped_for_space(records, cfg.rewind_max_bytes, parents)
    for at, path in dropped:
        records[at][KEPT][path][DROPPED_IN] = turn
    # Patched as parsed, so a key another version wrote survives.
    lines = "".join(f"{json.dumps(one, ensure_ascii=False)}\n" for one in records)
    yield from harness.store(FILE_HISTORY, lines.encode("utf-8"))
    return not_kept, dropped


def _warn_unmarked(turn: str, now: Mapping[str, Seen], *, budgeted: bool) -> None:
    """Say once for the turn which folders the listing gave no mark for, where that costs a
    deployment a copy of every such file every turn, which nothing else would tell it."""
    folders = sorted({path.partition("/")[0] for path, (mark, _) in now.items() if mark is None})
    if not folders:
        return
    named = " and ".join(f"/{one}" for one in folders)
    instead = (
        "not copied, since KINGFISHER_REWIND_MAX_BYTES could not count it"
        if budgeted
        else "copied again"
    )
    cost = (
        "is read back every turn to tell it unchanged, or, where the backend copies files "
        f"itself, {instead}"
    )
    warn(
        "turn_files_unmarked",
        f"turn {turn}: the listing gave no checksum, size or time for files under {named}, "
        f"so each {cost}",
        folders=folders,
    )


class _Tree(NamedTuple):
    """The turns as a rewind left them, for ranking copies."""

    #: Each turn's children, by the turn.
    children: dict[str | None, list[str]]
    #: Each turn's line in the history, by the turn.
    line: dict[str, int]


def _tree(records: list[dict[str, Any]], parents: Mapping[str, str | None]) -> _Tree:
    """Built once for all the copies ranked, not per copy: per copy, a thousand turns cost
    half a second at every turn's end."""
    children: dict[str | None, list[str]] = {}
    for turn, parent in parents.items():
        children.setdefault(parent, []).append(turn)
    return _Tree(children, {record[TURN]: number for number, record in enumerate(records)})


def _reach(records: list[dict[str, Any]], at: int, path: str, tree: _Tree) -> int:
    """The newest line whose turn the copy on line `at` still restores, down every branch
    from its turn until `path` next changes there: the last line, the turn now ending,
    where it is that turn's version, which no other copy outranks."""
    children, line = tree
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
    if path == cfg.layout.memory_file_route.lstrip("/"):
        # The store loads the user's memory over the file at every turn's start, so a copy
        # could restore nothing, and it would hold the user's memory out of the store's reach.
        # Unkeyed, no budget turns it away: a rewind would bring back the conversation
        # without what the agent knew then, to make room for a copy of a report.
        return HELD_BY_STORE if prepared.memory_key is not None else None
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
    unlisted: list[str],
    whole: bool,
) -> dict[str, Any]:
    """A turn's line in the history, each key only where it holds something."""
    line: dict[str, Any] = {TURN: turn, UNDER: under}
    keys = ((KEPT, kept), (NOT_KEPT, not_kept), (REMOVED, removed), (STATE, state))
    for key, held in (*keys, (UNLISTED, unlisted), (WHOLE, whole)):
        if held:
            line[key] = held
    return line


def artifacts_now(
    files: Any, layout: Layout, walked: Walked | None
) -> Steps[tuple[dict[str, Seen] | None, bytes | None]]:
    """The artifact folders as `walked` found them, but the memory file marked by its
    contents, and those contents; `None` for either that could not be read. Without `walked`,
    the memory file alone, where it is there.

    By its contents because a listing may give no mark, and a memory file without one would
    be copied every turn; it is small, and every turn reads it whole into the prompt anyway.
    """
    memory_file = layout.memory_file_route.lstrip("/")
    if walked is None:
        now: dict[str, Seen] | None = {memory_file: (None, None)}
    else:
        # Every file a listing missed would read as removed.
        now = None if walked.cut else dict(walked.seen)
    if now is None or memory_file not in now:
        return now, None
    (got,) = yield reading(files, "download_files", [layout.memory_file_route])
    if walked is None and got.error == "file_not_found":
        return {}, None
    if got.error or got.content is None:
        return now, None
    now[memory_file] = (content_mark(got.content), len(got.content))
    return now, got.content


def _kept_whole(
    harness: HarnessFiles, under: str, path: str, content: bytes | None
) -> Steps[tuple[int | None, str | None]]:
    """What the copy of `content` as `path`'s in `under` came to, or why there is none."""
    if content is None:
        return None, UNREAD_MEMORY
    try:
        yield from harness.store(f"{KEPT_FILES}/{under}/{path}", content)
    except OSError as exc:
        return None, str(exc)
    return len(content), None


def forget_in_copies(harness: HarnessFiles, layout: Layout, entry: MemoryEntry) -> Steps[None]:
    """Take `entry` out of every copy of the memory file, and what the writer had not read
    out of every copy of its state, so no rewind brings either back; and remove each turn's
    folder no line of the history names. Run under the session's claim, so no turn is
    copying into a folder while it looks."""
    memory_file = layout.memory_file_route.lstrip("/")
    records = yield from read_file_history(harness)
    named = {under for record in records if (under := _under(record))}
    for folder in (yield from harness.folders(KEPT_FILES)):
        # Left by a turn that died copying, before its line: no rewind reads it, no limit
        # counts it, and the shell can read it.
        if folder not in named:
            yield from harness.drop(f"{KEPT_FILES}/{folder}")
    states = {one[UNDER] for record in records if (one := state_in(record).get(MEMORY_WRITER))}
    for under in sorted(states):
        yield from unread_dropped(harness, f"{KEPT_FILES}/{under}/{STATE_FOLDER}/{MEMORY_WRITER}")
    for record in records:
        under = _under(record)
        # A dropped copy, whose removal may have failed, and one a copy failed partway
        # through: the shell can read what is left of either.
        touched = _entries(record, KEPT) | _entries(record, NOT_KEPT)
        if under is None or memory_file not in touched:
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
        last = next((one for one in map(state_in, reversed(records)) if name in one), {})
        if last.get(name, {}).get(SHA) == digest:
            kept[name] = last[name]
            continue
        yield from harness.store(f"{KEPT_FILES}/{under}/{STATE_FOLDER}/{name}", held)
        kept[name] = {UNDER: under, SHA: digest}
    return kept


def state_in(record: dict[str, Any]) -> dict[str, dict[str, str]]:
    """The record's kept state, by name, each with a folder `folder_for` could have named."""
    held = record.get(STATE)
    if not isinstance(held, dict):
        return {}
    return {
        name: one
        for name, one in held.items()
        if isinstance(one, dict) and _a_folder(one.get(UNDER))
    }


def unlisted_in(record: dict[str, Any]) -> set[str]:
    """The artifact folders the record's turn did not list."""
    unlisted = record.get(UNLISTED)
    if not isinstance(unlisted, list):
        return set()
    return {one for one in unlisted if isinstance(one, str)}


def removed_in(record: dict[str, Any]) -> list[str]:
    """The paths there when the turn before the record's ended and gone when it did."""
    removed = record.get(REMOVED)
    return [one for one in removed if isinstance(one, str)] if isinstance(removed, list) else []


def versions_in(record: dict[str, Any]) -> dict[str, tuple[str | None, str | None, str | None]]:
    """Each file the record's turn changed, as the folder of its copy or `None`, its mark,
    and why there is no copy."""
    under = _under(record)
    versions: dict[str, tuple[str | None, str | None, str | None]] = {}
    for where, kept in _entries(record, KEPT).items():
        dropped = kept.get(DROPPED_IN)
        if dropped is not None:
            why = f"its copy was dropped for space in turn {dropped}"
            versions[where] = (None, kept.get(MARK), why)
        else:
            versions[where] = (under, kept.get(MARK), None if under else "its copy has no folder")
    for where, missing in _entries(record, NOT_KEPT).items():
        versions[where] = (None, missing.get(MARK), str(missing.get(WHY) or "it has no copy"))
    return versions


def turn_files(records: list[dict[str, Any]]) -> dict[str, TurnFiles]:
    """What each turn with a line in the history did to the files, by its id."""
    found: dict[str, TurnFiles] = {}
    for record in records:
        if not isinstance(turn := record.get(TURN), str):
            continue
        versions = versions_in(record)
        found[turn] = TurnFiles(
            changed=tuple(sorted(versions)),
            removed=tuple(sorted(removed_in(record))),
            without_copy={where: why for where, (_, _, why) in versions.items() if why},
            unrecorded=tuple(sorted(unlisted_in(record))),
        )
    return found

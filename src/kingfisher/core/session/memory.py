"""The session's memory file as entries: one line each, `- [m3] text`, the id kingfisher's.

The agent adds an entry as a line starting `- `; a turn's start and end number it, so a
caller can be told which entries a turn added, changed or removed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from kingfisher.core.result import MemoryChanges, MemoryEntry, MemoryEntryError, RunEvent

from .steps import Steps, changing, on_host, reading

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.agent.tool_context import Caller
    from kingfisher.core.interfaces import MemoryStore
    from kingfisher.core.layout import Layout

_log = logging.getLogger(__name__)

_NUMBERED = re.compile(r"^- \[m(\d+)\] (.*)$")
_BARE = re.compile(r"^- (.*\S.*)$")


def numbered(text: str, *, after: int = 0) -> tuple[str, tuple[MemoryEntry, ...]]:
    """`text` with every entry given an id, and the entries, in file order.

    A line led by whitespace continues the entry above it. An id written twice keeps its
    first line and numbers the second afresh, so no two entries share one. A new id goes
    past both the text's highest and `after`.
    """
    lines = text.split("\n")
    taken = {int(m.group(1)) for line in lines if (m := _NUMBERED.match(line))}
    after = max(taken | {after})
    seen: set[int] = set()
    entries: list[list[str]] = []
    out: list[str] = []
    for written in lines:
        numbered_line = _NUMBERED.match(written)
        line = written
        if numbered_line and int(numbered_line.group(1)) not in seen:
            number, said = int(numbered_line.group(1)), numbered_line.group(2)
        elif bare := _BARE.match(written):
            # A copied id counts as written by hand: it is the text that is new.
            said = numbered_line.group(2) if numbered_line else bare.group(1)
            after += 1
            number = after
            line = f"- [m{number}] {said}"
        else:
            if entries and line[:1].isspace() and line.strip():
                entries[-1].append(line.strip())
            out.append(line)
            continue
        seen.add(number)
        entries.append([f"m{number}", said.strip()])
        out.append(line)
    return "\n".join(out), tuple(
        MemoryEntry(id=parts[0], text=" ".join(parts[1:])) for parts in entries
    )


def without(text: str, entry_id: str) -> str:
    """`text` numbered, less the entry `entry_id` and the lines continuing it.

    `MemoryEntryError`, naming the ids there are, for an id `text` holds no entry for.
    """
    written, entries = numbered(text)
    if entry_id not in {one.id for one in entries}:
        held = ", ".join(one.id for one in entries) or "none"
        msg = f"no memory entry {entry_id!r}; the memory file holds {held}"
        raise MemoryEntryError(msg)
    kept: list[str] = []
    owner = None
    for line in written.split("\n"):
        if entry := _NUMBERED.match(line):
            owner = f"m{entry.group(1)}"
        # Continued by the rule `numbered` reads it by, or this would drop lines it did
        # not count as the entry's, or keep some it did.
        elif not (line[:1].isspace() and line.strip()):
            kept.append(line)
            continue
        if owner != entry_id:
            kept.append(line)
    return "\n".join(kept)


def changes(
    before: tuple[MemoryEntry, ...],
    after: tuple[MemoryEntry, ...],
    dropped: tuple[MemoryEntry, ...] = (),
) -> MemoryChanges:
    """What became of the entries between two readings, by id, with those the identifier
    check took out reported as dropped rather than removed."""
    was = {one.id: one for one in before}
    now = {one.id: one for one in after}
    gone = {one.id for one in dropped}
    return MemoryChanges(
        added=tuple(one for one in after if one.id not in was),
        changed=tuple(one for one in after if one.id in was and was[one.id].text != one.text),
        removed=tuple(one for one in before if one.id not in now and one.id not in gone),
        dropped=dropped,
    )


def without_entries(text: str, ids: frozenset[str]) -> str:
    """`text` less the entries `ids` names, each with the lines continuing it."""
    kept: list[str] = []
    skipping = False
    for line in text.split("\n"):
        if skipping and line[:1].isspace() and line.strip():
            continue
        found = _NUMBERED.match(line)
        skipping = found is not None and f"m{found.group(1)}" in ids
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def with_entries(text: str | None, entries: tuple[MemoryEntry, ...]) -> str:
    """The memory file holding `entries`, under whatever `text` had above its first entry."""
    above: list[str] = []
    for line in (text or "").split("\n"):
        if _BARE.match(line):
            break
        above.append(line)
    header = "\n".join(above).rstrip()
    # One line each, whatever a writer or a store answered: a newline would end the entry.
    listed = "".join(f"- [{one.id}] {' '.join(one.text.split())}\n" for one in entries)
    return f"{header}\n\n{listed}" if header else listed


#: What a written list says when it keeps nothing, so an empty answer can be refused.
NOTHING_KEPT = "none"


def listed(answer: str, before: tuple[MemoryEntry, ...]) -> tuple[MemoryEntry, ...]:
    """The entries a written list holds, a new one numbered past every id in `before`.

    An id `before` does not hold is read as no id: kept, it would report an entry nobody
    had as changed, or bring back an id the file once gave something else. Raises
    `ValueError`, saying why, for an answer that is not a list.
    """
    lines = answer.strip().split("\n")
    # Fenced or not, the lines inside are the list: models fence unasked.
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    said = [line for line in lines if line.strip()]
    if [line.strip().lower() for line in said] == [NOTHING_KEPT]:
        return ()
    if not said:
        msg = "it answered nothing"
        raise ValueError(msg)
    known = {one.id for one in before}
    kept: list[str] = []
    for line in said:
        if (found := _NUMBERED.match(line)) and f"m{found.group(1)}" not in known:
            kept.append(f"- {found.group(2)}")
        elif _BARE.match(line) or (kept and line[:1].isspace()):
            kept.append(line)
        else:
            msg = f"its answer is not a list of entries: {line.strip()[:80]!r}"
            raise ValueError(msg)
    highest = max((int(one.id[1:]) for one in before), default=0)
    return numbered("\n".join(kept), after=highest)[1]


def memory_text(backend: Any, layout: Layout) -> Steps[tuple[str | None, tuple[MemoryEntry, ...]]]:
    """The memory file as numbered text, or `None` where there is none, and its entries;
    its unnumbered lines are numbered and written back first.

    No file is no entries: an agent may delete it, and a turn must still end.
    """
    path = layout.memory_file_route
    (got,) = yield reading(backend, "download_files", [path])
    if got.error or got.content is None:
        return None, ()
    text = got.content.decode("utf-8", errors="replace")
    written, entries = numbered(text)
    if written != text:
        yield changing(backend, "upload_files", [(path, written.encode("utf-8"))])
    return written, entries


def settled_memory(backend: Any, layout: Layout) -> Steps[tuple[MemoryEntry, ...]]:
    """The memory file's entries, its unnumbered lines numbered and written back first."""
    _, entries = yield from memory_text(backend, layout)
    return entries


def memory_entries(backend: Any, layout: Layout) -> Steps[tuple[MemoryEntry, ...]]:
    """The memory file's entries, by the ids a turn would give them, with nothing written.

    Written back, a read taken without the session's claim could land over a turn's edit.
    """
    (got,) = yield reading(backend, "download_files", [layout.memory_file_route])
    if got.error or got.content is None:
        return ()
    return numbered(got.content.decode("utf-8", errors="replace"))[1]


def forget_entry(backend: Any, layout: Layout, entry_id: str) -> Steps[None]:
    """Write the memory file back without `entry_id`; for a caller holding the claim."""
    path = layout.memory_file_route
    (got,) = yield reading(backend, "download_files", [path])
    present = not got.error and got.content is not None
    text = got.content.decode("utf-8", errors="replace") if present else ""
    answers = yield changing(backend, "upload_files", [(path, without(text, entry_id).encode())])
    if refused := [f"{answer.path}: {answer.error}" for answer in answers if answer.error]:
        msg = f"the session's backend would not keep {', '.join(refused)}"
        raise OSError(msg)


#: What a working copy of a user's memory opens with: text for an edit to anchor against.
_HEADING = "# Memory"


def as_file(entries: tuple[MemoryEntry, ...]) -> str:
    """`entries` as the memory file holds them, a numbered line each under a heading."""
    return with_entries(_HEADING, entries)


@dataclass(frozen=True)
class UsersMemory:
    """Whose memory a turn keeps, as a wired `MemoryStore` answered at its start."""

    #: The store's key, or `None` where the turn keeps the session's memory.
    key: str | None = None
    #: The user's entries, for the session's file; `None` where none could be loaded.
    loaded: tuple[MemoryEntry, ...] | None = None
    events: tuple[RunEvent, ...] = ()


def users_memory(store: MemoryStore, caller: Caller) -> Steps[UsersMemory]:
    """Whose memory this turn keeps, and their entries as `store` holds them now.

    A key whose entries cannot be loaded is still kept: the session's copy is the one the
    last turn left, so what this turn changes in it is still the user's to apply.
    """
    try:
        key = yield on_host(store.key, caller)
    except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
        return UsersMemory(events=(store_failed(store, "key", exc),))
    if key is None:
        said = f"{type(store).__name__} named no user, so this turn keeps the session's memory"
        return UsersMemory(events=(RunEvent(kind="memory_unkeyed", text=said),))
    try:
        loaded = yield on_host(_entries, store.load, key)
    except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
        return UsersMemory(key=key, events=(store_failed(store, "load", exc),))
    return UsersMemory(key=key, loaded=loaded)


def kept_in_store(
    store: MemoryStore, key: str, changed: MemoryChanges, backend: Any, layout: Layout
) -> Steps[RunEvent | None]:
    """`changed` applied to the user's memory, and the session's copy made what the store
    answered. The event saying it failed, or `None`.

    Written back so the copy between turns is the user's memory, not this session's view
    of it.
    """
    try:
        after = yield on_host(_entries, store.apply, key, changed)
    except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
        return store_failed(store, "apply", exc)
    yield changing(backend, "upload_files", [(layout.memory_file_route, as_file(after).encode())])
    return None


def _entries(call: Callable[..., Any], *args: Any) -> tuple[MemoryEntry, ...]:
    """What a store's `load` or `apply` answered, refused unless it is entries: written
    into the memory file, anything else would fail the turn there instead."""
    got = tuple(call(*args))
    if wrong := [one for one in got if not isinstance(one, MemoryEntry)]:
        msg = f"answered a {type(wrong[0]).__name__}, not a MemoryEntry"
        raise TypeError(msg)
    return got


#: What a turn does instead, by the store call that failed.
_INSTEAD = {
    "key": "this turn keeps the session's memory",
    "load": "this turn reads the session's copy of the user's memory",
    "apply": (
        "this turn's changes stay in the session's copy, until its next turn reloads "
        "the user's memory"
    ),
}


def store_failed(store: MemoryStore, call: str, exc: Exception) -> RunEvent:
    """The event, and the log line with its traceback, for a store call that raised."""
    named = f"{type(store).__name__}.{call}"
    _log.warning("%s raised; the turn goes on", named, exc_info=exc)
    return RunEvent(
        kind="memory_store_failed",
        text=f"{named} raised {type(exc).__name__}: {exc}; {_INSTEAD[call]}",
    )

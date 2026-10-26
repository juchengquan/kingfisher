"""The session's memory file as entries: one line each, `- [m3] text`, the id kingfisher's.

The agent adds an entry as a line starting `- `; a turn's start and end number it, so a
caller can be told which entries a turn added, changed or removed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from kingfisher.core.result import MemoryChanges, MemoryEntry, MemoryEntryError

from .steps import Steps, changing, reading

if TYPE_CHECKING:
    from kingfisher.core.layout import Layout

_NUMBERED = re.compile(r"^- \[m(\d+)\] (.*)$")
_BARE = re.compile(r"^- (.*\S.*)$")


def numbered(text: str) -> tuple[str, tuple[MemoryEntry, ...]]:
    """`text` with every entry given an id, and the entries, in file order.

    A line led by whitespace continues the entry above it. An id written twice keeps its
    first line and numbers the second afresh, so no two entries share one.
    """
    lines = text.split("\n")
    taken = {int(m.group(1)) for line in lines if (m := _NUMBERED.match(line))}
    after = max(taken, default=0)
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


def changes(before: tuple[MemoryEntry, ...], after: tuple[MemoryEntry, ...]) -> MemoryChanges:
    """What became of the entries between two readings, by id."""
    was = {one.id: one for one in before}
    now = {one.id: one for one in after}
    return MemoryChanges(
        added=tuple(one for one in after if one.id not in was),
        changed=tuple(one for one in after if one.id in was and was[one.id].text != one.text),
        removed=tuple(one for one in before if one.id not in now),
    )


def settled_memory(backend: Any, layout: Layout) -> Steps[tuple[MemoryEntry, ...]]:
    """The memory file's entries, its unnumbered lines numbered and written back first.

    No file is no entries: an agent may delete it, and a turn must still end.
    """
    path = layout.memory_file_route
    (got,) = yield reading(backend, "download_files", [path])
    if got.error or got.content is None:
        return ()
    text = got.content.decode("utf-8", errors="replace")
    written, entries = numbered(text)
    if written != text:
        yield changing(backend, "upload_files", [(path, written.encode("utf-8"))])
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

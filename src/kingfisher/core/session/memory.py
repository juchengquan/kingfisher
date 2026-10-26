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
    listed = "".join(f"- [{one.id}] {one.text}\n" for one in entries)
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

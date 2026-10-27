"""A `MemoryStore` keeping each user's memory in a file of its own, keyed on the request's
`context["user_id"]`.

For code that builds its own `Kingfisher`:
`Kingfisher(..., memory_store=FileMemoryStore(Path("user_memory")))`. One process only:
the lock keeping two sessions' saves apart is this process's, so a deployment running
several keeps its users' memory where a transaction can.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher import MemoryEntry
from kingfisher.plugins import MemoryStore

if TYPE_CHECKING:
    from kingfisher import Caller, MemoryChanges

_ID = re.compile(r"m(\d+)")


class FileMemoryStore(MemoryStore):
    """One JSON file per user, in `directory`, merged by id on every save."""

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()

    def key(self, caller: Caller) -> str | None:
        return caller.context.get("user_id") or None

    def load(self, key: str) -> tuple[MemoryEntry, ...]:
        return self._read(key)[1]

    def apply(self, key: str, changes: MemoryChanges) -> tuple[MemoryEntry, ...]:
        # Read and written under one lock: two sessions of a user end their turns at once.
        with self._lock:
            last, entries = self._read(key)
            kept = {one.id: one for one in entries}
            for one in changes.removed:
                kept.pop(one.id, None)
            for one in changes.changed:
                # Only where it still is: an edit made against an older copy must not bring
                # back an entry another session removed or the user forgot.
                if one.id in kept:
                    kept[one.id] = one
            for one in changes.added:
                # Numbered past every id this user has had, not only those kept: a reused id
                # would take an edit another session's older copy makes to the entry it named.
                number = _number(one.id)
                if number is None or number <= last:
                    number = last + 1
                last = number
                kept[f"m{number}"] = MemoryEntry(f"m{number}", one.text)
            after = tuple(kept.values())
            self._write(key, last, after)
        return after

    def _file(self, key: str) -> Path:
        # Hashed: a user id is the caller's to send, and spelt into a path it could name
        # any file.
        return self.directory / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def _read(self, key: str) -> tuple[int, tuple[MemoryEntry, ...]]:
        """The highest id this user has had, and their entries."""
        path = self._file(key)
        if not path.is_file():
            return 0, ()
        written: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        entries = tuple(MemoryEntry(one["id"], one["text"]) for one in written["entries"])
        return written["last"], entries

    def _write(self, key: str, last: int, entries: tuple[MemoryEntry, ...]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._file(key)
        # Replaced whole, so a crash mid-write leaves the user's memory as it was.
        partial = path.with_suffix(".partial")
        written = {"last": last, "entries": [{"id": one.id, "text": one.text} for one in entries]}
        partial.write_text(json.dumps(written, ensure_ascii=False, indent=1), encoding="utf-8")
        partial.replace(path)


def _number(entry_id: str) -> int | None:
    found = _ID.fullmatch(entry_id)
    return int(found.group(1)) if found else None

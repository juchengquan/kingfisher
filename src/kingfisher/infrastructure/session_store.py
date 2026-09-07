"""A `SessionStore` over a directory.

Deliberately dull. It walks, it writes, it deletes. Everything interesting about this
design is in *when* a caller reaches for it, not in what happens when they do, and a
first implementation that was clever about batching or streaming would be optimising a
cost nobody has measured yet.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.domain.references import within
from kingfisher.domain.transcript import Message, as_json, from_json

if TYPE_CHECKING:
    from kingfisher.domain.ports import SessionStore


class LocalSessionStore:
    """Sessions kept as directories under `root`, one per session id."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _held(self, session_id: str) -> Path:
        """Where one session's files sit, refusing an id that would escape."""
        return within(self.root, session_id)

    def fetch(self, session_id: str) -> dict[str, bytes]:
        """Everything kept for this session, keyed by path relative to its root."""
        held = self._held(session_id)
        if not held.is_dir():
            return {}
        return {
            str(path.relative_to(held)): path.read_bytes()
            for path in sorted(held.rglob("*"))
            if path.is_file()
        }

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files, replacing any of the same name."""
        held = self._held(session_id)
        for name, content in files.items():
            target = within(held, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def knows(self, session_id: str) -> bool:
        """Whether anything is held for this session, without reading it."""
        return self._held(session_id).is_dir()

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent."""
        shutil.rmtree(self._held(session_id), ignore_errors=True)


def restore_into(store: SessionStore, session_id: str, directory: Path) -> tuple[str, ...]:
    """Write back what the store kept, for a directory that has lost it."""
    written: list[str] = []
    for name, content in store.fetch(session_id).items():
        target = within(directory, name)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(name)
    return tuple(written)


def keep_from(store: SessionStore, session_id: str, directory: Path, names: Sequence[str]) -> None:
    """Hand the named files to the store, reading them from `directory`."""
    store.save(
        session_id,
        {
            name: within(directory, name).read_bytes()
            for name in names
            if within(directory, name).is_file()
        },
    )


#: Where a session's conversation is kept. Dotted and not in `SESSION_DIRS`,
#: for the reason `.home` is not: those are the names the agent addresses, and
#: this is plumbing. It sits at the session root so it is deleted with the
#: session, counted by `session_bytes`, and carried by whatever keeps the rest.
TRANSCRIPT = ".transcript.jsonl"


def read_transcript(directory: Path) -> tuple[Message, ...]:
    """What was said in this session before now, or nothing for a first turn."""
    held = Path(directory) / TRANSCRIPT
    if not held.is_file():
        return ()
    return from_json(held.read_text(encoding="utf-8"))


def write_transcript(directory: Path, messages: tuple[Message, ...]) -> None:
    """Replace this session's transcript with what it now holds."""
    Path(directory).mkdir(parents=True, exist_ok=True)
    (Path(directory) / TRANSCRIPT).write_text(as_json(messages), encoding="utf-8")

"""One session's directory, on this machine."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from kingfisher.infrastructure.workspace.permissions import keep_tmp_private, unlock_and_retry
from kingfisher.layout import (
    AGENTS_SCAFFOLD,
    HARNESS,
    MEMORY,
    SESSION_DIRS,
)


class LocalSessionDirs:
    """The rules about session directories on this host: make one exclusively, mark
    one used, list them, remove one. What `DefaultBackends` and the turn lock use.
    """

    def create_exclusive(self, path: Path) -> bool:
        try:
            path.mkdir()
        except FileExistsError:
            return False
        return True

    def mark_used(self, path: Path) -> None:
        # `exist_ok` because this records use of something that already exists;
        # a session that has gone is not an error here, it is the sweep winning
        # a race, and the turn will fail on its own next read.
        with suppress(OSError):
            path.touch(exist_ok=True)

    def listing(self, path: Path) -> tuple[tuple[str, float], ...]:
        if not path.is_dir():
            return ()
        return tuple((p.name, p.stat().st_mtime) for p in path.iterdir() if p.is_dir())

    def remove_tree(self, path: Path) -> str | None:
        try:
            # not ignore_errors: partials must surface
            shutil.rmtree(path, onexc=unlock_and_retry)
        except OSError as exc:
            return f"directory not removed ({exc.strerror})"
        return None


def ensure_session_layout(session_dir: Path) -> Path:
    """Every directory a session holds, and its memory scaffolded. Idempotent."""
    session_dir = Path(session_dir).expanduser().resolve()
    for name in (*SESSION_DIRS, HARNESS):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Asked for rather than done here: `permissions` is the one module in this
    # package that changes a mode, and it says why this one is set at all.
    keep_tmp_private(session_dir)
    # Last, so a session's own memory beats the scaffold rather than losing to it.
    scaffold_memory(session_dir)
    return session_dir


def scaffold_memory(session_dir: Path) -> None:
    """Give `/memory/AGENTS.md` something for an edit to anchor against.

    Scaffolded rather than empty: the memory prompt directs the agent to save
    knowledge with `edit_file`, which replaces existing text.
    """
    agents_md = Path(session_dir) / MEMORY / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())

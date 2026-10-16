"""One session's directory, on this machine."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from kingfisher.domain.session import names_a_session
from kingfisher.infrastructure.workspace.permissions import keep_tmp_private, unlock_and_retry
from kingfisher.layout import (
    AGENTS_SCAFFOLD,
    HARNESS,
    MEMORY,
    SAVED_RESULTS,
    SCRATCH,
    SESSION_DIRS,
)


class LocalSessionDirs:
    """The rules about session directories on this host: make one exclusively, mark
    one used, list them, remove one. What `LocalSessions` and the turn lock use.
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
        return tuple(
            (p.name, p.stat().st_mtime)
            for p in path.iterdir()
            if p.is_dir() and names_a_session(p.name)
        )

    def remove_tree(self, path: Path) -> str | None:
        try:
            # not ignore_errors: partials must surface
            shutil.rmtree(path, onexc=unlock_and_retry)
        except OSError as exc:
            return f"directory not removed ({exc.strerror})"
        return None


def ensure_session_layout(
    session_dir: Path,
    session_dirs: tuple[str, ...] = SESSION_DIRS,
    *,
    memory: str = MEMORY,
    scratchpad: str = SCRATCH,
    saved_results: tuple[str, ...] = SAVED_RESULTS,
) -> Path:
    """Every directory a session holds, and its memory scaffolded. Idempotent.

    The leaves default to the module constants, so the call with no layout lays out
    the names kingfisher has always used; `backend_at` passes the configured layout's
    leaves so a renamed session is made under its own names. `memory` and `scratchpad`
    are passed on their own as well as in `session_dirs` because the two steps below
    name one directory each and cannot pick it out of the tuple.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    # `saved_results` before the first result is: bubblewrap binds them read-only only
    # where they exist when its fence is built, so a folder the recorder made mid-turn
    # would be the shell's to write until the next one.
    for name in (*session_dirs, HARNESS, *saved_results):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Asked for rather than done here: `permissions` is the one module in this
    # package that changes a mode, and it says why this one is set at all.
    keep_tmp_private(session_dir, scratchpad=scratchpad)
    # Last, so a session's own memory beats the scaffold rather than losing to it.
    scaffold_memory(session_dir, memory=memory)
    return session_dir


def scaffold_memory(session_dir: Path, *, memory: str = MEMORY) -> None:
    """Give `/memory/AGENTS.md` something for an edit to anchor against.

    Scaffolded rather than empty: the memory prompt directs the agent to save
    knowledge with `edit_file`, which replaces existing text.
    """
    agents_md = Path(session_dir) / memory / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())

"""One session's directory, on this machine.

`SessionDirs` and `SessionRoot` with an implementation rather than only a promise,
and the two questions asked of what a session holds: which of it is worth keeping,
and what it costs the host to keep.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from kingfisher.domain.session import sessions_root
from kingfisher.infrastructure.workspace.permissions import unlock_and_retry
from kingfisher.layout import (
    AGENTS_SCAFFOLD,
    ARTIFACT_DIRS,
    SESSION_DIRS,
    SESSION_PLUMBING,
)


class LocalSessionDirs:
    """`SessionDirs` over the real filesystem."""

    def ensure(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

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

    def children(self, path: Path) -> tuple[str, ...]:
        if not path.is_dir():
            return ()
        return tuple(p.name for p in path.iterdir() if p.is_dir())

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
    """Create one session's layout. Idempotent."""
    session_dir = Path(session_dir).expanduser().resolve()
    for name in (*SESSION_DIRS, *SESSION_PLUMBING):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = session_dir / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return session_dir


class LocalSessionRoot:
    """A session's directory, on this machine, staying where it is."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)

    @contextmanager
    def hold(self, session_id: str) -> Iterator[Path]:
        yield sessions_root(self.workspace) / session_id


def collect_artifacts(session_dir: Path) -> tuple[str, ...]:
    """What this session holds that is worth keeping, as relative paths."""
    session_dir = Path(session_dir)
    found: list[str] = []
    for name in ARTIFACT_DIRS:
        root = session_dir / name
        if not root.is_dir():
            continue
        found.extend(
            str(path.relative_to(session_dir)) for path in root.rglob("*") if path.is_file()
        )
    return tuple(sorted(found))


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())

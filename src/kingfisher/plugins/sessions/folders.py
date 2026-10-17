"""One session's directory, on this machine."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.session.model import UnknownSessionError
from kingfisher.plugins.sessions.permissions import keep_tmp_private, unlock_and_retry

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""


def names_a_session(session_id: str) -> bool:
    """Whether an id can name a session's folder: one visible entry in a directory."""
    # Joined as given, `""` is every session, `..` the workspace, and an absolute id
    # replaces the path. Not hidden: the macOS profile guards `.harness` in every session,
    # so a session of that name could not write its files. The listing filters by this too.
    return (
        bool(session_id)
        and not session_id.startswith(".")
        and "/" not in session_id
        and "\0" not in session_id
    )


def session_dir(sessions_root: Path, session_id: str) -> Path:
    """Where one session is kept: a folder under the sessions root, named for its id."""
    # Checked here rather than at a door: every path built from an id is built here.
    if not names_a_session(session_id):
        msg = (
            f"{session_id!r} cannot name a session: a session id is one name, not "
            "starting with '.', holding neither '/' nor NUL"
        )
        raise UnknownSessionError(msg)
    return Path(sessions_root) / session_id


class LocalSessionDirs:
    """Session directories on this host, for `LocalSessions` and the turn lock."""

    def create_exclusive(self, path: Path) -> bool:
        try:
            path.mkdir()
        except FileExistsError:
            return False
        return True

    def mark_used(self, path: Path) -> None:
        # A session gone is the sweep winning a race, not an error: the turn fails on its
        # next read.
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
    session_dirs: tuple[str, ...] = DEFAULT_LAYOUT.session_dirs,
    *,
    memory: str = DEFAULT_LAYOUT.memory,
    scratchpad: str = DEFAULT_LAYOUT.scratchpad,
    saved_results: tuple[str, ...] = DEFAULT_LAYOUT.saved_results,
) -> Path:
    """Every directory a session holds, and its memory scaffolded. Idempotent.

    `memory` and `scratchpad` are also in `session_dirs`, and a renamed layout passes them
    too: the steps that scaffold and harden them cannot pick them out of the tuple.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    # `saved_results` before the first result: bubblewrap binds them read-only only where
    # they exist when its fence is built, so one made mid-turn would be the shell's to write.
    for name in (*session_dirs, DEFAULT_LAYOUT.harness, *saved_results):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Not a chmod here: `permissions` is this package's one module that changes a mode.
    keep_tmp_private(session_dir, scratchpad=scratchpad)
    # Last, so a session's own memory beats the scaffold.
    scaffold_memory(session_dir, memory=memory)
    return session_dir


def scaffold_memory(session_dir: Path, *, memory: str = DEFAULT_LAYOUT.memory) -> None:
    """Give `/memory/AGENTS.md` text for an edit to anchor against: the agent saves memory
    with `edit_file`, which replaces existing text."""
    agents_md = Path(session_dir) / memory / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())

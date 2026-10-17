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
    # One entry and nowhere else: joined as given, `""` is every session, `..` the
    # workspace, and an absolute id replaces the path. Not hidden: `.harness` is the
    # name the macOS profile guards in every session, so a session called that could
    # not write its own files. The listing reads only what passes this, so nothing it
    # lists is a session `open` would refuse and `reap` would stop at.
    return (
        bool(session_id)
        and not session_id.startswith(".")
        and "/" not in session_id
        and "\0" not in session_id
    )


def session_dir(sessions_root: Path, session_id: str) -> Path:
    """Where one session is kept: a folder of its own under the sessions root, named for
    its id.
    """
    # Here rather than at a door, because every path built from an id is built here --
    # `open` makes folders where it points and `delete` removes them.
    if not names_a_session(session_id):
        msg = (
            f"{session_id!r} cannot name a session: a session id is one name, not "
            "starting with '.', holding neither '/' nor NUL"
        )
        raise UnknownSessionError(msg)
    return Path(sessions_root) / session_id


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
    session_dirs: tuple[str, ...] = DEFAULT_LAYOUT.session_dirs,
    *,
    memory: str = DEFAULT_LAYOUT.memory,
    scratchpad: str = DEFAULT_LAYOUT.scratchpad,
    saved_results: tuple[str, ...] = DEFAULT_LAYOUT.saved_results,
) -> Path:
    """Every directory a session holds, and its memory scaffolded. Idempotent.

    The leaves default to `DEFAULT_LAYOUT`'s, so the call with no layout lays out
    the names kingfisher has always used; `backend_at` passes the configured layout's
    leaves so a renamed session is made under its own names. `memory` and `scratchpad`
    are passed on their own as well as in `session_dirs` because the two steps below
    name one directory each and cannot pick it out of the tuple.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    # `saved_results` before the first result is: bubblewrap binds them read-only only
    # where they exist when its fence is built, so a folder the recorder made mid-turn
    # would be the shell's to write until the next one.
    for name in (*session_dirs, DEFAULT_LAYOUT.harness, *saved_results):
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Asked for rather than done here: `permissions` is the one module in this
    # package that changes a mode, and it says why this one is set at all.
    keep_tmp_private(session_dir, scratchpad=scratchpad)
    # Last, so a session's own memory beats the scaffold rather than losing to it.
    scaffold_memory(session_dir, memory=memory)
    return session_dir


def scaffold_memory(session_dir: Path, *, memory: str = DEFAULT_LAYOUT.memory) -> None:
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

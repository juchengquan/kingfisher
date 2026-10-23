"""One session's directory, on this machine."""

from __future__ import annotations

import errno
import os
import shutil
import stat
from contextlib import suppress
from pathlib import Path

from kingfisher.core.layout import Layout
from kingfisher.core.session.model import UnknownSessionError

from .permissions import keep_tmp_private, unlock_and_retry
from .scratch import NOFOLLOW

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

_REPLACED = frozenset({errno.ELOOP, errno.EEXIST, errno.EISDIR})


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


def ensure_session_layout(session_dir: Path, layout: Layout) -> Path:
    """Every directory a session holds, and its memory scaffolded. Idempotent.

    The layout is the deployment's, with no default: a call that forgot it would lay a
    renamed deployment's sessions out under the names kingfisher ships.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    # Before anything is made or written: `mkdir` and the scaffold would go through a link.
    refuse_linked_folders(session_dir, layout)
    # The saved results before the first result: bubblewrap binds them read-only only where
    # they exist when its fence is built, so one made mid-turn would be the shell's to write.
    for name in layout.made_dirs:
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Not a chmod here: `permissions` is this package's one module that changes a mode.
    keep_tmp_private(session_dir, layout)
    # Last, so a session's own memory beats the scaffold.
    scaffold_memory(session_dir, layout)
    return session_dir


def refuse_linked_folders(session_dir: Path, layout: Layout) -> None:
    """Refuse a session whose folders are not where they are named, as a link makes them.

    A route's backend is rooted where its folder resolves, so a `memory` the shell
    replaced with a link would hand the file tools, which no fence holds, the host.
    Refused rather than replaced: what the link points at may be someone's data.
    """
    real = Path(session_dir).resolve()
    # Not `made_dirs` alone: a link the shell left where `scratchpad/blobs` was never made
    # rooted the next turn's `/scratchpad/blobs/` on the host.
    moved = [name for name in layout.unlinked_dirs if (real / name).resolve() != real / name]
    if moved:
        msg = (
            f"session directory {real}: {', '.join(moved)} is a link or reached through "
            "one, which no session folder may be; remove the link to use this session"
        )
        raise ValueError(msg)


def scaffold_memory(session_dir: Path, layout: Layout) -> None:
    """Give `/memory/AGENTS.md` text for an edit to anchor against: the agent saves memory
    with `edit_file`, which replaces existing text."""
    agents_md = Path(session_dir) / layout.memory / "AGENTS.md"
    # This runs in kingfisher's process, outside every fence, on a file the shell may
    # replace: only a regular file is read or written, and only a missing one created. The
    # flags refuse a link made after the `lstat` too -- not on Windows, which has neither
    # `O_NOFOLLOW` nor a fence to run the shell in.
    try:
        held = agents_md.lstat()
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW
    else:
        if not stat.S_ISREG(held.st_mode) or _has_text(agents_md):
            return
        flags = os.O_WRONLY | os.O_TRUNC | NOFOLLOW
    try:
        fd = os.open(agents_md, flags, 0o644)
    except OSError as exc:
        # Replaced since the lstat: a link (ELOOP), a file made first, or a folder.
        if exc.errno in _REPLACED:
            return
        raise
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write(AGENTS_SCAFFOLD)


def _has_text(path: Path) -> bool:
    """Whether a regular file holds more than whitespace, read without following a link."""
    try:
        fd = os.open(path, os.O_RDONLY | NOFOLLOW)
    except OSError:
        # A link, or a file this process cannot read: left as it is.
        return True
    with os.fdopen(fd, encoding="utf-8", errors="replace") as held:
        return bool(held.read().strip())


def session_bytes(session_dir: Path) -> int:
    """How much disk one session is holding, across everything in it."""
    session_dir = Path(session_dir)
    if not session_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())

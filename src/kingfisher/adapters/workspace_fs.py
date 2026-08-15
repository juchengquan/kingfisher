"""The filesystem, doing what `domain.layout` describes.

Everything here touches the world: mkdir, chmod, rmtree. It was in `domain/`,
where a test asserted it imported nothing from langchain and passed happily
while shelling out to git and dropping write bits off files. "No foreign
imports" is not the same boundary as "no side effects", and only the second
one makes a domain layer worth having.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from kingfisher.domain.layout import (
    AGENTS_SCAFFOLD,
    LAYOUT_DIRS,
    MARKER,
    WORKSPACE_GITIGNORE,
)


class LocalSessionDirs:
    """`SessionDirs` over the real filesystem.

    `create_exclusive` deliberately does *not* pass `exist_ok`: the domain's
    turn allocation is correct only because a taken name fails here.
    """

    def ensure(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def create_exclusive(self, path: Path) -> bool:
        try:
            path.mkdir()
        except FileExistsError:
            return False
        return True

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
            shutil.rmtree(path)  # not ignore_errors: partials must surface
        except OSError as exc:
            return f"directory not removed ({exc.strerror})"
        return None


def is_new_workspace(workspace: Path) -> bool:
    """True when this path has never been used as a workspace.

    Surfaced by callers so a silently relocated workspace — an unstable `~`,
    a changed env var — reads as "created new" rather than as a first run.
    """
    return not (Path(workspace) / MARKER).exists()


def ensure_layout(workspace: Path) -> Path:
    """Create the workspace layout and its .gitignore. Idempotent."""
    workspace = Path(workspace).expanduser().resolve()
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(WORKSPACE_GITIGNORE, encoding="utf-8")

    marker = workspace / MARKER
    if not marker.exists():
        marker.write_text("kingfisher workspace\n", encoding="utf-8")

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = workspace / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return workspace


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def protect_data(workspace: Path) -> None:
    """Make `data/` read-only at the OS level. Idempotent.

    This is the layer the tool-level deny rule cannot provide: the kernel
    enforces it against `execute` too, and filesystem permissions in deepagents
    are applied only to the built-in file tools. Directories are included
    because deletion is governed by the *directory's* write bit, not the file's.
    """
    data = Path(workspace) / "data"
    if not data.is_dir():
        return
    for path in sorted(data.rglob("*"), reverse=True):
        _drop_write_bits(path)
    _drop_write_bits(data)


@contextmanager
def writable_data(workspace: Path) -> Iterator[Path]:
    """Temporarily make `data/` writable, for loading inputs.

        with writable_data(ws) as data:
            shutil.copy(source, data / "sales.csv")
    """
    data = Path(workspace) / "data"
    data.mkdir(parents=True, exist_ok=True)
    _add_write_bits(data)
    for path in data.rglob("*"):
        _add_write_bits(path)
    try:
        yield data
    finally:
        protect_data(workspace)

"""Writes to this host's disk that a power cut cannot take back once they return."""

from __future__ import annotations

import os
import sys
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.permitted_backend import host_path

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


def flush(fd: int) -> None:
    """Hand what `fd` holds to the drive, and the drive's cache to its storage."""
    if sys.platform == "darwin":
        import fcntl  # noqa: PLC0415 -- POSIX only, and asked only here

        try:
            # macOS's `fsync` stops at the drive's cache, which a power cut empties.
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError:
            # A filesystem with no such request, a network share among them.
            os.fsync(fd)
        return
    os.fsync(fd)


def flushed(path: Path, made: Iterable[Path] = (), *, new: bool, full: bool = True) -> None:
    """Flush the file at `path`, and the folder entries a power cut would take back with
    it: its own where it is `new`, and each of `made`, the folders made for it.

    `full=False` stops at the drive's cache on macOS: only for a file a full flush of
    another follows before anything relies on it, since that one takes the cache along.
    """
    for folder in {one.parent for one in ((path,) if new else ()) + tuple(made)}:
        held = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(held)
        finally:
            os.close(held)
    # The folders first: on macOS the file's full flush takes their entries along.
    held = os.open(path, os.O_RDONLY)
    try:
        if full:
            flush(held)
        else:
            os.fsync(held)
    finally:
        os.close(held)


def missing_folders(folder: Path) -> list[Path]:
    """The folders `folder` and those above it that are not there yet, nearest first."""
    missing = []
    while not folder.exists() and folder.parent != folder:
        missing.append(folder)
        folder = folder.parent
    return missing


def on_this_host(backend: Any, where: str) -> Path | None:
    """Where the backend keeps `where` on this host, or `None` for a backend that keeps it
    elsewhere, or could not say."""
    try:
        found = host_path(backend, where)
    except Exception:  # noqa: BLE001 -- a turn outlives a failing plugin
        # Unlogged: asked at every save, and a tool's own asking already reports it once.
        return None
    return None if found is None else found[1]


def unmade(path: Path) -> tuple[list[Path], bool]:
    """What a write of `path` would make: the folders above it, and whether the file."""
    return missing_folders(path.parent), not path.exists()

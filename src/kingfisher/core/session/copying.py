"""Copying one file in a session's backend, on this host where the backend names both ends
here."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import os
import shutil
import stat
import sys
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.permitted_backend import HostPathFailedError, host_path_in_turn

from .steps import Steps, changing, on_host, reading

if TYPE_CHECKING:
    from pathlib import Path

#: Said by `copied_through` of contents whose mark is the `last` it was handed, which it then
#: does not write.
SAME = "its contents are the version the last turn left"

#: Why `copied_through`, `counted`, refused a copy whose size the listing did not give.
UNCOUNTED = "its size is unknown, so KINGFISHER_REWIND_MAX_BYTES could not count its copy"

# Not blocking, or a FIFO left under a listed name would hold the ending on its open.
_SOURCE = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_CLOEXEC", 0)
)
_CHUNK = 1024 * 1024
# macOS's value, which Linux's is not: only the macOS clone may use it.
_AT_FDCWD = -2
_FICLONE = 0x40049409


def content_mark(content: bytes) -> str:
    """The mark a version gets from its contents, where no listing gave it one."""
    return f"sha256 {hashlib.sha256(content).hexdigest()}"


def copied_through(  # noqa: PLR0913 -- the two keywords are a turn's end's alone
    backend: Any,
    source: str,
    destination: str,
    size: int | None,
    *,
    counted: bool = False,
    last: str | None = None,
) -> Steps[tuple[int | None, str | None, str | None]]:
    """What the copy of `source` at `destination` came to, or why there is none, and the
    mark of the contents where it read them.

    On this host where the backend names both ends here; else by the backend's own `copy`;
    else read and written whole, which holds the file in this process's memory. `counted`
    where a budget has to count the copy: the backend's own `copy` reports no size, so one
    whose listing gave none is refused before it is made, or the budget would count it as
    nothing and bound nothing. Read whole, contents whose mark is `last` are not written
    again, and `SAME` says so.
    """
    whence = yield on_host(_on_this_host, backend, source)
    where = yield on_host(_on_this_host, backend, destination)
    if whence is not None and where is not None:
        try:
            return (yield on_host(copied_on_host, whence, where)), None, None
        except OSError as exc:
            return None, exc.strerror or str(exc), None
    own = getattr(backend, "copy", None)
    if callable(own):
        refused: object = UNCOUNTED
        if size is not None or not counted:
            try:
                refused = yield on_host(own, source, destination)
            except Exception as exc:  # noqa: BLE001 -- a turn outlives a failing plugin
                failed = f"{type(backend).__name__}.copy raised {type(exc).__name__}: {exc}"
                return None, failed, None
        return (None, str(refused), None) if refused else (size, None, None)
    return (yield from _copied_whole(backend, source, destination, last))


def _copied_whole(
    backend: Any, source: str, destination: str, last: str | None
) -> Steps[tuple[int | None, str | None, str | None]]:
    """`copied_through`, read and written whole through this process."""
    (got,) = yield reading(backend, "download_files", [source])
    if got.error or got.content is None:
        return None, got.error or "the backend handed back no content", None
    read = content_mark(got.content)
    if read == last:
        return None, SAME, read
    (put,) = yield changing(backend, "upload_files", [(destination, got.content)])
    return (None, put.error, read) if put.error else (len(got.content), None, read)


def _on_this_host(backend: Any, path: str) -> tuple[Path | None, Path] | None:
    try:
        return host_path_in_turn(backend, path)
    except HostPathFailedError:
        # Not a refusal here: the backend's own copy may still reach the file.
        return None


def copied_on_host(source: tuple[Path | None, Path], destination: tuple[Path | None, Path]) -> int:
    """Copy one file on this host, as a clone where the filesystem makes one; its size.

    Never through a link: the shell may leave one in `/outputs` naming any file this process
    can read, and a rewind would hand what it named back to the agent.
    """
    root, path = source
    # As `PermittedBackend.on_this_host` judges a tool's path: a root that answered is
    # resolved, so anything else is a link below it.
    if root is not None and path.resolve() != path:
        raise OSError(errno.ELOOP, "it is or runs through a link", str(path))
    target_root, target = destination
    # A rewind writes into `/outputs`, where the shell may have made a folder a link: through
    # it, this process would replace a file outside the session.
    if target_root is not None and target.parent.resolve() != target.parent:
        raise OSError(errno.ELOOP, "it runs through a link", str(target))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    held = os.open(path, _SOURCE)
    try:
        found = os.fstat(held)
        if not stat.S_ISREG(found.st_mode):
            raise OSError(errno.EINVAL, "it is not a regular file", str(path))
        try:
            _clone(held, target)
        except OSError:
            # Refused by a filesystem that makes no clones, as ext4 and tmpfs refuse it.
            target.unlink(missing_ok=True)
            with os.fdopen(os.dup(held), "rb") as read, target.open("xb") as write:
                shutil.copyfileobj(read, write, _CHUNK)
        return found.st_size
    finally:
        os.close(held)


def _clone(source: int, target: Path) -> None:
    """Make `target` share the open file `source`'s blocks until either changes, or raise
    `OSError`."""
    if sys.platform == "darwin":
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.fclonefileat(source, _AT_FDCWD, os.fsencode(target), 0) != 0:
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), str(target))
        return
    if sys.platform == "linux":
        import fcntl  # noqa: PLC0415 -- POSIX only, and asked only here

        made = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        try:
            fcntl.ioctl(made, _FICLONE, source)
        finally:
            os.close(made)
        return
    raise OSError(errno.ENOTSUP, "no clones on this platform", str(target))

"""The write bits on `/data`, and the only place allowed to change them.

The layer a tool-level deny rule cannot provide: the kernel enforces this
against `execute` too, where deepagents' own file permissions reach only its
built-in file tools.

One module because the rule is *nothing else chmods `/data`*. Reaching for
`sudo` when a directory refused a copy is what once left root-owned files in a
workspace and made a session permanently unusable, and a second caller doing its
own chmod is how that comes back.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def unlock_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Undo `protect_data` for one path so a sweep can finish, then retry it.

    `protect_data` drops the write bit off `data/` and everything under it, and
    deletion is governed by the *directory's* write bit rather than the file's.
    So a session that was ever given `--data` could not be removed at all: every
    reap failed on it with `Permission denied`, reported the failure, and left
    it on disk to fail again on the next sweep. Sessions that never received
    data swept fine, which is why this stayed invisible.

    Chmod belongs to this module and to nothing else -- `placement.place_data`
    says so from the other side, and the reason is that reaching for `sudo` when
    a directory refused is what once put root-owned files in a workspace. This is
    the same unlock `writable_data` does, for the one other operation that needs
    it.

    Only `PermissionError` is handled, and only by retrying once. Anything else,
    including a path we do not own and therefore cannot chmod, is re-raised for
    `sessions.LocalSessionDirs.remove_tree` to report -- degrading the same way
    `protect_data` does, since a file owned by someone else was never ours to
    delete.
    """
    if not isinstance(exc, PermissionError):
        raise exc
    try:
        _add_write_bits(Path(path).parent)
    except OSError:
        raise exc from None
    func(path)


def _unreachable(path: Path, error: OSError) -> str:
    """One path we were not allowed to touch, said in one line."""
    return f"{path.name}: {error.strerror or error}"


def protect_data(session_dir: Path) -> tuple[str, ...]:
    """Make `data/` read-only at the OS level. Idempotent.

    Returns a description of every path whose mode could not be changed, and
    an empty tuple when all of them were.

    This is the layer the tool-level deny rule cannot provide: the kernel
    enforces it against `execute` too, and filesystem permissions in deepagents
    are applied only to the built-in file tools. Directories are included
    because deletion is governed by the *directory's* write bit, not the file's.

    A path we cannot chmod is reported, not raised. `chmod` refuses anyone who
    does not own the file, so a single input copied in by another user -- a
    `sudo` run, a file restored from a backup -- used to abort the run. And it
    aborted *every* run of that session afterwards, because this happens before
    anything else, which made one root-owned file a permanent brick.

    Degrading is safe in the case that actually arises: a file owned by someone
    else is one this process cannot write either, so the mode change being
    skipped was never what protected it. Where it is not safe, the deny rule is
    still in force and the caller is told which paths are bare.
    """
    data = Path(session_dir) / "data"
    if not data.is_dir():
        return ()

    # Children first, then the directory itself.
    failures = []
    for path in (*sorted(data.rglob("*"), reverse=True), data):
        try:
            _drop_write_bits(path)
        except OSError as exc:
            failures.append(_unreachable(path, exc))
    return tuple(failures)


@contextmanager
def writable_data(session_dir: Path) -> Iterator[Path]:
    """Temporarily make `data/` writable, for loading inputs.

        with writable_data(session.directory) as data:
            shutil.copy(source, data / "sales.csv")

    Takes the session, not the workspace. `/data` is per-session -- one
    caller's data, isolated by being a different root rather than by a path
    check -- and this argument was called `workspace` for long enough that the
    smoke seeded `workspace/data`, where no agent would ever look.

    The directory itself must become writable or there is nowhere to put the
    inputs, so a failure there is raised. Existing files are best-effort for
    the same reason `protect_data` is: one we do not own is one we could not
    have overwritten anyway, and refusing to accept a new input because an
    unrelated old one is someone else's would be its own bug.
    """
    data = Path(session_dir) / "data"
    data.mkdir(parents=True, exist_ok=True)
    _add_write_bits(data)
    for path in data.rglob("*"):
        with suppress(OSError):
            _add_write_bits(path)
    try:
        yield data
    finally:
        protect_data(session_dir)

"""The write bits on `/data`, and the only place allowed to change them."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from kingfisher.layout import AGENT_TMP


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def unlock_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Undo `protect_data` for one path so a sweep can finish, then retry it."""
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


def keep_tmp_private(session_dir: Path) -> None:
    """Give the session's `TMPDIR` the mode the shared scratch directory had.

    Here rather than in `sessions`, which creates the directory, because this
    package has one module that changes a mode and
    `test_only_one_workspace_module_changes_a_mode` enforces it.

    `mkdir(mode=)` cannot do this at creation: it is masked by the umask, and
    ignored outright when the directory already exists -- which it does for
    every session made before `TMPDIR` moved inside one.

    Not a boundary, and not claimed as one. `derived/` sits beside it holding the
    same data at whatever the umask gave it, so this is continuity with what
    `prepare_scratch` did rather than a rule about who may read a session. A
    session that must be private to its uid wants a mode on the session
    directory, which is a different change from the one that moved `TMPDIR`.

    Silent when it cannot: a session directory handed over by a `SessionRoot`
    provider may be a mount whose modes are not ours to set, and refusing the
    turn over the mode of a scratch directory would be a worse answer than
    running with the mode the provider chose.
    """
    with suppress(OSError):
        (Path(session_dir) / AGENT_TMP).chmod(0o700)


def protect_data(session_dir: Path) -> tuple[str, ...]:
    """Make `data/` read-only at the OS level. Idempotent."""
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
    """Temporarily make `data/` writable, for loading inputs."""
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

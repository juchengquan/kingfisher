"""The write bits on `/inputs`, and the only place allowed to change them."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from kingfisher.layout import INPUTS, SCRATCH


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def unlock_and_retry(func: Callable[[str], object], path: str, exc: BaseException) -> None:
    """Undo `protect_inputs` for one path so a sweep can finish, then retry it."""
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

    Not a boundary, and not claimed as one. `outputs/` sits beside it holding the
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
        (Path(session_dir) / SCRATCH).chmod(0o700)


def protect_inputs(session_dir: Path) -> tuple[str, ...]:
    """Make `inputs/` read-only at the OS level. Idempotent."""
    # The layout's name and not a spelling of it: `InputsBackend` uploads under the
    # constant, and a folder named here instead is one the upload never reaches.
    inputs = Path(session_dir) / INPUTS
    if not inputs.is_dir():
        return ()

    # Children first, then the directory itself.
    failures = []
    for path in (*sorted(inputs.rglob("*"), reverse=True), inputs):
        try:
            _drop_write_bits(path)
        except OSError as exc:
            failures.append(_unreachable(path, exc))
    return tuple(failures)


@contextmanager
def writable_inputs(session_dir: Path) -> Iterator[Path]:
    """Temporarily make `inputs/` writable, for loading inputs."""
    inputs = Path(session_dir) / INPUTS
    inputs.mkdir(parents=True, exist_ok=True)
    _add_write_bits(inputs)
    for path in inputs.rglob("*"):
        with suppress(OSError):
            _add_write_bits(path)
    try:
        yield inputs
    finally:
        protect_inputs(session_dir)

"""The write bits on `/inputs`, and the only place allowed to change them."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from kingfisher.core.layout import DEFAULT_LAYOUT


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
    return f"{path.name}: {error.strerror or error}"


def keep_tmp_private(session_dir: Path, *, scratchpad: str = DEFAULT_LAYOUT.scratchpad) -> None:
    """Give the session's `TMPDIR` mode `0o700`.

    `mkdir(mode=)` cannot: the umask masks it, and an existing directory ignores it.
    Not a boundary: `outputs/` beside it holds the same data at the umask's mode, so a
    session private to its uid needs a mode on the session directory instead. Silent
    when it cannot, since the session may be a mount whose modes are not ours to set.
    """
    with suppress(OSError):
        (Path(session_dir) / scratchpad).chmod(0o700)


def protect_inputs(session_dir: Path, inputs: str = DEFAULT_LAYOUT.inputs) -> tuple[str, ...]:
    """Make `inputs/` read-only at the OS level. Idempotent."""
    # The layout's name, not a spelling of it: `InputsBackend` uploads under the same leaf.
    inputs_dir = Path(session_dir) / inputs
    if not inputs_dir.is_dir():
        return ()

    failures = []
    for path in (*sorted(inputs_dir.rglob("*"), reverse=True), inputs_dir):
        try:
            _drop_write_bits(path)
        except OSError as exc:
            failures.append(_unreachable(path, exc))
    return tuple(failures)


@contextmanager
def writable_inputs(session_dir: Path, inputs: str = DEFAULT_LAYOUT.inputs) -> Iterator[Path]:
    """Temporarily make `inputs/` writable, for loading inputs."""
    inputs_dir = Path(session_dir) / inputs
    inputs_dir.mkdir(parents=True, exist_ok=True)
    _add_write_bits(inputs_dir)
    for path in inputs_dir.rglob("*"):
        with suppress(OSError):
            _add_write_bits(path)
    try:
        yield inputs_dir
    finally:
        protect_inputs(session_dir, inputs)

"""Names that came from outside, and what may be done with them."""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class UnknownReferenceError(ValueError):
    """A store has no such reference.

    Distinct from `FileNotFoundError` on purpose: a bare one cannot be told from
    the deployment's own disk being wrong, which turns a caller naming something
    that is not there into an operator's problem.
    """


class UnsafeReferenceError(ValueError):
    """A reference names somewhere other than where it was allowed to."""


def within(root: Path, name: str) -> Path:
    """Where `name` lands under `root`, or a refusal if that is not under it."""
    parts = PurePosixPath(name).parts
    if (
        not parts
        or PurePosixPath(name).is_absolute()
        or PureWindowsPath(name).is_absolute()
        or ".." in parts
    ):
        msg = f"reference {name!r} does not name a file inside its store"
        raise UnsafeReferenceError(msg)
    return Path(root).joinpath(*parts)

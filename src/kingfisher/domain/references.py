"""Names that came from outside, and what may be done with them.

A ref is whatever a caller wrote. `FileStore` and `DefinitionStore` both take
one, both join it onto a directory, and both would let `../../etc/passwd` through
if each remembered the check separately -- so the rule is one function here
rather than a habit in two adapters.

Separate from `layout`, which is the workspace's own shape. Nothing here is
trusted.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath, PureWindowsPath


class UnknownReferenceError(ValueError):
    """A store has no such reference.

    Part of the `FileStore` and `DefinitionStore` contract rather than each
    adapter's own choice: a bare `FileNotFoundError` is indistinguishable from the
    deployment's own disk being wrong, and would answer 500 to a caller who simply
    named a file that is not there.
    """


class UnsafeReferenceError(ValueError):
    """A reference names somewhere other than where it was allowed to."""


def within(root: Path, name: str) -> Path:
    """Where `name` lands under `root`, or a refusal if that is not under it.

    Lexical, and deliberately so: the domain is not allowed to touch the
    filesystem, and `resolve` is a syscall. That is enough for the case this
    guards -- a name from a caller joined onto a directory -- because the escape
    is in the name itself.

    It is *not* enough for a symlink inside `root` that points outside it. That
    cannot be seen without asking the filesystem, so an adapter reading from a
    directory somebody else can write to has a second check to do.

    Empty names and a bare `.` are refused too, which is why the check is on
    `parts`: they would resolve to `root` itself, and a store answering "here is
    your file" with a directory is a confusion worth stopping at the edge.
    """
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

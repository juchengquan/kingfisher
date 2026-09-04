"""Copying a caller's files in: to a session's `/data`, or to one turn's input.

Two destinations and one set of rules about what may be placed. `_checked`
refuses before anything is copied, so a request naming a file that is not there
leaves nothing half-placed behind -- applying that to `/data` and not to a
turn's input was an accident of which was written first, and it cost the second
one both guarantees.

The durable half goes through `permissions.writable_data`, which is the only
sanctioned way to lift the write bits it puts back afterwards.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain.references import within
from kingfisher.infrastructure.workspace.permissions import writable_data


class DataError(ValueError):
    """A caller-supplied file cannot be placed, in `/data` or in a turn's input."""


def _checked(sources: tuple[Path, ...]) -> dict[str, Path]:
    """Every source keyed by the name it will land under.

    Raises before anything is copied, so a request that names a file twice or
    names one that is not there leaves nothing half-placed behind.

    Shared by both destinations deliberately. Every rule here is about what the
    caller asked for rather than about where it goes, so applying them to
    `/data` and not to a turn's input was an accident of which one happened to
    be written first -- and it cost the second one both guarantees.
    """
    seen: dict[str, Path] = {}
    for source in sources:
        name = Path(source).name
        if name in {"", ".", ".."}:
            msg = f"{source}: has no filename to place it under"
            raise DataError(msg)
        if not Path(source).is_file():
            msg = f"{source}: no such file"
            raise DataError(msg)
        if name in seen:
            # Keeping the last one silently loses a file the caller asked for.
            msg = f"{name}: supplied twice, from {seen[name]} and {source}"
            raise DataError(msg)
        seen[name] = Path(source)
    return seen


def check_placeable(sources: tuple[Path, ...]) -> None:
    """Raise if these files could not be placed, without placing them.

    The copying half of `place_inputs` has to happen after a turn directory
    exists, because that is where the files go. The *refusing* half must happen
    before, or a typo leaves a turn behind -- which it did: `--data` named a
    missing file and left nothing, `--input` named one and left `t001`, against
    a docstring promising neither would.

    So the check is callable on its own and the service runs it while it is
    still allowed to reject. `place_inputs` repeats it rather than trusting a
    caller to have asked, which costs three `is_file` calls and keeps the
    function safe to call directly.
    """
    _checked(sources)


def place_inputs(
    sources: tuple[Path, ...],
    input_dir: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
) -> tuple[str, ...]:
    """Copy a turn's supplied files into its `input/`, and name what landed.

    The transient counterpart to `place_data`: these belong to one turn, where
    `/data` survives into the next. That is the only difference, and it is why
    there is no `writable_data` dance here -- a turn directory is ours and was
    made moments ago.

    This lived inline in the service as a `mkdir` and a bare `shutil.copy`, one
    layer up from where the filesystem is supposed to be touched. It was the
    only place in the application layer doing its own I/O, and it had silently
    missed both of `_checked`'s guarantees for as long as it existed.
    """
    checked = _checked(sources)
    if not checked and not contents:
        return ()

    input_dir.mkdir(exist_ok=True)
    for name, source in checked.items():
        shutil.copy(source, input_dir / name)
    # Fetched by id rather than read from a path, and written here for the same
    # reason the copies are: a turn's input directory is ours and was made
    # moments ago. `within` is what makes a store-supplied key safe to join.
    for name, content in (contents or {}).items():
        within(input_dir, name).write_bytes(content)
    return tuple(checked) + tuple(contents or ())


@dataclass(frozen=True)
class DataPlacement:
    """What `place_data` did. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def place_data(
    sources: tuple[Path, ...],
    session_dir: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
) -> DataPlacement:
    """Copy caller-supplied files into a session's `/data`, and re-harden it.

    The durable counterpart to a turn's `input/`: these survive the turn and
    are there on the next one. That is the whole distinction, and the only
    reason both exist.

    Written through `writable_data`, whose `finally` drops the write bits
    again -- including when a copy raises. Nothing outside `permissions` should
    ever chmod `/data`, and that is the reason the two are separate modules:
    reaching for `sudo` when the directory refused a copy is what put root-owned
    files in a workspace and made one session unusable for good.

    Everything is checked before anything is copied -- see `_checked`, which
    the turn's input directory now shares.
    """
    if not sources and not contents:
        return DataPlacement()

    seen = _checked(sources)
    arriving = tuple(seen) + tuple(contents or ())
    existing = {p.name for p in (Path(session_dir) / "data").glob("*")}
    # One `writable_data` block for both, not two. Its `finally` drops the write
    # bits again, and taking them twice would mean a window between the two
    # where `/data` is writable for no reason.
    with writable_data(session_dir) as data:
        for name, source in seen.items():
            shutil.copy(source, data / name)
        for name, content in (contents or {}).items():
            within(data, name).write_bytes(content)

    return DataPlacement(
        placed=arriving,
        replaced=tuple(name for name in arriving if name in existing),
    )

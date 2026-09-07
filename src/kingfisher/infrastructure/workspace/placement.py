"""Copying a caller's files in: to a session's `/data`, or to one turn's input.

Two destinations and one set of rules about what may be placed. `_checked` refuses
before anything is copied, so a request naming a file that is not there leaves
nothing half-placed behind -- and it applies to both destinations, because a rule
that covers one is a rule the other quietly does without.
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
    """Every source keyed by the name it will land under."""
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
    """Raise if these files could not be placed, without placing them."""
    _checked(sources)


def place_inputs(
    sources: tuple[Path, ...],
    input_dir: Path,
    *,
    contents: Mapping[str, bytes] | None = None,
) -> tuple[str, ...]:
    """Copy a turn's supplied files into its `input/`, and name what landed."""
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
    """Copy caller-supplied files into a session's `/data`, and re-harden it."""
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

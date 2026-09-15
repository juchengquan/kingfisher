"""Copying a caller's files in: to a session's `/data`, or to one turn's input."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

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


def place_inputs(sources: tuple[Path, ...], input_dir: Path) -> tuple[str, ...]:
    """Copy a turn's supplied files into its `input/`, and name what landed."""
    checked = _checked(sources)
    if not checked:
        return ()

    input_dir.mkdir(exist_ok=True)
    for name, source in checked.items():
        shutil.copy(source, input_dir / name)
    return tuple(checked)


@dataclass(frozen=True)
class DataPlacement:
    """What `place_data` did. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def place_data(sources: tuple[Path, ...], session_dir: Path) -> DataPlacement:
    """Copy caller-supplied files into a session's `/data`, and re-harden it."""
    if not sources:
        return DataPlacement()

    seen = _checked(sources)
    existing = {p.name for p in (Path(session_dir) / "data").glob("*")}
    with writable_data(session_dir) as data:
        for name, source in seen.items():
            shutil.copy(source, data / name)

    return DataPlacement(
        placed=tuple(seen),
        replaced=tuple(name for name in seen if name in existing),
    )

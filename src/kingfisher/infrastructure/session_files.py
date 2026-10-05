"""A caller's files into a session's `/data`, and what a turn left back out of it.

Through the backend the agent runs on, never through the session directory. A
backend that runs somewhere else holds the session there, and a directory on this
host is then a place the agent never sees -- a caller's files placed where the agent
cannot read them, and a turn that wrote a report handing back nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from kingfisher.domain.result import ArtifactError
from kingfisher.infrastructure.steps import Steps, changing, on_host, reading
from kingfisher.layout import ARTIFACT_DIRS, DATA_ROUTE

_log = logging.getLogger(__name__)


class DataError(ValueError):
    """A caller-supplied file cannot be placed in a session's `/data`."""


def checked(sources: tuple[Path, ...]) -> dict[str, Path]:
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


@dataclass(frozen=True)
class DataPlacement:
    """What `place_data` did. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()


def place_data(sources: tuple[Path, ...], backend: Any) -> Steps[DataPlacement]:
    """Copy caller-supplied files into a session's `/data`, through its backend."""
    if not sources:
        return DataPlacement()
    # Before anything is read or sent: a request naming a file that is not there
    # must fail without having placed the ones that were.
    seen = yield on_host(checked, sources)
    listing = yield reading(backend, "ls", DATA_ROUTE)
    existing = {PurePosixPath(entry["path"]).name for entry in listing.entries or ()}
    contents = yield on_host(_contents_of, seen)
    answers = yield changing(backend, "upload_files", contents)
    if refused := [f"{answer.path}: {answer.error}" for answer in answers if answer.error]:
        msg = f"the session's backend refused {', '.join(refused)}"
        raise DataError(msg)
    return DataPlacement(
        placed=tuple(seen),
        replaced=tuple(name for name in seen if name in existing),
    )


def _contents_of(seen: dict[str, Path]) -> list[tuple[str, bytes]]:
    return [(f"{DATA_ROUTE}{name}", source.read_bytes()) for name, source in seen.items()]


def collect_artifacts(backend: Any) -> Steps[tuple[str, ...]]:
    """What this session holds that is worth keeping, relative to the session.

    A walk of what is there rather than a record of tool calls: `execute`
    bypasses the file tools, and running a script is how most of `/derived` gets
    produced.
    """
    found: list[str] = []
    for name in ARTIFACT_DIRS:
        result = yield reading(backend, "glob", "**", path=f"/{name}/")
        if result.error or result.truncated:
            # Reported rather than raised. The turn has already run, and failing it
            # here would lose its answer over a listing of what it left behind.
            _log.warning(
                "artifacts under /%s/ may be incomplete: %s",
                name,
                result.error or "the listing was cut short",
            )
        found.extend(
            match["path"].lstrip("/")
            for match in result.matches or ()
            if not match.get("is_dir")
        )
    return tuple(sorted(found))


def read_artifact(backend: Any, name: str) -> Steps[bytes]:
    """One file a turn produced, as bytes, by the name `collect_artifacts` gave it.

    Only under `ARTIFACT_DIRS`. The same backend reaches `/.harness` and `/data`,
    and a caller who may read what a turn produced may not read what kingfisher
    keeps about the session through the same door.
    """
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parent.parts
        or path.parts[0] not in ARTIFACT_DIRS
    ):
        msg = f"{name!r} is not an artifact: name a file under {', '.join(ARTIFACT_DIRS)}"
        raise ArtifactError(msg)
    (answer,) = yield reading(backend, "download_files", [f"/{path}"])
    if answer.error or answer.content is None:
        msg = f"{name}: {answer.error or 'no content'}"
        raise ArtifactError(msg)
    return answer.content

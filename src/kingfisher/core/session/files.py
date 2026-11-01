"""A caller's files into a session's `/inputs`, and what a turn left back out of it.

Through the backend the agent runs on, never the session directory: a backend running
elsewhere holds the session there, and a directory on this host is one the agent never sees.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import Layout
from kingfisher.core.result import ArtifactError

from .steps import Steps, changing, on_host, reading

if TYPE_CHECKING:
    from collections.abc import Mapping

# The layout arrives as an argument, never as `layout`'s route constants such as
# `INPUTS_ROUTE`: a deployment may rename the folders a caller's files cross.

_log = logging.getLogger(__name__)


class InputsError(ValueError):
    """A caller-supplied file cannot be placed in a session's `/inputs`."""


def checked(sources: tuple[Path, ...]) -> dict[str, Path]:
    """Every source keyed by the name it will land under."""
    seen: dict[str, Path] = {}
    for source in sources:
        name = Path(source).name
        if name in {"", ".", ".."}:
            msg = f"{source}: has no filename to place it under"
            raise InputsError(msg)
        if not Path(source).is_file():
            msg = f"{source}: no such file"
            raise InputsError(msg)
        if name in seen:
            # Keeping the last one silently loses a file the caller asked for.
            msg = f"{name}: supplied twice, from {seen[name]} and {source}"
            raise InputsError(msg)
        seen[name] = Path(source)
    return seen


@dataclass(frozen=True)
class InputsPlacement:
    """What `planned_inputs` found to place. `replaced` is a subset of `placed`."""

    placed: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    #: Where each placed name is read from, when `place_inputs` sends it.
    sources: Mapping[str, Path] = field(default_factory=dict)


def planned_inputs(
    sources: tuple[Path, ...], backend: Any, layout: Layout
) -> Steps[InputsPlacement]:
    """What placing these files in a session's inputs folder would place and replace.

    Changes nothing: a request refused after this must leave the session's files as they
    were.
    """
    if not sources:
        return InputsPlacement()
    # Before anything is read or sent: a request naming a file that is not there
    # must fail without having placed the ones that were.
    seen = yield on_host(checked, sources)
    listing = yield reading(backend, "ls", layout.inputs_route)
    existing = {PurePosixPath(entry["path"]).name for entry in listing.entries or ()}
    return InputsPlacement(
        placed=tuple(seen),
        replaced=tuple(name for name in seen if name in existing),
        sources=seen,
    )


def place_inputs(placement: InputsPlacement, backend: Any, layout: Layout) -> Steps[None]:
    """Copy what `planned_inputs` found into a session's inputs folder, through its backend.

    The folder this deployment named, which is not always `/inputs`: under a renamed
    layout that route is not mounted, and a write there lands in a folder nothing fences.
    """
    if not placement.placed:
        return
    contents = yield on_host(_contents_of, placement.sources, layout.inputs_route)
    answers = yield changing(backend, "upload_files", contents)
    if refused := [f"{answer.path}: {answer.error}" for answer in answers if answer.error]:
        msg = f"the session's backend refused {', '.join(refused)}"
        raise InputsError(msg)


def _contents_of(seen: Mapping[str, Path], route: str) -> list[tuple[str, bytes]]:
    return [(f"{route}{name}", source.read_bytes()) for name, source in seen.items()]


#: What a backend's listing may add to each file: any text that changes whenever the
#: file's contents do, such as an object store's ETag. Only ever compared.
CHECKSUM = "checksum"


@dataclass(frozen=True)
class InputsChanged:
    """What the inputs folder gained, had replaced and lost since the agent last heard."""

    new: tuple[str, ...] = ()
    replaced: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


def inputs_seen(backend: Any, layout: Layout) -> Steps[dict[str, str | None] | None]:
    """Each file in the inputs folder by its name there, with what tells one version of it
    from another, or `None` where the listing came back incomplete."""
    route = layout.inputs_route
    seen: dict[str, str | None] = {}
    # Two patterns, as for artifacts: `**` skips a name starting with a dot.
    for pattern in ("**", ".*"):
        result = yield reading(backend, "glob", pattern, path=route)
        if result.error or result.truncated:
            # Every file it missed would read as removed.
            return None
        for match in result.matches or ():
            if not match.get("is_dir"):
                seen[match["path"].removeprefix(route)] = _version(match)
    return seen


def _version(entry: Mapping[str, Any]) -> str | None:
    """The backend's checksum, else size and time, else `None`.

    Never the contents: a backend hands a file back only whole, so reading one to
    checksum it would hold the largest input in memory on every turn.
    """
    if checksum := entry.get(CHECKSUM):
        return f"checksum {checksum}"
    if entry.get("size") is not None and entry.get("modified_at"):
        return f"size {entry['size']} at {entry['modified_at']}"
    return None


def inputs_changed(
    before: Mapping[str, str | None] | None,
    now: Mapping[str, str | None] | None,
    placement: InputsPlacement,
) -> InputsChanged:
    """The inputs folder `now` against `before`, as the agent last heard of it.

    Without either, only what this request placed is known.
    """
    if before is None or now is None:
        return InputsChanged(
            new=tuple(name for name in placement.placed if name not in placement.replaced),
            replaced=placement.replaced,
        )

    def another(name: str) -> bool:
        was, is_now = before[name], now[name]
        if was is None or is_now is None or was.split()[0] != is_now.split()[0]:
            # Nothing to compare, but one this request wrote over is another file.
            return name in placement.replaced
        return was != is_now

    return InputsChanged(
        new=tuple(sorted(now.keys() - before.keys())),
        replaced=tuple(sorted(name for name in now.keys() & before.keys() if another(name))),
        removed=tuple(sorted(before.keys() - now.keys())),
    )


def collect_artifacts(backend: Any, layout: Layout) -> Steps[tuple[str, ...]]:
    """What this session holds that is worth keeping, relative to the session.

    A walk of what is there rather than a record of tool calls: `execute` bypasses
    the file tools, and a script produces most of `/outputs`.
    """
    found: set[str] = set()
    for name in layout.artifact_dirs:
        # Two patterns: deepagents' `**` skips a name starting with a dot, so alone it
        # drops `/outputs/.env` without a word.
        for pattern in ("**", ".*"):
            result = yield reading(backend, "glob", pattern, path=f"/{name}/")
            if result.error or result.truncated:
                # Reported, not raised: the turn has run, and failing it now would lose
                # its answer over a listing.
                _log.warning(
                    "artifacts under /%s/ may be incomplete: %s",
                    name,
                    result.error or "the listing was cut short",
                )
            found.update(
                match["path"].lstrip("/")
                for match in result.matches or ()
                if not match.get("is_dir")
            )
    return tuple(sorted(found))


def read_artifact(backend: Any, name: str, layout: Layout) -> Steps[bytes]:
    """One file a turn produced, as bytes, by the name `collect_artifacts` gave it.

    Only under the folders `collect_artifacts` walks: the same backend reaches the
    harness directory, which a caller who may read a turn's output may not.
    """
    artifacts = layout.artifact_dirs
    path = PurePosixPath(name)
    if (
        path.is_absolute()
        or ".." in path.parts
        or not path.parent.parts
        or path.parts[0] not in artifacts
    ):
        msg = f"{name!r} is not an artifact: name a file under {', '.join(artifacts)}"
        raise ArtifactError(msg)
    (answer,) = yield reading(backend, "download_files", [f"/{path}"])
    if answer.error or answer.content is None:
        msg = f"{name}: {answer.error or 'no content'}"
        raise ArtifactError(msg)
    return answer.content

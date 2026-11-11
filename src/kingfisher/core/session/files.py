"""A caller's files into a session's `/inputs`, and what a turn left back out of it.

Through the backend the agent runs on, never the session directory: a backend running
elsewhere holds the session there, and a directory on this host is one the agent never sees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, NamedTuple

from kingfisher.core.layout import Layout
from kingfisher.core.logged import warn
from kingfisher.core.result import ArtifactError

from .steps import Steps, changing, on_host, reading

if TYPE_CHECKING:
    from collections.abc import Mapping

# The layout arrives as an argument, never as one of `layout`'s route constants: a
# deployment may rename the folders a caller's files cross.


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
    seen: Mapping[str, Path], backend: Any, layout: Layout
) -> Steps[InputsPlacement]:
    """What placing the files `checked` found in a session's inputs folder would place and
    replace.

    Changes nothing: a request refused after this must leave the session's files as they
    were.
    """
    if not seen:
        return InputsPlacement()
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


#: One file a listing named: what tells its version apart, and its size where given.
type Seen = tuple[str | None, int | None]


class Walked(NamedTuple):
    """The artifact folders as one walk of them found them."""

    #: Each file by its path in the session, as `inputs_seen` marks it, with its size.
    seen: dict[str, Seen]
    #: Each folder a listing of came back failed or cut short, with which.
    cut: dict[str, str]


def walk_artifacts(backend: Any, layout: Layout) -> Steps[Walked]:
    """Each file under the artifact folders, and the folders a listing missed some of.

    A walk of what is there rather than a record of tool calls: `execute` bypasses
    the file tools, and a script produces most of `/outputs`.
    """
    seen: dict[str, Seen] = {}
    cut: dict[str, str] = {}
    for name in layout.artifact_dirs:
        # Two patterns: deepagents' `**` skips a name starting with a dot, so alone it
        # drops `/outputs/.env` without a word.
        for pattern in ("**", ".*"):
            result = yield reading(backend, "glob", pattern, path=f"/{name}/")
            if result.error or result.truncated:
                # Not `result.error`, a backend's own words, which may name what the agent
                # left there.
                cut.setdefault(
                    name, "the listing failed" if result.error else "the listing was cut short"
                )
            for match in result.matches or ():
                if not match.get("is_dir"):
                    size = match.get("size")
                    seen[match["path"].lstrip("/")] = (
                        _version(match), size if isinstance(size, int) else None
                    )
    return Walked(seen, cut)


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


def collect_artifacts(walked: Walked) -> tuple[str, ...]:
    """What this session holds that is worth keeping, relative to the session."""
    for name, why in walked.cut.items():
        # Reported, not raised: the turn has run, and failing it now would lose its answer
        # over a listing.
        warn(
            "artifacts_incomplete",
            f"artifacts under /{name}/ may be incomplete: {why}",
            folder=name,
            reason=why,
        )
    return tuple(sorted(walked.seen))


def read_artifact(backend: Any, name: str, layout: Layout) -> Steps[bytes]:
    """One file a turn produced, as bytes, by the name `collect_artifacts` gave it.

    Only under the folders `walk_artifacts` walks: the same backend reaches the
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

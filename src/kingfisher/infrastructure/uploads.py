"""Unpacking a request's own definitions into its session.

Fetching is the caller's business — kingfisher states the requirement as
`DefinitionStore` and is handed something that satisfies it. What happens here
is the part kingfisher owns: deciding where a fetched definition lands, and
refusing the ones that would land badly.

Uploads go into the session rather than being held in memory because the agent
reads skills *through the backend*, so they have to exist somewhere it can be
routed to. Subagents could have gone either way; writing both keeps
`build_agent` reading definitions off disk, which is what it already does for
the catalogue.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from kingfisher.domain import skill
from kingfisher.domain.subagent import SUFFIX
from kingfisher.infrastructure import skill_store
from kingfisher.infrastructure.definitions import read_subagent, skill_name
from kingfisher.infrastructure.subagent_store import load_all
from kingfisher.infrastructure.workspace_fs import Catalogue

if TYPE_CHECKING:
    from collections.abc import Mapping

    from kingfisher.config import Config
    from kingfisher.domain.ports import DefinitionStore
    from kingfisher.domain.request import Request


class UploadError(ValueError):
    """A definition could not be fetched, named, or safely written."""


def _write(root: Path, files: Mapping[str, bytes]) -> None:
    """Write one definition's files under `root`, refusing to escape it."""
    root.mkdir(parents=True, exist_ok=True)
    resolved_root = root.resolve()
    for relative, content in files.items():
        target = (root / relative).resolve()
        # A catalogue is a remote service, so its paths are input, not data we
        # produced. `../` in one of them would write anywhere this process can.
        if not target.is_relative_to(resolved_root):
            msg = f"{relative!r} escapes the directory it belongs to"
            raise UploadError(msg)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)


@dataclass(frozen=True)
class Brought:
    """The names a request supplied itself, by kind."""

    skills: tuple[str, ...] = ()
    subagents: tuple[str, ...] = ()


def provision(
    request: Request,
    store: DefinitionStore | None,
    session_dir: Path,
    cfg: Config,
    *,
    catalogue: Catalogue | None = None,
) -> Brought:
    """Unpack everything this request brought with it, or refuse to.

    Called before the agent is built, because the agent discovers definitions
    by reading the directories these write. Refusing here rather than later is
    deliberate: a request that names a store it was never given, or a skill
    that shadows a reviewed one, should fail before a turn directory exists.

    `catalogue` is what "already defined" is measured against, and it has to be
    the same one the agent will read. Left to `cfg` while the agent read
    somewhere else, an upload could take a name the catalogue already holds and
    the collision rule below would never see it -- which is the silent override
    it exists to refuse.
    """
    if not request.skill_refs and not request.subagent_refs:
        return Brought()
    if store is None:
        msg = "request supplies definitions by id, but no DefinitionStore is wired"
        raise UploadError(msg)

    roots = catalogue or Catalogue.from_config(cfg)
    return Brought(
        skills=materialise_skills(
            request.skill_refs, store, session_dir, skill_store.names(roots.skills)
        ),
        subagents=materialise_subagents(
            request.subagent_refs, store, session_dir, tuple(load_all(roots.subagents))
        ),
    )


def materialise_skills(
    refs: tuple[str, ...],
    store: DefinitionStore,
    session_dir: Path,
    catalogue: tuple[str, ...],
) -> tuple[str, ...]:
    """Fetch each skill and unpack it under the session. Returns their names.

    The name comes from the definition's own `name` field rather than from the
    id, because deepagents validates it against the directory name and rejects
    the skill when they differ — so there is exactly one name a skill can be
    unpacked under, and the catalogue does not get to choose it.
    """
    if not refs:
        return ()

    root = session_dir / skill.DIRECTORY / skill.UPLOADED
    names: list[str] = []
    for ref in refs:
        files = store.fetch(ref)
        body = files.get(skill.FILENAME)
        if body is None:
            msg = f"{ref}: a skill must contain {skill.FILENAME}"
            raise UploadError(msg)

        name = skill_name(body.decode("utf-8"), source=ref)
        if name in catalogue:
            # Silently overriding is what deepagents would do -- later sources
            # win -- which would let a request stand in its own definition for
            # a reviewed one under the same name.
            msg = f"{ref}: skill {name!r} is already defined by the catalogue"
            raise UploadError(msg)
        if name in names:
            msg = f"{ref}: skill {name!r} was uploaded twice in one request"
            raise UploadError(msg)

        _write(root / name, files)
        names.append(name)
    return tuple(names)


def materialise_subagents(
    refs: tuple[str, ...],
    store: DefinitionStore,
    session_dir: Path,
    catalogue: tuple[str, ...],
) -> tuple[str, ...]:
    """Fetch each subagent and unpack it under the session. Returns their names.

    A subagent is one file, and its name is the `name` field's — the filename is
    not authoritative, so the definition is parsed before it is placed.
    """
    if not refs:
        return ()

    root = session_dir / "subagents"
    names: list[str] = []
    for ref in refs:
        files = store.fetch(ref)
        if len(files) != 1:
            msg = f"{ref}: a subagent is a single file, got {len(files)}"
            raise UploadError(msg)

        (content,) = files.values()
        spec = read_subagent(content.decode("utf-8"), Path(ref))
        if spec.name in catalogue:
            msg = f"{ref}: subagent {spec.name!r} is already defined by the catalogue"
            raise UploadError(msg)
        if spec.name in names:
            msg = f"{ref}: subagent {spec.name!r} was uploaded twice in one request"
            raise UploadError(msg)

        _write(root, {f"{spec.name}{SUFFIX}": content})
        names.append(spec.name)
    return tuple(names)

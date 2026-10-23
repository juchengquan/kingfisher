"""Which skills the agent will actually have, asked of the thing that decides.

A listing of the directories holding a skill file disagrees with what deepagents loads,
so the answer comes from deepagents' own lister.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import SEPARATOR, CapabilityError
from kingfisher.core.config import ConfigError

from . import spec as skill
from .catalogue import DEEPEST, MOUNT_DEEPEST, reachable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from .catalogue import LocalSkillRepository

#: The key a qualified id travels under in deepagents' metadata, beside `name` rather
#: than replacing it: rewriting `name` would make it depend on where the folder sits.
KEY = "kingfisher_id"

#: What skills sitting directly in the root are called, when they have to be
#: told apart from a folder's. Not a folder name, because they are not in one.
CATALOGUE = "catalogue"


def qualified(source: str, name: str) -> str:
    """How a grant names one skill when the bare name is not enough."""
    return f"{source}{SEPARATOR}{name}"


def split_qualified(text: str) -> tuple[str | None, str]:
    """A written grant into the source it names and the skill it means."""
    source, found, name = text.rpartition(SEPARATOR)
    if not found:
        return None, text.strip()
    return source.strip() or None, name.strip()


def sources(root: Path) -> tuple[tuple[str, str], ...]:
    """`(label, path)` for every place a skill may sit, in a stable order.

    The root, plus each folder directly inside it that holds a skill: deepagents lists a
    source one level deep, so a folder is a source or its skills are invisible.
    """
    found = [(CATALOGUE, ROOT)]
    # None at all: deepagents logs "Cannot load skills" every turn about a missing source.
    if not root.is_dir():
        return ()
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        # A folder that is a skill is not a folder that holds them.
        if (entry / skill.FILENAME).is_file():
            continue
        if any((child / skill.FILENAME).is_file() for child in entry.iterdir() if child.is_dir()):
            # Refused, not skipped: listed under the root's label, its skills would be
            # described here while the run mounted the root's namesakes in their place.
            if entry.name == CATALOGUE:
                msg = (
                    f"the skills catalogue has a folder called {CATALOGUE!r}, which is "
                    "what the skills directly in it are called -- rename the folder"
                )
                raise ConfigError(msg)
            found.append((entry.name, f"/{entry.name}/"))
    return tuple(found)


def list_skills(backend: Any, path: str) -> list[Any]:
    """Every skill deepagents finds under one source, in its own metadata shape.

    The one call to its private lister, which `test_skill_registry` pins, so an upgrade
    that moves it breaks only here; the error returned beside the skills is already
    logged. Deferred: the light exports reach this module, and deepagents loads the
    provider SDKs.
    """
    from deepagents.middleware.skills import _list_skills_with_errors  # noqa: PLC0415

    found, _error = _list_skills_with_errors(backend, path)
    return found


async def alist_skills(backend: Any, path: str) -> list[Any]:
    """`list_skills`, for a graph run on an event loop."""
    from deepagents.middleware.skills import _alist_skills_with_errors  # noqa: PLC0415

    found, _error = await _alist_skills_with_errors(backend, path)
    return found


@dataclass(frozen=True)
class Listed:
    """One loaded skill, read off deepagents' metadata where a listing arrives."""

    name: str
    #: Where its skill file is, as the listing's backend addresses it.
    path: str
    description: str


#: The path a source is listed under when a repository is read directly, before any
#: session exists: not `SKILLS_ROUTE`, since there is no route outside a backend.
ROOT = "/"


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill the agent will be told about, and the ones it will not."""

    #: Every skill that loaded, keyed by `source::name`: two parties may both ship a skill
    #: of one name.
    offered: Mapping[str, Listed]
    unloadable: tuple[str, ...] = ()
    #: Skills deepagents filed under a name their directory does not have, as
    #: `(directory, name)`: offered under the header's name, which nobody typed.
    misfiled: tuple[tuple[str, str], ...] = ()
    #: The folders under the root that hold skills, in read order, then each mount's
    #: label. Kept here rather than recomputed, so the labels a caller types and the
    #: labels the agent loads under come from one walk.
    folders: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """What a request may write, sorted: bare where unique, qualified where not."""
        bare = {name for _, name in map(split_qualified, self.offered)}
        ambiguous = {name for name in bare if len(self._sources_of(name)) > 1}
        unique = [name for name in bare if name not in ambiguous]
        spelt_out = [key for key in self.offered if split_qualified(key)[1] in ambiguous]
        return tuple(sorted(unique + spelt_out))

    @property
    def taken(self) -> tuple[str, ...]:
        """Every name in use, unqualified, for a caller asking only "is this free"."""
        return tuple(sorted({name for _, name in map(split_qualified, self.offered)}))

    def _sources_of(self, name: str) -> tuple[str, ...]:
        """Which sources offer this bare name, in the order they were read."""
        return tuple(
            source
            for source, offered_name in map(split_qualified, self.offered)
            if offered_name == name and source is not None
        )

    @property
    def spellings(self) -> tuple[str, ...]:
        """Every spelling a grant may legally write: `names`, plus every qualified form."""
        return tuple(sorted({*self.names, *self.offered}))

    def identity(self, written: str) -> str | None:
        """The one skill this grant means, or `None` when it means no one skill."""
        source, name = split_qualified(written)
        if source is not None:
            return written if written in self.offered else None
        found = self._sources_of(name)
        return qualified(found[0], name) if len(found) == 1 else None

    def resolve(self, written: str) -> str:
        """One grant into the `source::name` it means, or a refusal saying why."""
        if (found := self.identity(written)) is not None:
            return found

        source, name = split_qualified(written)
        if source is not None:
            msg = (
                f"no skill {name!r} in {source!r}; the definitions offer "
                f"{self.names}"
            )
            raise CapabilityError(msg)

        sources = self._sources_of(name)
        if not sources:
            msg = f"unknown skill: {name!r}; the definitions offer {self.names}"
            raise CapabilityError(msg)
        # Several, necessarily: a new reason for `identity` to return `None` needs its own
        # branch above, or it is reported as an ambiguity.
        spelt = ", ".join(qualified(s, name) for s in sorted(sources))
        msg = (
            f"{name!r} is offered by more than one source, so naming it alone "
            f"would silently pick one: write {spelt}"
        )
        raise CapabilityError(msg)

    def description(self, written: str) -> str:
        """What a skill says it is for. Empty for anything this does not hold."""
        key = self.identity(written)
        return self.offered[key].description if key is not None and key in self.offered else ""


def read(repository: LocalSkillRepository) -> SkillRegistry:
    """Ask deepagents what this repository offers."""
    # Deferred: deepagents loads the provider SDKs.
    from deepagents.backends import FilesystemBackend  # noqa: PLC0415

    root = repository.root
    # A subagent's own folder is one source, as its index will be: read as the catalogue
    # is, a skill in a subfolder would be granted to a subagent never shown it.
    found_sources = ((CATALOGUE, ROOT),) if repository.flat else sources(root)
    # `(directory, prefix, sources, deepest)`: the root, then each mount as one source.
    places = [
        (root, "", found_sources, MOUNT_DEEPEST if repository.flat else DEEPEST),
        *(
            (mount, f"/{label}", ((label, ROOT),), MOUNT_DEEPEST)
            for label, mount in repository.mounts.items()
        ),
    ]

    # One listing per source, kept apart: deepagents merges by name and lets the last win.
    offered: dict[str, Listed] = {}
    loaded: list[Listed] = []
    missing: list[str] = []
    for directory, prefix, labelled, deepest in places:
        backend = FilesystemBackend(root_dir=str(directory))
        here: list[Listed] = []
        for label, path in labelled:
            for one in list_skills(backend, path):
                # The prefix is the mount's route segment: without it a mounted skill's deny
                # rule names a path that does not exist, and the skill stays readable.
                entry = Listed(
                    name=one["name"],
                    path=f"{prefix}{one['path']}",
                    description=one["description"],
                )
                here.append(entry)
                offered[qualified(label, entry.name)] = entry
        loaded.extend(here)

        # Directories that looked like a skill and did not load. Compared by directory, not
        # name: sources under one root share this list, and two may ship one name. The two
        # sides differ on purpose: `Listed.path` carries the mount prefix, and
        # `relative_to` does not.
        kept = {str(PurePosixPath(one.path).parent).lstrip("/") for one in here}
        missing.extend(
            where
            for found in reachable(directory, deepest)
            if (where := str(PurePosixPath(prefix.lstrip("/")) / found.relative_to(directory)))
            not in kept
        )
    # A skill whose header names something its directory does not: deepagents files it
    # under the header, so a caller typing the directory name gets "unknown skill".
    misfiled = tuple(
        sorted(
            (directory, one.name)
            for one in loaded
            if (directory := PurePosixPath(one.path).parent.name) != one.name
        )
    )
    return SkillRegistry(
        offered=offered,
        unloadable=tuple(sorted(missing)),
        misfiled=misfiled,
        folders=(
            *(label for label, _ in found_sources if label != CATALOGUE),
            *repository.mounts,
        ),
    )

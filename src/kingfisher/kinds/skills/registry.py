"""Which skills the agent will actually have, asked of the thing that decides.

Two readers looked at this catalogue and did not agree. Kingfisher listed the
directories holding a skill file; deepagents opened each one and kept the ones it
could parse. Measured on four directories that all held one, kingfisher advertised
four names and deepagents loaded three -- two of them different.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.domain.capabilities import SEPARATOR, CapabilityError
from kingfisher.kinds.skills import spec as skill
from kingfisher.kinds.skills.catalogue import DEEPEST, MOUNT_DEEPEST, reachable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from kingfisher.domain.ports import SkillRepository

# `SEPARATOR` is imported above rather than defined here. It was written out
# again with a comment saying it matched the separator tools already use, which
# is a claim a copied literal cannot keep. `kinds.tools.spec` had already made
# this move and said why: one separator both kinds import beats two that agree
# by coincidence. Re-exported by the import, so `skills.registry.SEPARATOR`
# still resolves for readers who look here first.

#: The key a qualified id travels under inside deepagents' own metadata. Added
#: beside `name` rather than replacing it: `name` is what the file says and what
#: deepagents validated against the directory, and rewriting it would make a
#: skill's name depend on where its folder sits.
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


def sources(root: Path | None) -> tuple[tuple[str, str], ...]:
    """`(label, path)` for every place a skill may sit, in a stable order.

    The root itself, plus each folder directly inside it that holds a skill. That is
    the only shape deepagents offers below the top level -- it lists a source one
    level deep and no further -- so a folder is a source or its skills are invisible.
    Measured: one root source finds nothing in a nested layout; three folder sources
    find all nine.
    """
    found = [(CATALOGUE, ROOT)]
    if root is None or not root.is_dir():
        return tuple(found)
    for entry in sorted(p for p in root.iterdir() if p.is_dir()):
        # A folder that *is* a skill is not a folder that *holds* them. Both
        # look like a directory under the root, and the difference is one level
        # down: a skill has the file itself, a source has directories that do.
        if (entry / skill.FILENAME).is_file():
            continue
        if any((child / skill.FILENAME).is_file() for child in entry.iterdir() if child.is_dir()):
            found.append((entry.name, f"/{entry.name}/"))
    return tuple(found)


def listed(backend: Any, path: str) -> list[Any]:
    """Every skill deepagents finds under one source, in its own metadata shape.

    The one call to its private lister, which `test_skill_registry` pins, so an
    upgrade that moves it breaks here and nowhere else. The error it returns beside
    the skills is one it has already logged. Deferred, because `Definitions` holds a
    registry and is reachable from `kingfisher`'s light exports: at module scope this
    import would make `from kingfisher import Config` load three provider SDKs.
    """
    from deepagents.middleware.skills import _list_skills_with_errors  # noqa: PLC0415

    found, _error = _list_skills_with_errors(backend, path)
    return found


async def alisted(backend: Any, path: str) -> list[Any]:
    """`listed`, for a graph run on an event loop."""
    from deepagents.middleware.skills import _alist_skills_with_errors  # noqa: PLC0415

    found, _error = await _alist_skills_with_errors(backend, path)
    return found


@dataclass(frozen=True)
class Listed:
    """One loaded skill, as the three things read from deepagents' metadata for it.

    Built where a listing arrives, so the keys of a dictionary this package does not
    own are spelled in one function rather than wherever an entry is read.
    """

    name: str
    #: Where its skill file is, as the listing's backend addresses it.
    path: str
    description: str


#: The path a source is listed under when reading a repository directly. Not
#: `SKILLS_ROUTE`: this reads a catalogue on its own, before any session exists
#: and outside the backend an agent will eventually get, so there is no route to
#: speak of -- only the root of whatever is mounted.
ROOT = "/"


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill the agent will be told about, and the ones it will not."""

    #: Every skill that loaded, keyed by `source::name`. Keyed that way rather
    #: than by name because a name is no longer unique: two parties who never
    #: met can both ship a `lookup`, and keying by name is exactly the collapse
    #: this exists to undo.
    offered: Mapping[str, Listed]
    unloadable: tuple[str, ...] = ()
    #: Skills deepagents filed under a name their directory does not have, as
    #: `(directory, name)`. Loaded, offered, and reachable -- under the name in the
    #: header, which is the one nobody typed.
    misfiled: tuple[tuple[str, str], ...] = ()
    #: The folders under the catalogue root that hold skills, in the order they
    #: were read, then the label of each mount. What `skills_sources` turns into
    #: one source each -- kept here rather than recomputed there so the labels a
    #: caller types and the labels the agent loads under come from one walk.
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
        """Every spelling a grant may legally write, for a caller checking one.

        `names` is what a listing shows and is not that set: it gives the bare
        name wherever it is unique, and the qualified form is legal there too --
        it is the skill's identity, and the one spelling that still means the
        same skill after somebody else ships that name.
        """
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
                f"no skill {name!r} in {source!r}; this workspace offers "
                f"{self.names}"
            )
            raise CapabilityError(msg)

        sources = self._sources_of(name)
        if not sources:
            msg = f"unknown skill: {name!r}; this workspace offers {self.names}"
            raise CapabilityError(msg)
        # More than one, necessarily: `identity` resolves the single-source case
        # above, so reaching here with any source at all means several. A further
        # reason for `identity` to return `None` needs its own branch, or it is
        # reported as an ambiguity it is not.
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


def read(repository: SkillRepository) -> SkillRegistry:
    """Ask deepagents what this repository offers."""
    # Deferred for the reason `listed` gives.
    from deepagents.backends import FilesystemBackend  # noqa: PLC0415

    root = repository.root
    found_sources = sources(root)
    # `(directory, prefix, sources, deepest)`: the root, then each mount as a single
    # source at its own root.
    places = [
        (root, "", found_sources, DEEPEST),
        *(
            (mount, f"/{label}", ((label, ROOT),), MOUNT_DEEPEST)
            for label, mount in repository.mounts.items()
        ),
    ]

    # One listing per source, kept apart. deepagents merges them by name and
    # lets the last win, which is the collapse this exists to undo: two parties
    # who never met can both ship a `lookup`, and being told about one of them
    # is worse than being told about neither.
    offered: dict[str, Listed] = {}
    loaded: list[Listed] = []
    missing: list[str] = []
    for directory, prefix, labelled, deepest in places:
        backend = FilesystemBackend(root_dir=str(directory))
        here: list[Listed] = []
        for label, path in labelled:
            for one in listed(backend, path):
                # The prefix is the mount's route segment, and it is what the deny
                # rule for an unactivated skill is built from. Without it, a mounted
                # skill's rule names `/skills/<name>/**`, a path that does not exist,
                # and the skill stays readable.
                entry = Listed(
                    name=one["name"],
                    path=f"{prefix}{one['path']}",
                    description=one["description"],
                )
                here.append(entry)
                offered[qualified(label, entry.name)] = entry
        loaded.extend(here)

        # A directory that looked like a skill and did not come back. deepagents says
        # why in a warning it logs; what matters here is only which ones, so a reader
        # can go and look at the file rather than wonder why a skill they wrote is not
        # on offer.
        kept = {one.path for one in here}
        missing.extend(
            str(PurePosixPath(prefix.lstrip("/")) / found.relative_to(directory))
            for found in reachable(directory, deepest)
            if not any(f"/{found.name}/" in path for path in kept)
        )
    # A skill whose header names something its directory does not. deepagents files it
    # under the header and logs a warning nobody reads, so `--list` shows a name that is
    # not in the tree and a caller who typed the directory name gets "unknown skill" for
    # a skill that is plainly there.
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

"""Which skills the agent will actually have, asked of the thing that decides.

Two readers looked at this catalogue and did not agree. Kingfisher listed the
directories holding a skill file; deepagents opened each one and kept the ones
it could parse. Measured on four directories that all held one, kingfisher
advertised four names and deepagents loaded three -- two of them different.

The consequence was the failure this codebase refuses everywhere else, sitting
in the one kind it never parses. A skill with no `description` is dropped by
deepagents and advertised by kingfisher, so activating it passed validation,
allowed the name through the filter, and produced an agent with no skills at
all. Nothing said so.

So this asks deepagents. Not "parses a skill the same way deepagents does" --
that is a second opinion wearing the first one's clothes, and it would put the
name rules, the description limit and the size cap in this repo to drift against
an upstream that owns them. `SkillRepository` still answers `names` and `files`,
because *what exists to mount* is a different question from *what the agent will
be told about*, and running the two together is what produced the bug.

The private call is the price, and it is one this codebase already pays
knowingly: `WorkspaceScopedBackend` overrides `_get_backend_and_key` and says
so, pinned by a test "so a deepagents upgrade that renames it fails the build
rather than quietly removing the guard". The same applies here with more force.
A registry that silently came back empty would be the original bug again, in a
new place -- so `test_skill_registry` pins the import.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.domain.capabilities import SEPARATOR, CapabilityError
from kingfisher.domain.layout import UPLOADED_SKILL_DIR as UPLOADED
from kingfisher.skills import spec as skill
from kingfisher.skills.catalogue import reachable

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from kingfisher.domain.ports import SkillRepository

# `SEPARATOR` and `UPLOADED` are imported above rather than defined here.
# Both were written out again with a comment saying they matched something
# else -- the separator tools already use, and the string the backend mounts
# uploads at -- which is a claim a copied literal cannot keep. `tools.spec`
# had already made this exact move for the separator and said why: one
# separator both kinds import beats two that agree by coincidence. It named
# skills as the other kind; this is skills. Re-exported by the import, so
# `skills.registry.SEPARATOR` still resolves for readers who look here first.

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
    """A written grant into the source it names and the skill it means.

    `None` for the source when the short form was used, which stays valid
    wherever a name is unique -- every catalogue that has no collisions, which
    is all of them until somebody assembles one from two places.
    """
    source, found, name = text.rpartition(SEPARATOR)
    if not found:
        return None, text.strip()
    return source.strip() or None, name.strip()


def sources(root: Path | None) -> tuple[tuple[str, str], ...]:
    """`(label, path)` for every place a skill may sit, in a stable order.

    The root itself, plus each folder directly inside it that holds a skill.
    That is the only shape deepagents offers below the top level -- it lists a
    source one level deep and no further -- so a folder is a source or its
    skills are invisible. Measured: one root source finds nothing in a nested
    layout; three folder sources find all nine.

    No extra backend route is needed. These are paths *inside* the one
    `/skills/` mount, which is why folders cost a prompt line each and nothing
    more.

    Takes the directory rather than the repository, because a repository is not
    what is being asked: a store-backed catalogue has no folders to find, hands
    over skills by name, and a name has no folder in it. That is not a gap being
    papered over -- it is the root and nothing else, correctly.
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


#: The path a source is listed under when reading a repository directly. Not
#: `SKILLS_ROUTE`: this reads a catalogue on its own, before any session exists
#: and outside the backend an agent will eventually get, so there is no route to
#: speak of -- only the root of whatever is mounted.
ROOT = "/"


@dataclass(frozen=True)
class SkillRegistry:
    """Every skill the agent will be told about, and the ones it will not.

    `offered` is the answer to "what may a request activate", and it is the
    answer deepagents itself gave -- keyed by the name it will list the skill
    under, which is the name in the header rather than the folder. Those differ
    more often than they should, and when they do it is the header that wins.

    `unloadable` is the gap between what looks like a skill on disk and what
    deepagents kept. It is reported rather than refused, the way `misplaced`
    already is: one malformed skill should not stop a deployment starting, and
    the dangerous half -- a caller *naming* one -- is refused by validation
    reading `offered` instead of a directory listing.
    """

    #: Every skill that loaded, keyed by `source::name`. Keyed that way rather
    #: than by name because a name is no longer unique: two parties who never
    #: met can both ship a `lookup`, and keying by name is exactly the collapse
    #: this exists to undo.
    offered: Mapping[str, Any]
    unloadable: tuple[str, ...] = ()
    #: Skills deepagents filed under a name their directory does not have, as
    #: `(directory, name)`. Loaded, offered, and reachable -- under the name in
    #: the header, which is the one nobody typed.
    #:
    #: Not `unloadable`, and the difference is the whole reason this is separate:
    #: one is a skill the agent will never hear about, the other is a skill it
    #: will hear about under another name. Reported rather than refused, because
    #: deepagents accepts it and refusing here would make a working catalogue
    #: fail to start over a spelling.
    misfiled: tuple[tuple[str, str], ...] = ()
    #: The folders under the catalogue root that hold skills, in the order they
    #: were read. What `skills_sources` turns into one source each -- kept here
    #: rather than recomputed there so the labels a caller types and the labels
    #: the agent loads under come from one walk.
    folders: tuple[str, ...] = ()

    def merged(self, other: SkillRegistry) -> SkillRegistry:
        """This registry and one more, as the single answer a caller needs.

        The catalogue is read once when a deployment is wired; a request's own
        skills arrive per turn. Two registries because they are read at
        different times -- one answer because "what may this request activate"
        has to have exactly one, and the last time it had two they disagreed:
        validation offered an uploaded skill and the build refused it as
        unknown, so no upload could be activated at all.

        `folders` comes from this side alone. It says which folders under the
        *catalogue* root are their own source, and uploads have none.
        """
        return SkillRegistry(
            offered={**self.offered, **other.offered},
            unloadable=tuple(sorted({*self.unloadable, *other.unloadable})),
            misfiled=tuple(sorted({*self.misfiled, *other.misfiled})),
            folders=self.folders,
        )

    @property
    def names(self) -> tuple[str, ...]:
        """What a request may write, sorted: bare where unique, qualified where not.

        Both forms appear for a colliding name, because a caller reading this
        needs to see that the short one is gone and what replaced it. A
        catalogue with no collisions -- every one that exists today -- lists
        exactly the bare names it always did.
        """
        bare = {name for _, name in map(split_qualified, self.offered)}
        ambiguous = {name for name in bare if len(self._sources_of(name)) > 1}
        unique = [name for name in bare if name not in ambiguous]
        spelt_out = [key for key in self.offered if split_qualified(key)[1] in ambiguous]
        return tuple(sorted(unique + spelt_out))

    @property
    def taken(self) -> tuple[str, ...]:
        """Every name in use, unqualified, for a caller asking only "is this free".

        Distinct from `names`, which answers "what may a request write" and so
        spells a colliding name out. This answers "is this name spoken for
        anywhere", which is what an *upload* needs: a request may not call its
        own skill `lookup` because the catalogue has one, and it makes no
        difference to that whether the catalogue's sits in a folder.

        Reading `names` for this was the bug -- it hands back `research::lookup`
        for a foldered skill, which no upload will ever be called.
        """
        return tuple(sorted({name for _, name in map(split_qualified, self.offered)}))

    def _sources_of(self, name: str) -> tuple[str, ...]:
        """Which sources offer this bare name, in the order they were read.

        A key with no source is skipped rather than counted as one: every key
        `read` writes is qualified, so an unqualified one came from a registry
        built by hand and belongs to no party. Counting it would make a name
        look ambiguous with itself.
        """
        return tuple(
            source
            for source, offered_name in map(split_qualified, self.offered)
            if offered_name == name and source is not None
        )

    def resolve(self, written: str) -> str:
        """One grant into the `source::name` it means, or a refusal saying why.

        Three outcomes and each is a different message, because "no such skill"
        and "which one did you mean" send a reader to different places.
        """
        source, name = split_qualified(written)
        if source is not None:
            if written in self.offered:
                return written
            msg = (
                f"no skill {name!r} in {source!r}; this workspace offers "
                f"{self.names}"
            )
            raise CapabilityError(msg)

        found = self._sources_of(name)
        if not found:
            msg = f"unknown skill: {name!r}; this workspace offers {self.names}"
            raise CapabilityError(msg)
        if len(found) > 1:
            spelt = ", ".join(qualified(s, name) for s in sorted(found))
            msg = (
                f"{name!r} is offered by more than one source, so naming it alone "
                f"would silently pick one: write {spelt}"
            )
            raise CapabilityError(msg)
        return qualified(found[0], name)

    def description(self, written: str) -> str:
        """What a skill says it is for. Empty for anything this does not hold."""
        key = written if written in self.offered else None
        if key is None:
            found = self._sources_of(written)
            key = qualified(found[0], written) if len(found) == 1 else None
        return str(self.offered.get(key, {}).get("description", "")) if key else ""


def read_uploaded(root: Path | None) -> SkillRegistry:
    """The skills this request brought with it, asked of the same reader.

    Their own function rather than a flag on `read`, because they answer a
    different question about a different directory: the catalogue is read once
    when a deployment is wired and cached for its life, and these arrive per
    request and are gone when the session is.

    Asked of deepagents for the same reason the catalogue is. A request may
    upload a skill deepagents will not load -- one with no `description` is the
    easy case -- and until this existed such a skill was written to disk,
    advertised by a directory listing, accepted by the build, and then absent
    from an agent that reported nothing wrong. That is the exact failure this
    module was created to remove, still live in the half it did not cover.

    Flat, with no folder sources, and that is a property of how uploads are
    written rather than a limitation: `materialise_skills` files each one under
    the name in its own header, directly under the uploads directory. There is
    nowhere for a folder to come from.
    """
    if root is None or not root.is_dir():
        return SkillRegistry(offered={})

    from deepagents.backends import FilesystemBackend  # noqa: PLC0415
    from deepagents.middleware.skills import _list_skills_with_errors  # noqa: PLC0415

    loaded, _error = _list_skills_with_errors(FilesystemBackend(root_dir=str(root)), ROOT)
    kept = {one["path"] for one in loaded}
    return SkillRegistry(
        offered={qualified(UPLOADED, one["name"]): one for one in loaded},
        unloadable=tuple(
            sorted(
                directory.name
                for directory in reachable(root)
                if not any(f"/{directory.name}/" in path for path in kept)
            )
        ),
    )


def read(repository: SkillRepository, *, root: Path | None = None) -> SkillRegistry:
    """Ask deepagents what this repository offers.

    `root` is that repository's directory when it has one, and it is passed in
    rather than asked for: `catalogue_root` lives in `catalogue`, and importing
    it here would have `catalogue` and this module import each other. Reading a
    repository does not need to know what a catalogue is.

    A repository with a real directory behind it is read as a filesystem,
    because `skills.backend`
    says why not to do otherwise -- a store "holds every skill's contents for
    the life of the deployment", which is a copy worth making only when there is
    no path to read instead.

    A source-level failure -- the directory is missing, the store cannot be
    reached -- comes back as no skills rather than an exception, because that is
    what `_list_skills_with_errors` reports and because an empty catalogue is a
    thing a deployment may legitimately have. What it must not be is an empty
    catalogue nobody mentioned, which is why `unloadable` carries the difference
    and `--list` prints it.
    """
    # Deferred, and the architecture test is why: `Definitions` holds a registry
    # and `Definitions` is reachable from `kingfisher`'s light exports, so a
    # module-scope import here would make `from kingfisher import Config` load
    # three provider SDKs. The lister is private, which is the coupling
    # `test_skill_registry` pins -- a rename upstream fails there rather than
    # emptying this registry in silence.
    from deepagents.backends import FilesystemBackend  # noqa: PLC0415
    from deepagents.middleware.skills import (  # noqa: PLC0415
        _list_skills_with_errors,
    )

    from kingfisher.skills.backend import skills_backend  # noqa: PLC0415

    backend = FilesystemBackend(root_dir=str(root)) if root else skills_backend(repository)

    # One listing per source, kept apart. deepagents merges them by name and
    # lets the last win, which is the collapse this exists to undo: two parties
    # who never met can both ship a `lookup`, and being told about one of them
    # is worse than being told about neither.
    offered: dict[str, Any] = {}
    loaded: list[Any] = []
    found_sources = sources(root)
    for label, path in found_sources:
        found, _source_error = _list_skills_with_errors(backend, path)
        loaded.extend(found)
        for one in found:
            offered[qualified(label, one["name"])] = one

    # A directory that looked like a skill and did not come back. deepagents says
    # why in a warning it logs; what matters here is only which ones, so a
    # reader can go and look at the file rather than wonder why a skill they
    # wrote is not on offer.
    #
    # Walked rather than taken from `repository.names`, which lists the root and
    # stops. That was right while every skill sat at the root; with folders it
    # made a broken *nested* skill invisible -- dropped by deepagents, absent
    # from `names`, so reported by nobody. Precisely the silence this registry
    # exists to end, and it would have reopened it one directory down.
    kept = {one["path"] for one in loaded}
    missing = tuple(
        sorted(
            str(directory.relative_to(root))
            for directory in reachable(root)
            if not any(f"/{directory.name}/" in path for path in kept)
        )
        if root
        else sorted(name for name in repository.names if not any(f"/{name}/" in p for p in kept))
    )
    # A skill whose header names something its directory does not. deepagents
    # files it under the header and logs a warning nobody reads, so `--list`
    # shows a name that is not in the tree and a caller who typed the directory
    # name gets "unknown skill" for a skill that is plainly there.
    #
    # Read off what came back rather than by parsing again: `path` is where the
    # file is and `name` is what deepagents decided to call it, which is exactly
    # the pair that can disagree.
    misfiled = tuple(
        sorted(
            (directory, one["name"])
            for one in loaded
            if (directory := PurePosixPath(one["path"]).parent.name) != one["name"]
        )
    )
    return SkillRegistry(
        offered=offered,
        unloadable=missing,
        misfiled=misfiled,
        folders=tuple(label for label, _ in found_sources if label != CATALOGUE),
    )

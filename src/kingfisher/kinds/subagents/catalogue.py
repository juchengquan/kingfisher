"""Subagent definitions held in a directory on this host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.kinds.documents import documents_in
from kingfisher.kinds.importing import (
    Export,
    exported_from,
    modules_in,
    skipped,
)
from kingfisher.kinds.subagents import reading
from kingfisher.kinds.subagents.spec import EXPORT, SubagentError, SubagentSpec, declared
from kingfisher.kinds.tools.catalogue import CarriedTools, LocalToolRepository
from kingfisher.kinds.tools.spec import SEPARATOR, reference

if TYPE_CHECKING:
    from kingfisher.domain.ports import ToolRepository

#: What a bundle's own assets are kept in, and therefore the two directory names that
#: are not organisation here. A folder under `subagents/` is normally free -- it groups
#: definitions and nothing else -- but these two hold a subagent's private tools and
#: skills, and the definition walk must not descend into them.
ASSET_DIRECTORIES: frozenset[str] = frozenset({"tools", "skills"})

DECLARES = Export(EXPORT, error=SubagentError, holding="subagents", example="my_subagent")


def _declared_in(directory: Path) -> list[tuple[SubagentSpec, str]]:
    """Every subagent a module under `directory` declares, with where it came from."""
    found: list[tuple[SubagentSpec, str]] = []
    for path in modules_in(directory):
        relative = path.relative_to(directory)
        # The Python half of what `documents_in` is told to skip, and it has to be
        # here rather than in `modules_in`: that walk is shared with the tool
        # catalogue, where a folder called `tools` is ordinary organisation.
        # Filtered after the walk rather than during it because the walk imports
        # nothing -- `exported_from` does, further down -- so a module under a
        # bundle's `tools/` is dropped before anything executes it.
        if ASSET_DIRECTORIES & set(relative.parts):
            continue
        where = str(relative) + ("/" if path.is_dir() else "")
        exported = exported_from(path, where=where, declares=DECLARES)
        found.extend((declared(entry, where), where) for entry in exported)
    return found


@dataclass(frozen=True)
class Holdings:
    """The tools and skills that belong to one subagent and to nothing else.

    Named for the goods rather than for the bundle, because `SubagentSpec.bundled` is
    the other half of the pair and is not these: it is what the definition lists from
    the folder, checked against this. One word would put the list and the goods a line
    apart in `rules.miscounted`, which exists to tell them apart.
    """

    name: str
    where: str
    #: The folder these are in, for the usual backing. `None` for a definition that
    #: carried them instead, which is what a subagent imported from an installed
    #: package does: it has no folder under this catalogue to be named after.
    root: Path | None = None
    #: What it carried, by the same two halves a folder has. Tools arrive as the
    #: objects; skills as the directory the definition resolved for itself, since a
    #: skill is files deepagents mounts and there is nothing else to hand it.
    carried: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tools(self) -> ToolRepository | None:
        """This subagent's own tools, as the repository everything downstream asks.

        A repository rather than the directory it used to be, because the two
        backings have nothing else in common: one is a folder to walk, the other is
        objects already in hand. Both answer `found`, so this is the narrowest thing
        that lets a carried bundle reach the consumers a folder reaches.
        """
        if "tools" in self.carried:
            return CarriedTools(tuple(self.carried["tools"]), source=self.where)
        found = self._asset("tools")
        return None if found is None else LocalToolRepository(found)

    @property
    def skills(self) -> Path | None:
        """This subagent's skill directory, when it has one.

        A path for either backing, and not for want of symmetry with `tools`:
        deepagents mounts a skills source by path, so a carried bundle has to name a
        real directory too. What differs is who resolved it -- the catalogue walk, or
        the definition itself.
        """
        carried = self.carried.get("skills")
        if carried is not None:
            return Path(carried)
        return self._asset("skills")

    def _asset(self, kind: str) -> Path | None:
        if self.root is None:
            return None
        found = self.root / kind
        return found if found.is_dir() else None


def _bundle_of(spec: SubagentSpec, where: str, root: Path, key: str) -> Holdings | None:
    """What a definition owns: what it carried, or the folder named after it."""
    if spec.carried:
        # Keyed as a grant names it, because that is the one label guaranteed unique
        # -- two packages may each ship a `reviewer`, and the catalogue has already
        # told those apart by the file each came through. A route may not hold the
        # separator, so it travels as a dash.
        return Holdings(
            name=spec.name, where=key.replace(SEPARATOR, "-"), carried=spec.carried
        )
    parent = Path(where).parent
    # A loose definition directly under the catalogue has no folder to be named
    # after, which `parent.name` reports as the empty string.
    if not parent.name or parent.name != spec.name:
        return None
    return Holdings(name=spec.name, root=root / parent, where=str(parent))


@dataclass(frozen=True)
class LocalSubagentRepository:
    """The subagents defined in one directory."""

    root: Path

    @cached_property
    def _defined(self) -> dict[str, tuple[SubagentSpec, str]]:
        """Every definition below `root`, parsed once, with where it came from."""
        directory = Path(self.root)
        if not directory.is_dir():
            return {}

        # Two folders may each hold a `profiler.yaml`, and this used to refuse
        # the pair -- which stopped the whole catalogue loading over a clash no
        # single agent had yet asked for, and was unfixable by anyone who owned
        # neither file. The catalogue keeps both now, under the reference a
        # grant writes, and the refusal moved to where the constraint lives: an
        # agent's roster is keyed by name, so an *agent* holding two is refused.
        #
        # Measured, because it is the reason any of this is needed: handing
        # deepagents two subagents called `profiler` compiles one. No error, and
        # the other simply never exists.
        read: list[tuple[SubagentSpec, str]] = []
        for path in documents_in(directory, error=SubagentError, skipping=ASSET_DIRECTORIES):
            # Relative to the catalogue: `reviewer.yaml` stops identifying a
            # file once two folders may each hold one.
            where = str(path.relative_to(directory))
            read.append((reading.read(path), where))
        # The Python half, keyed and counted with the documents rather than
        # beside them: the two kinds share one namespace, so two definitions
        # claiming `reviewer` are told apart the same way whichever formats they
        # were written in.
        read.extend(_declared_in(directory))

        counted: dict[str, int] = {}
        for spec, _ in read:
            counted[spec.name] = counted.get(spec.name, 0) + 1
        return {
            (reference(where, spec.name) if counted[spec.name] > 1 else spec.name): (spec, where)
            for spec, where in read
        }

    @cached_property
    def specs(self) -> dict[str, SubagentSpec]:
        """Every subagent defined here, keyed as a grant would name it."""
        return {name: spec for name, (spec, _) in self._defined.items()}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._defined)

    @cached_property
    def bundles(self) -> dict[str, Holdings]:
        """Each subagent's own tools and skills, by the name a grant would use."""
        found: dict[str, Holdings] = {}
        holders: dict[str, list[str]] = {}
        for key, (spec, where) in self._defined.items():
            bundle = _bundle_of(spec, where, Path(self.root), key)
            if bundle is None:
                continue
            found[key] = bundle
            # Only the folder-backed ones are counted here. A carried bundle's
            # `where` is a label rather than a directory, so booking it in would
            # invent a folder and then refuse the next definition to land in it.
            if bundle.root is not None:
                holders.setdefault(bundle.where, []).append(where)

        # Every definition under a bundle folder, not only the ones named after
        # it: the neighbour is the whole problem, and it never gets a bundle of
        # its own to be counted by the loop above.
        for _, where in self._defined.values():
            parent = str(Path(where).parent)
            if parent in holders and where not in holders[parent]:
                holders[parent].append(where)

        for folder, definitions in sorted(holders.items()):
            if len(definitions) > 1:
                msg = (
                    f"{folder}/ is {Path(folder).name}'s own folder and also holds "
                    f"{', '.join(sorted(definitions))} -- a bundle is one subagent's, "
                    f"so move the others out or rename the folder, since what is in "
                    f"{folder}/tools and {folder}/skills reaches whichever of them "
                    "the folder belongs to"
                )
                raise SubagentError(msg)
        return found

    @cached_property
    def orphaned_assets(self) -> tuple[str, ...]:
        """Folders holding `tools/` or `skills/` that no definition is named for."""
        directory = Path(self.root)
        if not directory.is_dir():
            return ()
        owned = {bundle.where for bundle in self.bundles.values()}
        # And the folder a carried bundle pointed at, when it pointed inside this
        # catalogue. A definition that carries names its skills by absolute path
        # rather than by sitting next to them, so the folder is reached by nothing
        # this walk can see -- and an example arranged the way a package is would be
        # reported as abandoned while the delegate reading it works.
        for bundle in self.bundles.values():
            skills = bundle.skills
            if bundle.root is not None or skills is None:
                continue
            if skills.is_relative_to(directory):
                owned.add(str(skills.parent.relative_to(directory)))
        return tuple(
            sorted(
                str(entry.relative_to(directory))
                for entry in directory.rglob("*")
                if entry.is_dir()
                and entry.name not in ASSET_DIRECTORIES
                and not skipped(entry.name)
                and any((entry / kind).is_dir() for kind in ASSET_DIRECTORIES)
                and str(entry.relative_to(directory)) not in owned
            )
        )

    @cached_property
    def sources(self) -> dict[str, str]:
        """Where each subagent is defined, by name, relative to the catalogue."""
        return {name: where for name, (_, where) in self._defined.items()}

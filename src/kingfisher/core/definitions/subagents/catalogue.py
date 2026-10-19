"""Subagent definitions held in a directory on this host."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from kingfisher.core.definitions.documents import DefinitionText, documents_in
from kingfisher.core.definitions.importing import (
    Export,
    exported_from,
    modules_in,
    skipped,
)
from kingfisher.core.definitions.subagents import reading
from kingfisher.core.definitions.subagents.spec import EXPORT, SubagentError, SubagentSpec, declared
from kingfisher.core.definitions.tools.catalogue import CarriedTools, LocalToolRepository
from kingfisher.core.definitions.tools.spec import SEPARATOR, reference

#: The folders a bundle keeps its own tools and skills in, which the definition walk must
#: not descend into; any other folder under `subagents/` only groups definitions.
ASSET_DIRECTORIES: frozenset[str] = frozenset({"tools", "skills"})

DECLARES = Export(EXPORT, error=SubagentError, holding="subagents", example="my_subagent")


def _declared_in(directory: Path) -> list[tuple[SubagentSpec, str]]:
    """Every subagent a module under `directory` declares, with where it came from."""
    found: list[tuple[SubagentSpec, str]] = []
    for path in modules_in(directory):
        relative = path.relative_to(directory)
        # Here, not in `modules_in`, which the tool catalogue shares and where `tools/` is
        # only a folder. Safe after the walk: it imports nothing, so a bundle's module is
        # dropped before `exported_from` runs it.
        if ASSET_DIRECTORIES & set(relative.parts):
            continue
        where = str(relative) + ("/" if path.is_dir() else "")
        exported = exported_from(path, where=where, declares=DECLARES)
        found.extend((declared(entry, where), where) for entry in exported)
    return found


@dataclass(frozen=True)
class Holdings:
    """The tools and skills that belong to one subagent and to nothing else.

    Not named for the bundle: `SubagentSpec.bundled` is the list checked against these.
    """

    name: str
    where: str
    #: The folder these are in, or `None` for a definition that carried them, as one
    #: imported from an installed package does.
    root: Path | None = None
    #: What it carried, by a folder's two halves: tool objects, and skill directories,
    #: since a skill is files deepagents mounts.
    carried: Mapping[str, Any] = field(default_factory=dict)

    @property
    def tools(self) -> LocalToolRepository | CarriedTools | None:
        """This subagent's own tools, as the repository everything downstream asks."""
        if "tools" in self.carried:
            return CarriedTools(tuple(self.carried["tools"]), source=self.where)
        found = self._asset("tools")
        return None if found is None else LocalToolRepository(found)

    @property
    def skills(self) -> tuple[Path, ...]:
        """This subagent's skill directories: none, one for a folder, or those carried.

        Paths either way, since deepagents mounts a skills source by path.
        """
        carried = self.carried.get("skills")
        if carried is not None:
            return tuple(carried)
        found = self._asset("skills")
        return () if found is None else (found,)

    def _asset(self, kind: str) -> Path | None:
        if self.root is None:
            return None
        found = self.root / kind
        return found if found.is_dir() else None


def _bundle_of(spec: SubagentSpec, where: str, root: Path, key: str) -> Holdings | None:
    """What a definition owns: what it carried, or the folder named after it."""
    if spec.carried:
        # Labelled by its grant key, the one label guaranteed unique: two packages may
        # each ship a `probe`. A route may not hold the separator, so it becomes a dash.
        return Holdings(
            name=spec.name, where=key.replace(SEPARATOR, "-"), carried=spec.carried
        )
    parent = Path(where).parent
    # A definition directly under the catalogue has no folder: `parent.name` is empty.
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

        # Two folders may each hold a `probe.yaml`: both are kept, under the reference a
        # grant writes, and only an agent holding two is refused.
        read: list[tuple[SubagentSpec, str]] = []
        for path in documents_in(directory, error=SubagentError, skipping=ASSET_DIRECTORIES):
            # Relative to the catalogue, since a file name alone does not identify one.
            where = str(path.relative_to(directory))
            read.append((reading.read(DefinitionText.at(path)), where))
        # Counted with the documents: both formats share one namespace of names.
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
            # Folder-backed only: a carried bundle's `where` is a label, and counting it
            # would invent a folder and refuse the next definition in it.
            if bundle.root is not None:
                holders.setdefault(bundle.where, []).append(where)

        # Every definition under a bundle folder, not only its owner: the neighbour is the
        # problem, and it has no bundle of its own to be counted by.
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
        # And a folder inside this catalogue that a carried bundle names by absolute path,
        # which would otherwise be reported abandoned while the subagent reading it works.
        for bundle in self.bundles.values():
            if bundle.root is not None:
                continue
            for skills in bundle.skills:
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

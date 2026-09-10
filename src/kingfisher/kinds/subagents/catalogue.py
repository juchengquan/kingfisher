"""Subagent definitions held in a directory on this host."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from kingfisher.kinds.importing import (
    PACKAGE_MARKER,
    load,
    modules_in,
    skipped,
)
from kingfisher.kinds.subagents import reading
from kingfisher.kinds.subagents.reading import EXPORT, NEAR_MISS, SUFFIX, declared
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec
from kingfisher.kinds.tools.spec import reference

#: What a bundle's own assets are kept in, and therefore the two directory names that
#: are not organisation here. A folder under `subagents/` is normally free -- it groups
#: definitions and nothing else -- but these two hold a subagent's private tools and
#: skills, and the definition walk must not descend into them.
ASSET_DIRECTORIES: frozenset[str] = frozenset({"tools", "skills"})


def _definitions_in(directory: Path) -> list[Path]:
    """Every definition document below `directory`, at any depth, in a stable order."""
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            if entry.name in ASSET_DIRECTORIES:
                continue
            found.extend(_definitions_in(entry))
        elif entry.name.endswith(SUFFIX):
            found.append(entry)
        elif entry.suffix == NEAR_MISS:
            msg = (
                f"{entry.name}: kingfisher reads {SUFFIX!r} here, so this file is "
                f"not loaded -- rename it to {entry.stem}{SUFFIX}"
            )
            raise SubagentError(msg)
    return found


def _declared_in(directory: Path) -> list[tuple[SubagentSpec, str]]:
    """Every subagent a module under `directory` declares, with where it came from."""
    found: list[tuple[SubagentSpec, str]] = []
    for path in modules_in(directory):
        relative = path.relative_to(directory)
        # The Python half of what `_definitions_in` skips, and it has to be here
        # rather than in `modules_in`: that walk is shared with the tool
        # catalogue, where a folder called `tools` is ordinary organisation.
        # Filtered after the walk rather than during it because the walk imports
        # nothing -- `load` does, further down -- so a module under a bundle's
        # `tools/` is dropped before anything executes it.
        if ASSET_DIRECTORIES & set(relative.parts):
            continue
        where = str(relative) + ("/" if path.is_dir() else "")
        module = load(path, declares=EXPORT, error=SubagentError)
        exported = getattr(module, EXPORT, None)
        if exported is None:
            declared_in = f"{where}{PACKAGE_MARKER}" if path.is_dir() else where
            msg = f"{declared_in}: must define {EXPORT}, the subagents it contributes"
            raise SubagentError(msg)
        # A list or a tuple, and nothing looser. A compiled subagent is a
        # `dict`, and a dict is iterable, so `SUBAGENTS = {...}` would pass a
        # duck test and then loop over its own key names. `TOOLS` learned this
        # from pydantic models, which are iterable for a different reason.
        if not isinstance(exported, (list, tuple)):
            msg = (
                f"{where}: {EXPORT} must be a list or tuple of subagents, "
                f"got {type(exported).__name__} -- write {EXPORT} = [my_subagent]"
            )
            raise SubagentError(msg)
        found.extend((declared(entry, where), where) for entry in exported)
    return found


@dataclass(frozen=True)
class Bundle:
    """The tools and skills that belong to one subagent and to nothing else."""

    name: str
    root: Path
    where: str

    @property
    def tools(self) -> Path | None:
        """This bundle's tool directory, when it has one."""
        return self._asset("tools")

    @property
    def skills(self) -> Path | None:
        """This bundle's skill directory, when it has one."""
        return self._asset("skills")

    def _asset(self, kind: str) -> Path | None:
        found = self.root / kind
        return found if found.is_dir() else None


def _bundle_of(spec: SubagentSpec, where: str, root: Path) -> Bundle | None:
    """The bundle a definition owns, if its folder is named after it."""
    parent = Path(where).parent
    # A loose definition directly under the catalogue has no folder to be named
    # after, which `parent.name` reports as the empty string.
    if not parent.name or parent.name != spec.name:
        return None
    return Bundle(name=spec.name, root=root / parent, where=str(parent))


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
        for path in _definitions_in(directory):
            # Relative to the catalogue: `reviewer.yaml` stops identifying a
            # file once two folders may each hold one.
            where = str(path.relative_to(directory))
            read.append((reading.read(path.read_text(encoding="utf-8"), path), where))
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
    def bundles(self) -> dict[str, Bundle]:
        """Each subagent's own tools and skills, by the name a grant would use."""
        found: dict[str, Bundle] = {}
        holders: dict[str, list[str]] = {}
        for key, (spec, where) in self._defined.items():
            bundle = _bundle_of(spec, where, Path(self.root))
            if bundle is None:
                continue
            found[key] = bundle
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

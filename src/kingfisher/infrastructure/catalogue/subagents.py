"""Subagent definitions held in a directory on this host.

`domain.subagent.reading` owns the format -- what a definition means and what makes one
malformed -- and `documents` turns a document into one. Finding the files is a
third job, and it is this one: nothing in either of those globs a directory.

A class rather than two functions taking the same `Path`. Beyond holding the
directory, it fixes something the pair could not: `load_all` and `sources` each
walked the tree and parsed every file, so a caller wanting both -- which is what
`--list` is -- parsed the whole catalogue twice. One read now answers both.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from kingfisher.domain.subagent import SubagentError, SubagentSpec
from kingfisher.domain.subagent.reading import EXPORT, SUFFIX, declared
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.catalogue.importing import (
    PACKAGE_MARKER,
    load,
    modules_in,
    skipped,
)
from kingfisher.tools.spec import reference

#: The spelling people reach for, and the one that used to vanish. `.yml` is
#: valid YAML everywhere else, so a file named that way is a definition someone
#: wrote and kingfisher silently did not read.
#:
#: Named rather than "any extension we do not recognise", which was the first
#: draft. A folder here may now be a Python package, and a package is entitled
#: to hold whatever it needs beside its `__init__.py` -- a JSON fixture, a CSV,
#: a prompt in a text file. Refusing every unfamiliar suffix would break that
#: for the sake of one confusion, so the one confusion is named.
NEAR_MISS = ".yml"

#: What a bundle's own assets are kept in, and therefore the two directory
#: names that are not organisation here. A folder under `subagents/` is
#: normally free -- it groups definitions and nothing else -- but these two
#: hold a subagent's private tools and skills, and the definition walk must not
#: descend into them.
#:
#: Not a rule about bundles, deliberately: it applies wherever the names appear,
#: because the alternative needs to know whether a folder is a bundle *before*
#: reading the definition that decides it. What it costs is a grouping folder
#: literally called `tools` or `skills`, which would be a confusing thing to
#: own under `subagents/` in any case.
#:
#: A skill folder may hold whatever a skill needs -- a JSON fixture, a CSV, a
#: prompt, a `config.yaml` -- and that last one is why this exists rather than
#: being left to chance: without it, a skill's own settings file parses as a
#: subagent definition and the catalogue fails to load over a file that was
#: never one.
ASSET_DIRECTORIES: frozenset[str] = frozenset({"tools", "skills"})


def _definitions_in(directory: Path) -> list[Path]:
    """Every definition document below `directory`, at any depth, in a stable order.

    Folders are organisation, and that stays true now that one may also be a
    Python package: a package's documents are still read. A folder is a package
    for the *module* walk, which stops at it, and a folder for this one, which
    does not -- the two searches never look at each other's files, so one tree
    carries both without either needing to know.

    Hidden directories and `__pycache__` are skipped for the same reason the
    module loader skips them: a one-level scan could never reach whatever a
    person left lying under the catalogue, and a recursive one can.
    `ASSET_DIRECTORIES` is skipped for a sharper version of that reason: what is
    under there belongs to a *skill* or a tool, and a skill may keep a
    `config.yaml` that this would otherwise read as a subagent and refuse.

    A function and not a method: it recurses into subdirectories, so most of its
    calls are about somewhere that is not the repository's root.
    """
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
    """Every subagent a module under `directory` declares, with where it came from.

    The Python half. `modules_in` is the same collection the tool catalogue
    walks, with the same two shapes -- a loose file is a module, a folder
    holding `__init__.py` is one unit and is not descended into -- so a compiled
    subagent that grew helpers writes a folder exactly as a tool does.

    A module without `SUBAGENTS` is an error rather than a skipped file, for the
    reason the tool loader gives: quietly offering fewer than the workspace
    defines is the failure `CapabilityError` exists to prevent, one layer down.

    Which is exactly why a bundle's own `tools/` must be kept out of this walk:
    every module in it declares `TOOLS` and none declares `SUBAGENTS`, so a
    subagent that grew one private tool would fail the whole catalogue with a
    message about the wrong export.
    """
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
    """The tools and skills that belong to one subagent and to nothing else.

    A folder under `subagents/` is a bundle when it holds a definition whose
    `name` is the folder's own. That rule is the whole of it, and it is stated
    rather than inferred for a reason the skill registry already ran into:
    `misfiled` exists because a directory name and a declared name can disagree,
    and there the disagreement can only be *reported*, since deepagents owns the
    skill format and refusing would fail a working catalogue over a spelling.
    Kingfisher owns this format, so here the same relationship can decide
    something.

    What the folder buys is the one thing the shared catalogue cannot offer. An
    agent omitting `tools:` gets every tool there is -- `absent=ALL` -- so a tool
    in `tools/` is a tool the top-level agent holds. A tool in a bundle is not:
    it reaches the delegate that owns it and no one else, which is how a
    delegate comes to be trusted with something its caller is not.

    `where` rather than a second path: every message about a bundle names the
    place a person opens, and `root` is absolute.
    """

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
    """The bundle a definition owns, if its folder is named after it.

    Takes the relative `where` the repository already computed rather than
    walking again -- a bundle is a fact about where a definition was found, and
    that is known the moment it is read.

    A definition declared by a Python module has no bundle and cannot: `where`
    is then a module path, and the folder it names is a package whose
    `__init__.py` decides what it exports. A package that also held a `tools/`
    would be saying two different things with one directory.
    """
    parent = Path(where).parent
    # A loose definition directly under the catalogue has no folder to be named
    # after, which `parent.name` reports as the empty string.
    if not parent.name or parent.name != spec.name:
        return None
    return Bundle(name=spec.name, root=root / parent, where=str(parent))


@dataclass(frozen=True)
class LocalSubagentRepository:
    """The subagents defined in one directory.

    Given the directory itself rather than a workspace to derive one from: the
    catalogue can be deployed outside any workspace and shared by all of them,
    so there is no longer a single parent to infer it from. A session's uploaded
    subagents are this same class pointed at the session.
    """

    root: Path

    @cached_property
    def _defined(self) -> dict[str, tuple[SubagentSpec, str]]:
        """Every definition below `root`, parsed once, with where it came from.

        Both answers from one walk. The filename is not authoritative -- the
        `name` field is, since that is what a request names and what the `task`
        tool will use. Which is also why folders are free: a path cannot reach a
        name, so nesting a definition changes where it is kept and nothing else.
        The duplicate check is what stays load-bearing, and it spans folders
        rather than one listing.
        """
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
            read.append((read_subagent(path.read_text(encoding="utf-8"), path), where))
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
        """Every subagent defined here, keyed as a grant would name it.

        Flat where the name is its own, and `analysis/profiler.yaml::profiler`
        where two files claim it -- the same spelling a tool reference uses, and
        for the same reason: a bare name that means two things cannot pick one.
        """
        return {name: spec for name, (spec, _) in self._defined.items()}

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._defined)

    @cached_property
    def bundles(self) -> dict[str, Bundle]:
        """Each subagent's own tools and skills, by the name a grant would use.

        Keyed exactly as `specs` is, qualified reference and all, so a caller
        holding a name from one has a name for the other. Two catalogues may
        each ship a `surveyor` bundle for the same reason two may each ship a
        `surveyor`.

        Derived from the walk `_defined` already did rather than a second one.
        The class exists because `load_all` and `sources` each walked the tree
        and parsed every file; a bundle is a fact about *where* a definition was
        found, so it is already in hand by the time this is asked.

        A folder holding a definition named after it and *also* holding another
        definition is refused rather than resolved. `surveyor/surveyor.yaml`
        beside `surveyor/helper.yaml` gives no honest answer to "is `helper`
        inside the bundle or next to it", and the two answers differ in what
        `helper` may call -- so this is a question about capability, not tidiness,
        and guessing at it would decide something nobody wrote down.
        """
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
        """Folders holding `tools/` or `skills/` that no definition is named for.

        Reported, never refused, which is the split `skill_registry.misfiled`
        already draws: a grouping folder is allowed to have directories in it,
        so this is legal and the catalogue loads. It is also, nine times in ten,
        a bundle whose definition was renamed -- and the symptom otherwise is a
        delegate quietly holding nothing, which is the silent emptiness this
        package keeps refusing everywhere else.

        Its own walk, and a cheap one: directories only, nothing parsed. The
        single-read rule this class is built on is about not parsing every file
        twice, and this reads no files at all.
        """
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
        """Where each subagent is defined, by name, relative to the catalogue.

        For `--list`, and for the same reason the tool loader has one: a folder
        exists so a person can find a file, and a bare name does not help them.

        Not on `SubagentRepository`: a store that is not a directory has no
        relative path to report, and the one caller is an inventory listing.
        """
        return {name: where for name, (_, where) in self._defined.items()}

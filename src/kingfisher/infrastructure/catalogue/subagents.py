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
from kingfisher.domain.tool import reference
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.catalogue.importing import (
    PACKAGE_MARKER,
    load,
    modules_in,
    skipped,
)

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

    A function and not a method: it recurses into subdirectories, so most of its
    calls are about somewhere that is not the repository's root.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
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
    """
    found: list[tuple[SubagentSpec, str]] = []
    for path in modules_in(directory):
        where = str(path.relative_to(directory)) + ("/" if path.is_dir() else "")
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
    def sources(self) -> dict[str, str]:
        """Where each subagent is defined, by name, relative to the catalogue.

        For `--list`, and for the same reason the tool loader has one: a folder
        exists so a person can find a file, and a bare name does not help them.

        Not on `SubagentRepository`: a store that is not a directory has no
        relative path to report, and the one caller is an inventory listing.
        """
        return {name: where for name, (_, where) in self._defined.items()}

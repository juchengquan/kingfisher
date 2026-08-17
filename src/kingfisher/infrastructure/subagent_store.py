"""Subagent definitions held in a directory on this host.

`domain.subagent` owns the format -- what a definition means and what makes one
malformed -- and `definitions` turns a document into one. Finding the files is a
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

from kingfisher.domain.subagent import SUFFIX, SubagentSpec
from kingfisher.domain.tool import reference
from kingfisher.infrastructure.definitions import read_subagent


def _definitions_in(directory: Path) -> list[Path]:
    """Every definition below `directory`, at any depth, in a stable order.

    Folders are organisation and nothing else. There is no package shape to
    honour here as there is for tools -- a definition is a document we parse,
    not code we import -- so a walk is the whole feature.

    Hidden directories and `__pycache__` are skipped for the same reason the
    tool loader skips them: a one-level scan could never reach whatever a
    person left lying under the catalogue, and a recursive one can.

    A function and not a method: it recurses into subdirectories, so most of its
    calls are about somewhere that is not the repository's root.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        if entry.is_dir():
            found.extend(_definitions_in(entry))
        elif entry.name.endswith(SUFFIX):
            found.append(entry)
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

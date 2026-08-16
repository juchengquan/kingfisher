"""Reading subagent definitions off disk.

`domain.subagent` owns the format -- what a definition means and what makes one
malformed -- and `definitions` turns a document into one. Finding the files is a
third job, and it is this one: nothing in either of those globs a directory.
"""

from __future__ import annotations

from pathlib import Path

from kingfisher.domain.subagent import SUFFIX, SubagentError, SubagentSpec
from kingfisher.infrastructure.definitions import read_subagent


def _definitions_in(directory: Path) -> list[Path]:
    """Every definition below `directory`, at any depth, in a stable order.

    Folders are organisation and nothing else. There is no package shape to
    honour here as there is for tools -- a definition is a document we parse,
    not code we import -- so a walk is the whole feature.

    Hidden directories and `__pycache__` are skipped for the same reason the
    tool loader skips them: a one-level scan could never reach whatever a
    person left lying under the catalogue, and a recursive one can.
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


def load_all(directory: Path) -> dict[str, SubagentSpec]:
    """Every subagent defined in `directory`, keyed by name.

    Given the directory itself rather than a workspace to derive one from: the
    catalogue can be deployed outside any workspace and shared by all of them,
    so there is no longer a single parent to infer it from.

    The filename is not authoritative — the `name` field is, since that
    is what a request names and what the `task` tool will use. Which is also
    why folders are free: a path cannot reach a name, so nesting a definition
    changes where it is kept and nothing else. The duplicate check below is
    what stays load-bearing, and it now spans folders rather than one listing.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {}

    specs: dict[str, SubagentSpec] = {}
    seen: dict[str, str] = {}
    for path in _definitions_in(directory):
        # Relative to the catalogue: `reviewer.yaml` stops identifying a file
        # once two folders may each hold one.
        where = str(path.relative_to(directory))
        spec = read_subagent(path.read_text(encoding="utf-8"), path)
        if spec.name in specs:
            msg = (
                f"{where}: duplicate subagent name {spec.name!r}, "
                f"already defined by {seen[spec.name]}"
            )
            raise SubagentError(msg)
        seen[spec.name] = where
        specs[spec.name] = spec
    return specs


def sources(directory: Path) -> dict[str, str]:
    """Where each subagent is defined, by name, relative to the catalogue.

    For `--list`, and for the same reason the tool loader has one: a folder
    exists so a person can find a file, and a bare name does not help them.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {}
    return {
        read_subagent(p.read_text(encoding="utf-8"), p).name: str(p.relative_to(directory))
        for p in _definitions_in(directory)
    }

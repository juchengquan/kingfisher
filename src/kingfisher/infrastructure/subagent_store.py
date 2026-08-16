"""Reading subagent definitions off disk.

`domain.subagent` owns the format -- what a definition means and what makes one
malformed -- and `definitions` turns a document into one. Finding the files is a
third job, and it is this one: nothing in either of those globs a directory.
"""

from __future__ import annotations

from pathlib import Path

from kingfisher.domain.subagent import (
    LEGACY_SUFFIX,
    SUFFIX,
    SubagentError,
    SubagentSpec,
)
from kingfisher.infrastructure.definitions import read_subagent


def load_all(directory: Path) -> dict[str, SubagentSpec]:
    """Every subagent defined in `directory`, keyed by name.

    Given the directory itself rather than a workspace to derive one from: the
    catalogue can be deployed outside any workspace and shared by all of them,
    so there is no longer a single parent to infer it from.

    The filename is not authoritative — the frontmatter `name` is, since that
    is what a request names and what the `task` tool will use.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return {}

    specs: dict[str, SubagentSpec] = {}
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        spec = read_subagent(path.read_text(encoding="utf-8"), path)
        if spec.name in specs:
            msg = f"{path.name}: duplicate subagent name {spec.name!r}"
            raise SubagentError(msg)
        specs[spec.name] = spec
    return specs


def stranded(directory: Path) -> tuple[str, ...]:
    """Definitions left in the format this directory no longer reads.

    Subagents were markdown with a YAML header and are now YAML, because the
    header had grown into the whole document -- every field but the prompt.
    A workspace seeded before that change keeps its `.md` files, and they simply
    stop being found: the directory looks full and the catalogue reports none.

    Breaking a layout silently is the thing worth reporting, which is the same
    reason `skill_store.misplaced` exists. Named rather than converted, because
    a definition is someone's text and rewriting it unasked is not this
    function's business.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return ()
    return tuple(sorted(p.name for p in directory.glob(f"*{LEGACY_SUFFIX}")))

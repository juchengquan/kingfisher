"""Reading subagent definitions off disk.

`domain.subagent` owns the format -- what a definition means and what makes one
malformed. Finding the files is a different job, and it is this one. The domain
parses text it is handed; nothing there globs a directory.
"""

from __future__ import annotations

from pathlib import Path

from kingfisher.domain.subagent import DIRECTORY, SUFFIX, SubagentError, SubagentSpec, parse


def load_all(workspace: Path) -> dict[str, SubagentSpec]:
    """Every subagent the workspace defines, keyed by name.

    The filename is not authoritative — the frontmatter `name` is, since that
    is what a request names and what the `task` tool will use.
    """
    directory = Path(workspace) / DIRECTORY
    if not directory.is_dir():
        return {}

    specs: dict[str, SubagentSpec] = {}
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        spec = parse(path.read_text(encoding="utf-8"), path)
        if spec.name in specs:
            msg = f"{path.name}: duplicate subagent name {spec.name!r}"
            raise SubagentError(msg)
        specs[spec.name] = spec
    return specs

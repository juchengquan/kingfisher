"""Putting the smoke's fixtures into a workspace.

The sample skill is copied from `examples/skills/tabular-qa/`, not held here as
a string. It used to be both -- a constant in `smoke.py` *and* files under
`examples/` -- which meant two homes for sample content and no way to tell
which one a run had actually used.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from evals.dataset import seed_sample_data
from kingfisher.domain.skill import FILENAME

SKILL_NAME = "tabular-qa"
EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def seed_sample_skill(workspace: Path) -> bool:
    """Copy the sample skill into the workspace. True if anything changed."""
    source = EXAMPLES / "skills" / SKILL_NAME
    target = Path(workspace) / "skills" / SKILL_NAME
    if not source.is_dir():  # pragma: no cover -- the repo ships it
        msg = f"missing example skill: {source}"
        raise FileNotFoundError(msg)

    installed = target / FILENAME
    existing = installed.read_text(encoding="utf-8") if installed.is_file() else None
    if existing == (source / FILENAME).read_text(encoding="utf-8"):
        return False
    shutil.copytree(source, target, dirs_exist_ok=True)
    return True


__all__ = ["SKILL_NAME", "seed_sample_data", "seed_sample_skill"]

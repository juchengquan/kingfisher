"""Putting the smoke's fixtures into a workspace.

The sample skill is copied from `assets_examples/skills/tabular-qa`, not held here
as a string. It used to be both -- a constant in `smoke.py` *and* files under
the presets directory -- which meant two homes for sample content and no way to
tell which one a run had actually used.

From `assets_examples/` in this repository, found by marker rather than counted.
It came through `importlib.resources` while the definitions rode inside the
wheel; nothing ships them now, so that route resolves to a package that is not
there.

Deliberately not through `KINGFISHER_ASSETS`. The smoke asserts against a
*known* skill, and the variable exists precisely so a deployment can point
somewhere else -- reading it here would make the smoke pass or fail on whatever
content a developer happened to configure.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from evals.dataset import seed_sample_data
from kingfisher.domain.skill import FILENAME

SKILL_NAME = "tabular-qa"

#: This repository's worked definitions. `evals/` is not in the wheel either, so
#: both sides of this path live or die together in a checkout.
EXAMPLES = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file() and (parent / "src" / "kingfisher").is_dir()
) / "assets_examples"


def seed_sample_skill(workspace: Path) -> bool:
    """Copy the sample skill into the workspace. True if anything changed."""
    target = Path(workspace) / "skills" / SKILL_NAME
    source = EXAMPLES / "skills" / SKILL_NAME
    if not source.is_dir():  # pragma: no cover -- this repository ships it
        msg = f"missing sample skill: {source}"
        raise FileNotFoundError(msg)

    installed = target / FILENAME
    existing = installed.read_text(encoding="utf-8") if installed.is_file() else None
    if existing == (source / FILENAME).read_text(encoding="utf-8"):
        return False
    shutil.copytree(source, target, dirs_exist_ok=True)
    return True


__all__ = ["SKILL_NAME", "seed_sample_data", "seed_sample_skill"]

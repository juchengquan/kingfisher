"""Putting the smoke's fixtures into a workspace.

The sample skill is copied from the shipped preset `tabular-qa`, not held here
as a string. It used to be both -- a constant in `smoke.py` *and* files under
the presets directory -- which meant two homes for sample content and no way to
tell which one a run had actually used.

Reached through `presets.opened()` rather than a path relative to this file:
the definitions ship inside the package now, so an installed kingfisher finds
them and a checkout finds the same ones.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from evals.dataset import seed_sample_data
from kingfisher.domain.skill import FILENAME
from kingfisher.infrastructure import presets

SKILL_NAME = "tabular-qa"


def seed_sample_skill(workspace: Path) -> bool:
    """Copy the sample skill into the workspace. True if anything changed."""
    target = Path(workspace) / "skills" / SKILL_NAME
    with presets.opened() as root:
        source = root / "skills" / SKILL_NAME
        if not source.is_dir():  # pragma: no cover -- the package ships it
            msg = f"missing preset skill: {source}"
            raise FileNotFoundError(msg)

        installed = target / FILENAME
        existing = installed.read_text(encoding="utf-8") if installed.is_file() else None
        if existing == (source / FILENAME).read_text(encoding="utf-8"):
            return False
        shutil.copytree(source, target, dirs_exist_ok=True)
    return True


__all__ = ["SKILL_NAME", "seed_sample_data", "seed_sample_skill"]

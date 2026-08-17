"""Putting the smoke's fixtures into a workspace.

The sample skill is copied from the shipped preset `tabular-qa`, not held here
as a string. It used to be both -- a constant in `smoke.py` *and* files under
the presets directory -- which meant two homes for sample content and no way to
tell which one a run had actually used.

Reached through `importlib.resources` rather than a path relative to this file,
so it works from a wheel as well as a checkout.

From `kingfisher_assets`, which is where the definitions live: the framework
ships none. The smoke is development tooling in this repository and the pack is
a workspace member, so depending on it here costs the framework nothing -- and
a smoke run that needed content kingfisher does not have would otherwise have
no honest source for it.
"""

from __future__ import annotations

import shutil
from importlib import resources
from pathlib import Path

from evals.dataset import seed_sample_data
from kingfisher.domain.skill import FILENAME

SKILL_NAME = "tabular-qa"


def seed_sample_skill(workspace: Path) -> bool:
    """Copy the sample skill into the workspace. True if anything changed."""
    target = Path(workspace) / "skills" / SKILL_NAME
    with resources.as_file(resources.files("kingfisher_assets")) as root:
        source = root / "skills" / SKILL_NAME
        if not source.is_dir():  # pragma: no cover -- the pack ships it
            msg = f"missing sample skill: {source}"
            raise FileNotFoundError(msg)

        installed = target / FILENAME
        existing = installed.read_text(encoding="utf-8") if installed.is_file() else None
        if existing == (source / FILENAME).read_text(encoding="utf-8"):
            return False
        shutil.copytree(source, target, dirs_exist_ok=True)
    return True


__all__ = ["SKILL_NAME", "seed_sample_data", "seed_sample_skill"]

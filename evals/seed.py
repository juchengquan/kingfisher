"""Putting the smoke's fixtures where a run reads them: the skill into the definitions
folder, the data into a session.

The sample skill is copied from `assets/skills/tabular-qa`, never held here as well.
Not through `KINGFISHER_ASSETS`, which exists to point somewhere else: the smoke asserts
against a known skill.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from evals.dataset import seed_sample_data
from kingfisher.core.definitions.skills.spec import FILENAME

SKILL_NAME = "tabular-qa"

#: The definitions this repository ships. `evals/` is not in the wheel either, so
#: both sides of this path live or die together in a checkout.
ASSETS = next(
    parent
    for parent in Path(__file__).resolve().parents
    if (parent / "pyproject.toml").is_file() and (parent / "src" / "kingfisher").is_dir()
) / "assets"


def seed_sample_skill(definitions: Path) -> bool:
    """Copy the sample skill into the definitions folder. True if anything changed."""
    target = Path(definitions) / "skills" / SKILL_NAME
    source = ASSETS / "skills" / SKILL_NAME
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

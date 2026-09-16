"""Skill definitions: `<name>/SKILL.md`."""

from __future__ import annotations

FILENAME = "SKILL.md"

#: Where skills live and what an upload's directory is called are *not* here. They are
#: facts about the workspace layout, so `kingfisher.layout` declares them -- `SKILLS`
#: and `UPLOADED_SKILL_DIR` -- and readers ask it directly rather than through this
#: module.


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""

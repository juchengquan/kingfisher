"""Skill definitions: `<name>/SKILL.md`."""

from __future__ import annotations

FILENAME = "SKILL.md"

#: Where skills live is *not* here. That is a fact about the workspace layout, so
#: `kingfisher.layout` declares it -- `SKILLS` -- and readers ask it directly
#: rather than through this module.


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""

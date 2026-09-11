"""Reading a skill document for the one thing kingfisher needs from it."""

from __future__ import annotations

from kingfisher.domain import fields
from kingfisher.kinds import documents
from kingfisher.kinds.skills.spec import FILENAME, SkillError, split


def name_from(text: str, source: str = FILENAME) -> str:
    """A skill's declared name, which is also its directory name."""
    parts = split(text)
    if parts is None:
        msg = f"{source}: expected YAML frontmatter delimited by ---"
        raise SkillError(msg)

    document = documents.decode(parts[0])
    if isinstance(document, str):
        msg = f"{source}: cannot read frontmatter ({document})"
        raise SkillError(msg)

    name = fields.text(document.get("name"))
    if not name:
        msg = f"{source}: frontmatter is missing required field 'name'"
        raise SkillError(msg)
    if "/" in name or name in {".", ".."}:
        # It becomes a directory name, so a path separator here would write
        # outside the directory the caller believes it is filling.
        msg = f"{source}: {name!r} is not usable as a directory name"
        raise SkillError(msg)
    return name

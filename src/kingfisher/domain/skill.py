"""Skill definitions: `<name>/SKILL.md`.

kingfisher does not own this format — deepagents reads it and decides what a
skill means. What kingfisher needs from it is one thing: the name, because that
is what a request activates and what the directory must be called.

That last part is not our rule. deepagents validates `name` against the parent
directory and rejects the skill if they differ, so a definition arriving from a
catalogue cannot be dropped into a directory of our choosing — it has to be
unpacked under the name it declares.
"""

from __future__ import annotations

from kingfisher.domain import frontmatter

FILENAME = "SKILL.md"
DIRECTORY = "skills"

#: Uploaded skills live here, inside the session, beside the shared catalogue
#: in the agent's view of `/skills`. Reserved: a catalogue skill by this name
#: would shadow the route and hide every upload.
UPLOADED = "uploaded"


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""


def name_of(text: str, source: str = FILENAME) -> str:
    """The skill's declared name, which is also its directory name."""
    split = frontmatter.split(text)
    if split is None:
        msg = f"{source}: expected YAML frontmatter delimited by ---"
        raise SkillError(msg)

    fields = frontmatter.fields(split[0])
    if isinstance(fields, str):
        msg = f"{source}: cannot parse frontmatter line {fields!r}"
        raise SkillError(msg)

    name = frontmatter.scalar(fields.get("name", ""))
    if not name:
        msg = f"{source}: frontmatter is missing required field 'name'"
        raise SkillError(msg)
    if "/" in name or name in {".", ".."}:
        # It becomes a directory name, so a path separator here would write
        # outside the directory the caller believes it is filling.
        msg = f"{source}: {name!r} is not usable as a directory name"
        raise SkillError(msg)
    return name

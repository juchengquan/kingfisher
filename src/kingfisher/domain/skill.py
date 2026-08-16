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

from collections.abc import Mapping

from kingfisher.domain import frontmatter

FILENAME = "SKILL.md"
DIRECTORY = "skills"

#: Uploaded skills live here, inside the session, beside the shared catalogue
#: in the agent's view of `/skills`. Reserved: a catalogue skill by this name
#: would shadow the route and hide every upload.
UPLOADED = "uploaded"


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""


def name_of(fields: Mapping[str, object], source: str = FILENAME) -> str:
    """The skill's declared name, which is also its directory name.

    Takes decoded fields rather than the document. Reading YAML needs a
    library, so `adapters.definitions` does that half and hands the result
    here — see `domain.frontmatter` for where the seam falls and why.
    """
    name = frontmatter.text(fields.get("name"))
    if not name:
        msg = f"{source}: frontmatter is missing required field 'name'"
        raise SkillError(msg)
    if "/" in name or name in {".", ".."}:
        # It becomes a directory name, so a path separator here would write
        # outside the directory the caller believes it is filling.
        msg = f"{source}: {name!r} is not usable as a directory name"
        raise SkillError(msg)
    return name

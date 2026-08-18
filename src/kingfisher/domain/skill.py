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

import re
from collections.abc import Mapping

from kingfisher.domain import fields

FILENAME = "SKILL.md"
DIRECTORY = "skills"

#: Uploaded skills live here, inside the session, beside the shared catalogue
#: in the agent's view of `/skills`. Reserved: a catalogue skill by this name
#: would shadow the route and hide every upload.
UPLOADED = "uploaded"


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""


def name_of(document: Mapping[str, object], source: str = FILENAME) -> str:
    """The skill's declared name, which is also its directory name.

    Takes decoded fields rather than the document. Reading YAML needs a
    library, so `infrastructure.catalogue.documents` does that half and hands
    the result here — see `domain.fields` for where the seam falls and why.
    """
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


#: A skill is markdown with a `---` header. Kingfisher does not own that shape
#: -- deepagents reads it -- but something has to find where the header ends,
#: and it belongs with the one format that still has one.
_HEADER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def split(text: str) -> tuple[str, str] | None:
    """The raw header and the body, or `None` if there is no header.

    `None` rather than an exception: what a missing header *means* is the
    caller's to say, in its own words and its own exception type.
    """
    match = _HEADER.match(text)
    return (match.group(1), match.group(2).strip()) if match else None

"""Skill definitions: `<name>/SKILL.md`.

kingfisher does not own this format — deepagents reads it and decides what a
skill means. What kingfisher needs from it is one thing: the name, because that
is what a request activates and what the directory must be called.

That last part is not our rule. deepagents validates `name` against the parent
directory and rejects the skill if they differ, so a definition arriving from a
catalogue cannot be dropped into a directory of our choosing — it has to be
unpacked under the name it declares.

Vocabulary only: where the header ends, what the file is called, which exception
a mistake raises. Reading a document is `reading`, one file over, because that
needs `yaml` and a domain module is allowed to name this one.
"""

from __future__ import annotations

import re

FILENAME = "SKILL.md"

#: Where skills live and what an upload's directory is called are *not* here.
#: They are facts about the workspace layout, so `domain.layout` declares them
#: -- `SKILLS` and `UPLOADED_SKILL_DIR` -- and readers ask it directly rather
#: than through this module.
#:
#: They were here while both files were in `domain/` and nothing had to choose.
#: `skills` becoming a module of its own made the domain import it to learn
#: where a directory was, which is the wrong way round, and that is what named
#: the owner. This module is about the document format and nothing else.


class SkillError(ValueError):
    """Raised when a skill definition cannot be read."""


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

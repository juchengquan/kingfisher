"""The `---`-delimited header both definition formats carry.

Skills and subagents are deliberately the same shape — YAML frontmatter and a
markdown body — so a contributor who has written one does not have to learn a
second mechanism. That sameness is only real if one parser serves both.

What lives here is what the fields *mean*: where the header ends and the body
begins, how a name-list may be written, how a value becomes the string the
format meant. What does not live here is the YAML decode, which needs a
third-party library — `infrastructure.definitions` owns that, because a domain module
imports the standard library and `kingfisher.domain`, nothing else.

The seam is the envelope. Whether a document carries a header at all, and
whether that header decodes, are questions about the document as transport, and
the adapter answers them. Whether `name` is present and usable is a rule of the
format, and it is answered in here.

Errors belong to the caller. `split` returns `None` for "no frontmatter here"
and lets each format say what a missing header means in its own words, with its
own exception type — `SkillError` and `SubagentError` are not interchangeable to
someone reading a traceback.
"""

from __future__ import annotations

import re

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def split(text: str) -> tuple[str, str] | None:
    """The raw header and the body, or `None` if there is no header."""
    match = _FRONTMATTER.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def text(value: object) -> str:
    """One field as a string, however YAML typed it.

    `model: 4` is a number to YAML and a model name to us; a folded description
    is already joined. Both become the string the format meant.
    """
    return "" if value is None else str(value).strip()


def names(value: object) -> tuple[str, ...] | None:
    """A field naming several things, written either way YAML allows.

    `[read_file, grep]` and a block list both mean the same thing, and a single
    unbracketed name is accepted because someone will write it.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(text(item) for item in value if text(item))
    return (text(value),)

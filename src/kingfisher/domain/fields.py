"""Reading one decoded field as the value the format meant.

Both definition formats reach here, and neither is the reason this module
exists any more. It was `frontmatter`, named for the `---`-delimited header the
two shared — until subagents became a whole YAML document and stopped having a
header at all. What survived the change is the part that was never about
envelopes: turning whatever YAML produced into the string or the tuple of names
the format asked for.

Splitting a markdown header off a body lives with the format that still has one
— `domain.skill.split` — because it is deepagents' rule about deepagents'
document, not a thing both formats do.

What does *not* live here is the YAML decode, which needs a third-party
library. `infrastructure.definitions` owns that, because a domain module
imports the standard library and `kingfisher.domain`, nothing else.
"""

from __future__ import annotations


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

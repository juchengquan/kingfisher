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

from difflib import get_close_matches
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping

#: How alike two names must look before one is called a typo of the other. Only
#: ever used to *word* a refusal, never to decide one: a guess that changed
#: behaviour would be the silent-drop bug wearing a spellchecker.
SIMILARITY = 0.7


def unrecognised(
    document: Iterable[str],
    *,
    known: Collection[str],
    declined: Mapping[str, str] | None = None,
    noun: str = "field",
) -> str | None:
    """The complaint about every key a format does not define, or `None`.

    Two formats refuse unknown keys and both give the same reason for it: a key
    we ignore is a key the author believes took effect. Subagent definitions had
    the careful version of the message and `models.yaml` had a plainer one, so
    the file a deployment writes *first* -- and the only one that decides where
    prompts go -- was the one that would not tell you `defualt:` was a typo.

    Returns the complaint rather than raising it. The two callers raise
    different types on purpose, `SubagentError` against `ConfigError`, and each
    prefixes the source in its own way; what they share is which keys are
    wrong, what they might have meant, and what the format does define.

    All of them at once, not the first. Two typos used to take two runs to find,
    and the second only after fixing the first.

    `declined` names keys refused for a *specific* reason -- a field another
    library defines that this format deliberately does not -- where the generic
    message would read as an omission worth working around.
    """
    problems = [
        _explain(key, known=known, declined=declined or {}, noun=noun)
        for key in document
        if key not in known
    ]
    if not problems:
        return None
    return f"{'; '.join(problems)} (this format defines: {', '.join(sorted(known))})"


def _explain(
    key: str, *, known: Collection[str], declined: Mapping[str, str], noun: str
) -> str:
    """Why this one key is not accepted, in the terms that fit it."""
    if (reason := declined.get(key)) is not None:
        return f"{key!r} is not a {noun} of this format -- {reason}"
    near = get_close_matches(key, known, n=1, cutoff=SIMILARITY)
    # Parenthesised, not `; `-joined: that separates one key's explanation from
    # the next, and a hint using it too would blur where each ends.
    hint = f" (did you mean {near[0]!r}?)" if near else ""
    return f"unknown {noun} {key!r}{hint}"


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

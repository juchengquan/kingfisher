"""The `---`-delimited header both definition formats carry.

Skills and subagents are deliberately the same shape — YAML frontmatter and a
markdown body — so a contributor who has written one does not have to learn a
second mechanism. That sameness is only real if one parser serves both.

It is YAML, parsed as YAML. This used to hand-roll a `key: value` reader, on
the reasoning that a YAML dependency would accept anchors, multi-line blocks and
type coercion into a format whose point is that a person can read it at a
glance. deepagents accepts exactly those when it reads a skill, which made
kingfisher *stricter than the format it mirrors*: a folded description or a
block list — the Agent Skills spec's own form for `allowed-tools` — parsed there
and raised here. Catalogue skills are never read by kingfisher, but uploaded
ones are, so a skill that loaded fine could not be uploaded.

`safe_load`, so a document cannot construct arbitrary objects. Definitions
arrive from a catalogue service under `DefinitionStore`, which makes them input
rather than something we wrote.

Errors belong to the caller. This returns `None` for "no frontmatter here" and
lets each format say what a missing header means in its own words, with its own
exception type — `SkillError` and `SubagentError` are not interchangeable to
someone reading a traceback.
"""

from __future__ import annotations

import re

import yaml

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def split(text: str) -> tuple[str, str] | None:
    """The raw header and the body, or `None` if there is no header."""
    match = _FRONTMATTER.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def fields(header: str) -> dict[str, object] | str:
    """Parse a header into its fields, or return why it could not be read.

    A string return is the error case. The caller raises — it knows which
    format was being read and which exception its readers expect.

    Values come back with YAML's types, so a list is a list and a number is a
    number. Callers coerce what they need, because what a field *should* be is
    the format's rule rather than the parser's.
    """
    try:
        parsed = yaml.safe_load(header)
    except yaml.YAMLError as exc:
        # One line: this ends up inside a `SkillError` or `SubagentError`
        # message, and YAML's own report spans several with a caret diagram.
        return " ".join(str(exc).split())
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        return f"expected a mapping of fields, got {type(parsed).__name__}"
    return {str(key): value for key, value in parsed.items()}


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

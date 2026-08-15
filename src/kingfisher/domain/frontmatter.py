"""The `---`-delimited header both definition formats carry.

Skills and subagents are deliberately the same shape — YAML frontmatter and a
markdown body — so a contributor who has written one does not have to learn a
second mechanism. That sameness is only real if one parser serves both; two
would drift the first time either grew a field.

Deliberately not a YAML parser. These headers are a handful of scalars and one
inline list, and pulling in a YAML dependency would accept anchors, multi-line
blocks and type coercion into a format whose whole point is that a person can
read it at a glance.

Errors belong to the caller. This returns `None` for "no frontmatter here" and
lets each format say what a missing header means in its own words, with its own
exception type — `SkillError` and `SubagentError` are not interchangeable to
someone reading a traceback.
"""

from __future__ import annotations

import re

#: The shortest quoted scalar is a pair of quotes with nothing between them.
QUOTED_MINIMUM = 2

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


def scalar(value: str) -> str:
    """One frontmatter value, unquoted if it was quoted."""
    value = value.strip()
    if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= QUOTED_MINIMUM:
        return value[1:-1]
    return value


def split(text: str) -> tuple[str, str] | None:
    """The raw header and the body, or `None` if there is no header."""
    match = _FRONTMATTER.match(text)
    return (match.group(1), match.group(2).strip()) if match else None


def fields(header: str) -> dict[str, str] | str:
    """Parse a header into `key: value` pairs, or return the offending line.

    A string return is the error case. The caller raises — it knows which
    format was being read and which exception the reader expects.
    """
    parsed: dict[str, str] = {}
    for raw in header.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            return line
        parsed[key.strip()] = value.strip()
    return parsed

"""Reading a definition document into the value the domain works with.

`domain.frontmatter` owns what the fields mean; this owns the one step that
needs a library. `yaml.safe_load` sat in the domain until the boundary was made
deny-by-default — a domain module imports the standard library and
`kingfisher.domain`, nothing else — and this is where it landed.

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

Named `definitions` rather than `frontmatter`: one name across two layers makes
every import a small act of guessing, which is why `scoping` is not called
`capabilities` either.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from kingfisher.domain import frontmatter, skill, subagent

if TYPE_CHECKING:
    from pathlib import Path


def decode(header: str) -> dict[str, object] | str:
    """A header's fields, or one line saying why it could not be read.

    A string return is the error case. The caller raises — it knows which
    format was being read and which exception its readers expect.

    Values come back with YAML's types, so a list is a list and a number is a
    number. The domain coerces what it needs, because what a field *should* be
    is the format's rule rather than the parser's.
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


def _opened(text: str, label: str, error: type[ValueError]) -> tuple[dict[str, object], str]:
    """The envelope: a header that decodes, and a body.

    Both formats fail the same two ways here and say so in their own exception
    type, because `SkillError` and `SubagentError` are not interchangeable to
    someone reading a traceback.
    """
    parts = frontmatter.split(text)
    if parts is None:
        msg = f"{label}: expected YAML frontmatter delimited by ---"
        raise error(msg)

    header, body = parts
    fields = decode(header)
    if isinstance(fields, str):
        msg = f"{label}: cannot read frontmatter ({fields})"
        raise error(msg)
    return fields, body


def read_subagent(text: str, source: Path) -> subagent.SubagentSpec:
    """One subagent definition. Raises `SubagentError` on anything malformed."""
    fields, body = _opened(text, source.name, subagent.SubagentError)
    return subagent.parse(fields, body, source)


def skill_name(text: str, source: str = skill.FILENAME) -> str:
    """A skill's declared name, which is also its directory name."""
    fields, _ = _opened(text, source, skill.SkillError)
    return skill.name_of(fields, source=source)

"""The YAML an agent or subagent definition is written in, read the same way by both.

Shared rather than duplicated, which is the one exception to *kinds do not share*: a
scalar's style is a fact about a document rather than about what any kind means, and the
agent and subagent formats reflow a prompt identically.

Named rather than counted, and the count is why. This said "all three kinds" and
"a header", both true while a skill's frontmatter came through here, and neither
true after the reader that did it went with the upload path it served. Nothing was
red: the paragraph above has named the two ever since, contradicting a sentence one
line up in the same docstring. A name a reader can grep beats a number nobody
re-counts.

Here rather than under `infrastructure/` because a kind may not name a layer. That is
the whole of the reason -- by subject this is as much the workspace's business as the
kinds', which is why the two scans it used to sit beside went the other way, to the
seeding that is their only reader.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from pathlib import Path

#: The scalar style that keeps a prompt's line breaks. `|`, `|2`, `|-` and
#: `|+` are all this one style once parsed -- the suffix never reaches the node.
LITERAL = "|"


def decode(text: str) -> dict[str, object] | str:
    """A definition's fields, or one line saying why it could not be read."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # One line: this ends up inside an `AgentError` or `SubagentError`
        # message, and YAML's own report spans several with a caret diagram.
        return " ".join(str(exc).split())
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        return f"expected a mapping of fields, got {type(parsed).__name__}"
    return {str(key): value for key, value in parsed.items()}


def require_literal_prompt(text: str, source: Path, error: type[ValueError]) -> None:
    """Refuse a `system_prompt` written in a style that reflows it."""
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):  # pragma: no cover -- decode checked
        return
    for key, value in node.value:
        if getattr(key, "value", None) != "system_prompt":
            continue
        if isinstance(value, yaml.ScalarNode) and value.style != LITERAL:
            written = f"{value.style!r}" if value.style else "a plain scalar"
            msg = (
                f"{source.name}: system_prompt is written as {written}, which reflows it. "
                f"Use a literal block -- `system_prompt: {LITERAL}` -- so the prompt "
                "reaches the delegate with the line breaks you wrote"
            )
            raise error(msg)
        return

"""The YAML an agent or subagent definition is written in, read the same way by both.

Shared, the one exception to *the kinds do not share an implementation*: a scalar's style
is a fact about a document rather than about what either kind means.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from .importing import skipped

if TYPE_CHECKING:
    from pathlib import Path

#: What a definition file is called, and the near miss refused rather than silently
#: skipped: a `.yml`, or either suffix in another case, is a definition somebody wrote.
SUFFIX = ".yaml"
NEAR_MISS = ".yml"

#: The scalar style that keeps a prompt's line breaks. `|`, `|2`, `|-` and
#: `|+` are all this one style once parsed -- the suffix never reaches the node.
LITERAL = "|"


def documents_in(
    directory: Path, *, error: type[ValueError], skipping: frozenset[str] = frozenset()
) -> list[Path]:
    """Every definition document below `directory`, at any depth, in a stable order.

    A folder is walked into unless named in `skipping`, which is how a subagent bundle
    keeps its own tools and skills beside its definition.
    """
    found: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if skipped(entry.name):
            continue
        if entry.is_dir():
            if entry.name in skipping:
                continue
            found.extend(documents_in(entry, error=error, skipping=skipping))
        elif entry.name.endswith(SUFFIX):
            found.append(entry)
        elif entry.suffix.lower() in (SUFFIX, NEAR_MISS):
            msg = (
                f"{entry.name}: kingfisher reads {SUFFIX!r} here, so this file is "
                f"not loaded -- rename it to {entry.stem}{SUFFIX}"
            )
            raise error(msg)
    return found


def decode(text: str) -> dict[str, object] | str:
    """A definition's fields, or one line saying why it could not be read."""
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        # One line: it lands inside a refusal, and YAML's own report spans several.
        return " ".join(str(exc).split())
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        return f"expected a mapping of fields, got {type(parsed).__name__}"
    return {str(key): value for key, value in parsed.items()}


@dataclass(frozen=True)
class DefinitionText:
    """A definition as written, and the file a refusal names.

    The text travels with it so what is pinned into a session is what was parsed:
    reading the file again would let an edit land in between.
    """

    text: str
    source: Path

    @classmethod
    def at(cls, path: Path) -> DefinitionText:
        return cls(path.read_text(encoding="utf-8"), path)


def fields_of(definition: DefinitionText, error: type[ValueError]) -> dict[str, object]:
    """A definition's fields, or the refusal naming the file and what stopped it."""
    document = decode(definition.text)
    if isinstance(document, str):
        msg = f"{definition.source.name}: cannot read definition ({document})"
        raise error(msg)
    require_literal_prompt(definition.text, definition.source, error)
    return document


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
                "reaches the subagent with the line breaks you wrote"
            )
            raise error(msg)
        return

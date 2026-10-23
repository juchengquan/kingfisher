"""The YAML an agent or subagent definition is written in, read the same way by both.

Shared, the one exception to *the kinds do not share an implementation*: a scalar's style
is a fact about a document rather than about what either kind means.
"""

from __future__ import annotations

from collections.abc import Hashable, Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml
from yaml.constructor import ConstructorError

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


class _Unrepeated(yaml.SafeLoader):
    """`SafeLoader`, refusing a key written twice in one mapping, at any depth.

    YAML keeps the last of two, so a second `source_ids: ["*"]` would open a definition
    to everyone without a word.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict:
        lines: dict[object, int] = {}
        for key_node, _ in node.value:
            # A merge (`<<: *base`) has no constructor of its own, and overriding what it
            # brings in is what it is for.
            if key_node.tag == "tag:yaml.org,2002:merge":
                continue
            key = self.construct_object(key_node, deep=True)
            if not isinstance(key, Hashable):
                continue  # PyYAML refuses it, in its own words
            line = key_node.start_mark.line + 1
            if (first := lines.get(key)) is not None:
                msg = (
                    f"{key!r} is written twice, on lines {first} and {line}, and YAML "
                    "would keep only the last -- write it once"
                )
                raise ConstructorError(problem=msg)
            lines[key] = line
        return super().construct_mapping(node, deep=deep)


def load(text: str) -> object:
    """A YAML document, refused where it writes one key twice in a mapping."""
    return yaml.load(text, Loader=_Unrepeated)  # noqa: S506 -- a SafeLoader


def decode(text: str) -> dict[str, object] | str:
    """A definition's fields, or one line saying why it could not be read."""
    try:
        parsed = load(text)
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
    """Refuse any text of a `system_prompt`, short or long, written in a style that reflows
    it."""
    node = yaml.compose(text)
    if not isinstance(node, yaml.MappingNode):  # pragma: no cover -- decode checked
        return
    for where, value in _prompt_texts(node):
        if value.style != LITERAL:
            written = f"{value.style!r}" if value.style else "a plain scalar"
            msg = (
                f"{source.name}: {where} is written as {written}, which reflows it. "
                f"Use a literal block -- `{where.rsplit('.', 1)[-1]}: {LITERAL}` -- so the "
                "prompt reaches the model with the line breaks you wrote"
            )
            raise error(msg)


def _prompt_texts(document: yaml.MappingNode) -> Iterator[tuple[str, yaml.ScalarNode]]:
    """Every piece of prompt text a definition writes, with where it is written.

    A one-line slot entry too: unquoted, `text: Revenue #1 is net` loads as `Revenue`.
    """
    prompt = _value_of(document, "system_prompt")
    if isinstance(prompt, yaml.ScalarNode):
        yield "system_prompt", prompt
    if not isinstance(prompt, yaml.MappingNode):
        return
    if isinstance(text := _value_of(prompt, "text"), yaml.ScalarNode):
        yield "system_prompt.text", text
    slots = _value_of(prompt, "slots")
    for name, entries in slots.value if isinstance(slots, yaml.MappingNode) else ():
        for at, entry in enumerate(entries.value if isinstance(entries, yaml.SequenceNode) else ()):
            if isinstance(entry, yaml.MappingNode) and isinstance(
                text := _value_of(entry, "text"), yaml.ScalarNode
            ):
                yield f"system_prompt.slots.{name.value}[{at}].text", text


def _value_of(mapping: yaml.MappingNode, key: str) -> yaml.Node | None:
    return next((value for k, value in mapping.value if getattr(k, "value", None) == key), None)

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

from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from kingfisher.kinds.importing import skipped

if TYPE_CHECKING:
    from pathlib import Path

#: What a definition file is called, and the spelling that used to vanish. `.yml` is
#: valid YAML everywhere else, so a file named that way is a definition somebody wrote
#: and kingfisher silently did not read.
#:
#: Here for the reason everything else in this module is: what a document is called is
#: a fact about the document rather than about what either kind means, and both kinds
#: need it -- the subagent format to walk its own directory, the agent catalogue to
#: walk its.
SUFFIX = ".yaml"
NEAR_MISS = ".yml"

#: The scalar style that keeps a prompt's line breaks. `|`, `|2`, `|-` and
#: `|+` are all this one style once parsed -- the suffix never reaches the node.
LITERAL = "|"


def documents_in(
    directory: Path, *, error: type[ValueError], skipping: frozenset[str] = frozenset()
) -> list[Path]:
    """Every definition document below `directory`, at any depth, in a stable order.

    A folder is organisation and is walked into, unless the kind names it in
    `skipping`: a subagent bundle keeps its own assets in folders beside its
    definition, and those hold no definitions of its own.

    One walk for both kinds because the refusal below is the point of it. A `.yml`
    file is a definition somebody wrote that kingfisher would not read, and the two
    copies of that sentence were byte-identical -- which is a copy that has not
    drifted yet rather than one that cannot.
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
        elif entry.suffix == NEAR_MISS:
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
        # One line: this ends up inside an `AgentError` or `SubagentError`
        # message, and YAML's own report spans several with a caret diagram.
        return " ".join(str(exc).split())
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        return f"expected a mapping of fields, got {type(parsed).__name__}"
    return {str(key): value for key, value in parsed.items()}


@dataclass(frozen=True)
class DefinitionText:
    """A definition as written, and the file it is named after in a refusal.

    One value rather than a path, so a caller that keeps the text keeps what was parsed:
    the agent catalogue pins it into a session, and opening the file a second time for
    that would let an edit land in between -- the first turn and every later one
    running different agents.
    """

    text: str
    source: Path

    @classmethod
    def at(cls, path: Path) -> DefinitionText:
        return cls(path.read_text(encoding="utf-8"), path)


def fields_of(definition: DefinitionText, error: type[ValueError]) -> dict[str, object]:
    """A definition's fields, or the refusal naming the file and what stopped it.

    Everything both kinds do to a document before their own format looks at it, which
    is what this module is for. `decode` answers fields *or* a line saying why not,
    and that union only means anything together with the sentence reporting it -- so
    both readers wrote the same five lines, down to the wording, and the check below
    on the line after.
    """
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
                "reaches the delegate with the line breaks you wrote"
            )
            raise error(msg)
        return

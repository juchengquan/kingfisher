"""The YAML step, and the two scans that read a document without parsing it.

`domain.fields` owns what a field means; this owns the one step that needs a
library. `yaml.safe_load` sat in the domain until the boundary was made
deny-by-default — a domain module imports the standard library and
`kingfisher.domain`, nothing else — and this is where it landed.

It reads no kind. Each format's own reader lives with the format --
`subagents.reading.read`, `skills.reading.name_from`, and `catalogue.agents` for
the kind that has no module of its own. What is here is what all three share:
`decode` and `require_literal_prompt`, because a scalar's style is a fact about a
document rather than about what any kind means.

It is YAML, parsed as YAML. Hand-rolling a `key: value` reader made kingfisher
*stricter than the format it mirrors*: deepagents accepts anchors, folded scalars
and block lists when it reads a skill -- the Agent Skills spec's own form for
`allowed-tools` among them -- so a skill that loaded fine could not be uploaded.

`safe_load`, so a document cannot construct arbitrary objects. Definitions arrive
from a catalogue service under `DefinitionStore`, which makes them input rather
than something we wrote.

Flat in `infrastructure/` rather than inside `catalogue/`, and the reason is an
import cycle rather than tidiness: `catalogue/__init__` imports three kind
modules, and two of them need this.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from kingfisher.domain.access import AUDIENCED

if TYPE_CHECKING:
    from pathlib import Path

#: The scalar style that keeps a prompt's line breaks. `|`, `|2`, `|-` and
#: `|+` are all this one style once parsed -- the suffix never reaches the node.
LITERAL = "|"


def groups_named(text: str) -> tuple[str, ...]:
    """Every group a definition names, for a document that may not parse.

    Its own line and its entries both. Written for the same caller and the same
    reason as `middleware_named`: `seed` has to decide whether a definition
    belongs in a workspace that may not have what it names, and a definition
    naming a group the vocabulary does not declare is refused when the
    catalogue is read.

    `"*"` is not a name and is deliberately absent, exactly as it is there. It
    means everyone, resolves against no vocabulary at all, and is the one
    audience a definition can carry into any workspace.

    Never raises, for the reason its neighbour gives: a syntax error belongs to
    the loader that can name the format, not to a copy.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(parsed, dict):
        return ()

    found: list[str] = []
    _collect(parsed.get("groups"), into=found)
    for field_name in AUDIENCED:
        entries = parsed.get(field_name)
        # A list whose entries are names, or mappings of `name` and `groups`. A
        # reader that walks the wrong shape finds nothing and reports nothing:
        # a definition naming no group is exactly what a definition this cannot
        # read looks like from here.
        if isinstance(entries, (list, tuple)):
            for entry in entries:
                if isinstance(entry, dict):
                    _collect(entry.get("groups"), into=found)
    return tuple(dict.fromkeys(found))


def _collect(written: object, *, into: list[str]) -> None:
    """The names in one audience, appended. Anything else is the format's to refuse.

    An entry may be a conjunction -- `{all_of: [finance, senior]}` -- and its
    parts are named as surely as a bare name is. Missed, `seed` would copy a
    definition whose only undeclared group is written inside one, into a
    workspace that then refuses to start: the exact failure the rule this feeds
    exists to prevent.
    """
    if isinstance(written, str):
        written = [written]
    if not isinstance(written, (list, tuple)):
        return
    for one in written:
        if isinstance(one, dict):
            _collect(one.get("all_of"), into=into)
        elif isinstance(one, str) and one.strip() and one.strip() != "*":
            into.append(one.strip())


def middleware_named(text: str) -> tuple[str, ...]:
    """The middleware a definition names, for a document that may not parse.

    Two callers want this and neither wants a whole spec: `seed`, deciding
    whether a definition belongs in a fresh workspace, and the test holding it
    to that. Written once because the two must agree -- a rule enforced by one
    reading and checked by another is a rule with a seam in it.

    Both spellings, because a definition may write either and they mean the
    same thing here. A `"*"` is *not* a name and is deliberately absent from
    the result: it resolves against whatever the deployment registered, which
    on an empty registry is nothing, and raises nothing either way. That is
    what makes it the one form a definition can carry anywhere.

    Never raises. A document that does not parse, or parses to something other
    than a mapping, has a loader whose job is to say so in the terms of its own
    format -- and saying it here, during a copy, would report a definition's
    syntax error as a seeding failure. Empty means "nothing to act on", which
    for a broken file is the answer that leaves the real error reachable.
    """
    try:
        parsed = yaml.safe_load(text)
    except yaml.YAMLError:
        return ()
    if not isinstance(parsed, dict):
        return ()

    written = parsed.get("middleware")
    if isinstance(written, str):
        written = [written]
    if not isinstance(written, (list, tuple)):
        return ()

    found = []
    for entry in written:
        # A mapping is the long form, `{name, settings}`; a string is the name
        # on its own. Anything else is a definition the format will refuse, and
        # refusing it here is not this function's job.
        name = entry.get("name") if isinstance(entry, dict) else entry
        if isinstance(name, str) and name.strip() and name.strip() != "*":
            found.append(name.strip())
    return tuple(found)


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


def require_literal_prompt(text: str, source: Path, error: type[ValueError]) -> None:
    """Refuse a `system_prompt` written in a style that reflows it.

    `>` folds consecutive lines into one, so

        1. Recompute the figure.
        2. Say which definition you applied.

    reaches the delegate as a single run-on line. The document is valid, the
    definition looks right on screen, and the only symptom is a delegate
    behaving oddly. A plain or quoted scalar does the same, harder.

    Only the *style* is checked, not the indentation indicator: `|` and `|2`
    are the same scalar to a parser -- the indicator is consumed by the scanner
    and never reaches the node -- and the mistake it guards against already
    refuses to load. Checking it would mean reading the document a second time
    by hand, to catch something that is not silent.

    Here rather than in `subagents.reading` because a scalar's style is a fact
    about the document, not about what a subagent means. The domain is handed
    fields; by then every style looks alike.

    Both formats spell the prompt `system_prompt` and both reflow it the same
    way, so one check serves them -- with the format's own exception passed in,
    since that is the half a reader needs to be right about which file to open.
    """
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

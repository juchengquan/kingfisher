"""Reading a definition document into the value the domain works with.

`domain.fields` owns what a field means; this owns the one step that
needs a library. `yaml.safe_load` sat in the domain until the boundary was made
deny-by-default — a domain module imports the standard library and
`kingfisher.domain`, nothing else — and this is where it landed.

It is YAML, parsed as YAML. This used to hand-roll a `key: value` reader, on
the reasoning that a YAML dependency would accept anchors, multi-line blocks and
type coercion into a format whose point is that a person can read it at a
glance. deepagents accepts exactly those when it reads a skill, which made
kingfisher *stricter than the format it mirrors*: a folded description or a
block list — the Agent Skills spec's own form for `allowed-tools` — parsed there
and raised here. Definitions skills are never read by kingfisher, but uploaded
ones are, so a skill that loaded fine could not be uploaded.

`safe_load`, so a document cannot construct arbitrary objects. Definitions
arrive from a catalogue service under `DefinitionStore`, which makes them input
rather than something we wrote.

Named `documents` rather than `definitions`, which is what it was called while
it sat flat in `infrastructure/`. The old name was chosen against `domain.fields`
-- one name across two layers makes every import a small act of guessing, which
is why `narrowing` is not called `capabilities` either -- and the move gave it a
nearer collision than the one it was avoiding: `Definitions`, the deployment's
three repositories, is defined one file away in this package's `__init__`. Two
unrelated things a directory listing apart is worse than two related things a
layer apart. `documents` is what the first line already said it does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from kingfisher.domain import agent, skill, subagent
from kingfisher.domain.subagent import reading

if TYPE_CHECKING:
    from pathlib import Path

#: The scalar style that keeps a prompt's line breaks. `|`, `|2`, `|-` and
#: `|+` are all this one style once parsed -- the suffix never reaches the node.
LITERAL = "|"



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


def _opened(text: str, label: str, error: type[ValueError]) -> tuple[dict[str, object], str]:
    """The envelope: a header that decodes, and a body.

    Both formats fail the same two ways here and say so in their own exception
    type, because `SkillError` and `SubagentError` are not interchangeable to
    someone reading a traceback.
    """
    parts = skill.split(text)
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
    """One subagent definition. Raises `SubagentError` on anything malformed.

    The whole document, not a header and a body: a subagent is YAML through
    and through, so there is no envelope to open. A skill still has one --
    that format is deepagents', and it is markdown with a header.
    """
    fields = decode(text)
    if isinstance(fields, str):
        msg = f"{source.name}: cannot read definition ({fields})"
        raise subagent.SubagentError(msg)
    _require_literal_prompt(text, source, subagent.SubagentError)
    return reading.parse(fields, source)


def read_agent(text: str, source: Path) -> agent.AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed.

    The same three steps a subagent takes, and deliberately not a shared
    function taking a parser: what differs is the exception, and that is the
    one thing a caller reading a traceback needs to be right. `AgentError`
    and `SubagentError` are not interchangeable to someone finding out which
    of two folders holds the broken file.
    """
    fields = decode(text)
    if isinstance(fields, str):
        msg = f"{source.name}: cannot read definition ({fields})"
        raise agent.AgentError(msg)
    _require_literal_prompt(text, source, agent.AgentError)
    return agent.parse(fields, source)


def _require_literal_prompt(text: str, source: Path, error: type[ValueError]) -> None:
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

    Here rather than in `domain.subagent.reading` because a scalar's style is a fact
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


def skill_name(text: str, source: str = skill.FILENAME) -> str:
    """A skill's declared name, which is also its directory name."""
    fields, _ = _opened(text, source, skill.SkillError)
    return skill.name_of(fields, source=source)

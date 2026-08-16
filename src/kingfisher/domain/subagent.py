"""Subagent definitions: `/subagents/<name>.md`.

Deliberately the same shape as a skill — YAML frontmatter and a markdown body —
so a contributor who has written one does not need to learn a second mechanism.
`name` and `description` are required, `tools`, `skills`, `middleware` and
`model` are optional, and the body *is* the system prompt. `tools`, `skills`
and `middleware` all select by name from what the deployment already offers;
how each selection is enforced is the adapter's problem, not this format's.

    ---
    name: reviewer
    description: Checks an analysis for arithmetic errors and unsupported claims.
    tools: [read_file, glob, grep]
    skills: [tabular-qa]
    middleware: [audit]
    model: MiniMax-M2.5
    ---
    You review analyses...

**Omitting `tools` inherits the parent's; omitting `skills` grants none.** The
asymmetry is deliberate. Tools are what a delegate needs to *act* and it can do
nothing without them, so inheriting is the useful default. Skills are what it
needs to *know*, and the body below is already its procedure — a delegate that
needed the whole index would not have been worth defining. Handing it over also
costs: the listing is injected into the delegate's prompt, measured at ~464
tokens for three skills and growing with the catalogue.

`middleware` names entries from a registry the *deployment* supplies, so it is
the one field that selects code rather than content. It is empty until someone
wires one, and a name must be both registered and granted — including for a
definition a caller uploaded, which gets none of the leeway an uploaded skill
does. An uploaded skill is the caller's own text; a middleware name is not.

The optional `model` is where per-role cost routing lands naturally: reading
heavy delegation on a cheap model, synthesis on the expensive one.

Parsing lives in the domain because this is kingfisher's format, not a library's
— nothing here knows deepagents exists, and nothing here reads a disk. Finding
the files is `adapters.subagent_store`; translating a spec into deepagents'
`SubAgent` is `adapters.agent`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain import frontmatter

DIRECTORY = "subagents"
SUFFIX = ".md"


class SubagentError(ValueError):
    """Raised when a subagent definition cannot be read."""


@dataclass(frozen=True)
class SubagentSpec:
    """One subagent, as the workspace defines it."""

    name: str
    description: str
    system_prompt: str
    tools: tuple[str, ...] | None = None
    #: Skills this delegate is told about. `None` means *none*, which is not
    #: what `tools` means, and the difference is deliberate: tools are what a
    #: delegate needs to act, skills are what it needs to know -- and its body
    #: already is its procedure. Inheriting the caller's index would also put
    #: it in a context whose narrowness is the reason to delegate at all.
    skills: tuple[str, ...] | None = None
    #: Middleware this delegate runs with, by name, from a registry the
    #: deployment supplies. A name here selects *code*, which is why it is the
    #: one field never widened for an uploaded definition.
    middleware: tuple[str, ...] | None = None
    model: str | None = None


def _parse_frontmatter(text: str, source: Path) -> tuple[dict[str, object], str]:
    parts = frontmatter.split(text)
    if parts is None:
        msg = f"{source.name}: expected YAML frontmatter delimited by ---"
        raise SubagentError(msg)

    header, body = parts
    fields = frontmatter.fields(header)
    if isinstance(fields, str):
        msg = f"{source.name}: cannot read frontmatter ({fields})"
        raise SubagentError(msg)
    return fields, body


def parse(text: str, source: Path) -> SubagentSpec:
    """Parse one definition. Raises `SubagentError` on anything malformed."""
    fields, body = _parse_frontmatter(text, source)

    for required in ("name", "description"):
        if not fields.get(required):
            msg = f"{source.name}: frontmatter is missing required field {required!r}"
            raise SubagentError(msg)
    if not body:
        msg = f"{source.name}: the body is the system prompt and must not be empty"
        raise SubagentError(msg)

    return SubagentSpec(
        name=frontmatter.text(fields["name"]),
        description=frontmatter.text(fields["description"]),
        system_prompt=body,
        # `[read_file, grep]` and a block list are the same thing to YAML, so
        # both reach here already parsed.
        tools=frontmatter.names(fields.get("tools")),
        skills=frontmatter.names(fields.get("skills")),

        middleware=frontmatter.names(fields.get("middleware")),
        model=frontmatter.text(fields["model"]) if fields.get("model") else None,
    )

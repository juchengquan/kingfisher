"""Subagent definitions: `/subagents/<name>.md`.

Deliberately the same shape as a skill — YAML frontmatter and a markdown body —
so a contributor who has written one does not need to learn a second mechanism.
`name` and `description` are required, `tools` and `model` are optional, and
the body *is* the system prompt. `tools` selects from what the parent agent
already has, by name; how that selection is enforced is the adapter's problem,
not this format's.

    ---
    name: reviewer
    description: Checks an analysis for arithmetic errors and unsupported claims.
    tools: [read_file, glob, grep]
    model: MiniMax-M2.5
    ---
    You review analyses...

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
        model=frontmatter.text(fields["model"]) if fields.get("model") else None,
    )

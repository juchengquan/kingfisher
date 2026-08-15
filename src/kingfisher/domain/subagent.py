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
— nothing here knows deepagents exists. Translation into a `SubAgent` happens in
the adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DIRECTORY = "subagents"
SUFFIX = ".md"

#: The shortest quoted scalar is a pair of quotes with nothing between them.
QUOTED_MINIMUM = 2

_FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", re.DOTALL)


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


def _parse_scalar(value: str) -> str:
    value = value.strip()
    if value[:1] in {'"', "'"} and value[-1:] == value[:1] and len(value) >= QUOTED_MINIMUM:
        return value[1:-1]
    return value


def _parse_frontmatter(text: str, source: Path) -> tuple[dict[str, str], str]:
    match = _FRONTMATTER.match(text)
    if not match:
        msg = f"{source.name}: expected YAML frontmatter delimited by ---"
        raise SubagentError(msg)

    fields: dict[str, str] = {}
    for raw in match.group(1).splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            msg = f"{source.name}: cannot parse frontmatter line {line!r}"
            raise SubagentError(msg)
        fields[key.strip()] = value.strip()
    return fields, match.group(2).strip()


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

    raw_tools = fields.get("tools")
    tools: tuple[str, ...] | None = None
    if raw_tools is not None:
        inner = raw_tools.strip().removeprefix("[").removesuffix("]")
        tools = tuple(_parse_scalar(t) for t in inner.split(",") if t.strip())

    return SubagentSpec(
        name=_parse_scalar(fields["name"]),
        description=_parse_scalar(fields["description"]),
        system_prompt=body,
        tools=tools,
        model=_parse_scalar(fields["model"]) if fields.get("model") else None,
    )


def load_all(workspace: Path) -> dict[str, SubagentSpec]:
    """Every subagent the workspace defines, keyed by name.

    The filename is not authoritative — the frontmatter `name` is, since that
    is what a request names and what the `task` tool will use.
    """
    directory = Path(workspace) / DIRECTORY
    if not directory.is_dir():
        return {}

    specs: dict[str, SubagentSpec] = {}
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        spec = parse(path.read_text(encoding="utf-8"), path)
        if spec.name in specs:
            msg = f"{path.name}: duplicate subagent name {spec.name!r}"
            raise SubagentError(msg)
        specs[spec.name] = spec
    return specs

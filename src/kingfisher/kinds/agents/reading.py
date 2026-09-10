"""Turning an agent document into a spec.

`spec` says what a definition means; this says how a file becomes one. Named
`read` because the module says which kind, as `kinds.subagents.reading.read` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.infrastructure import documents
from kingfisher.kinds.agents import spec as agent
from kingfisher.kinds.agents.spec import AgentError, AgentSpec

if TYPE_CHECKING:
    from pathlib import Path


def read(text: str, source: Path) -> AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed."""
    document = documents.decode(text)
    if isinstance(document, str):
        msg = f"{source.name}: cannot read definition ({document})"
        raise AgentError(msg)
    documents.require_literal_prompt(text, source, AgentError)
    return agent.parse(document, source)

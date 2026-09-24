"""Turning an agent document into a spec.

`spec` says what a definition means; this says how a file becomes one. Named
`read` because the module says which kind, as `kinds.subagents.reading.read` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.kinds import documents
from kingfisher.kinds.agents import spec as agent
from kingfisher.kinds.agents.spec import AgentError, AgentSpec

if TYPE_CHECKING:
    from pathlib import Path


def read(text: str, source: Path) -> AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed.

    The text rather than the path, unlike the subagent reader: the catalogue pins the
    document it parsed into the session, and reading the file again to get it would let
    an edit land in between -- the first turn and every later one running different
    agents.
    """
    return agent.parse(documents.fields_of(text, source, AgentError), source)

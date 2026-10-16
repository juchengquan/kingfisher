"""Turning an agent document into a spec.

`spec` says what a definition means; this says how a file becomes one. Named
`read` because the module says which kind, as `core.definitions.subagents.reading.read` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.core.definitions import documents
from kingfisher.core.definitions.agents import spec as agent
from kingfisher.core.definitions.agents.spec import AgentError, AgentSpec

if TYPE_CHECKING:
    from kingfisher.core.definitions.documents import DefinitionText


def read(definition: DefinitionText) -> AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed."""
    return agent.parse(documents.fields_of(definition, AgentError), definition.source)

"""Turning an agent document into a spec."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.core.definitions import documents

from . import spec as agent
from .spec import AgentError, AgentSpec

if TYPE_CHECKING:
    from kingfisher.core.definitions.documents import DefinitionText


def read(definition: DefinitionText) -> AgentSpec:
    """One agent definition. Raises `AgentError` on anything malformed."""
    return agent.parse(documents.fields_of(definition, AgentError), definition.source)

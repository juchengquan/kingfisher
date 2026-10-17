"""Turning a subagent document into a spec."""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.core.definitions import documents
from kingfisher.core.definitions.subagents import spec as subagent
from kingfisher.core.definitions.subagents.spec import SubagentError, SubagentSpec

if TYPE_CHECKING:
    from kingfisher.core.definitions.documents import DefinitionText


def read(definition: DefinitionText) -> SubagentSpec:
    """One definition; raises `SubagentError` on anything malformed."""
    return subagent.parse(documents.fields_of(definition, SubagentError), definition.source)

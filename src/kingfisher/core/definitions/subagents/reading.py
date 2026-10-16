"""Turning a subagent document into a spec.

`spec` says what a definition means; this says how a file becomes one. Named `read`
because the module says which kind, as `core.definitions.agents.reading.read` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.core.definitions import documents
from kingfisher.core.definitions.subagents import spec as subagent
from kingfisher.core.definitions.subagents.spec import SubagentError, SubagentSpec

if TYPE_CHECKING:
    from kingfisher.core.definitions.documents import DefinitionText


def read(definition: DefinitionText) -> SubagentSpec:
    """One definition. Raises `SubagentError` on anything malformed."""
    return subagent.parse(documents.fields_of(definition, SubagentError), definition.source)

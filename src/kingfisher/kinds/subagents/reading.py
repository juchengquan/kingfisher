"""Turning a subagent document into a spec.

`spec` says what a definition means; this says how a file becomes one. Named `read`
because the module says which kind, as `kinds.agents.reading.read` does.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from kingfisher.kinds import documents
from kingfisher.kinds.subagents import spec as subagent
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec

if TYPE_CHECKING:
    from kingfisher.kinds.documents import DefinitionText


def read(definition: DefinitionText) -> SubagentSpec:
    """One definition. Raises `SubagentError` on anything malformed."""
    return subagent.parse(documents.fields_of(definition, SubagentError), definition.source)

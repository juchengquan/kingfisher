"""`kingfisher.tool`: a workspace tool that says who may use it."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any, cast

from langchain_core.tools import StructuredTool

from kingfisher.domain.capabilities import ALL
from kingfisher.domain.fields import Reader
from kingfisher.kinds.tools.catalogue import ToolError

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.domain.access import Audience


class AudiencedTool(StructuredTool):
    """A `StructuredTool` carrying an audience kingfisher reads and LangChain never sees."""

    # Private rather than a field or `metadata`: LangChain hands a tool's `metadata` to
    # every callback, so an audience kept there would be in the trace of every call. The
    # leading underscore is what makes pydantic keep it off the model's fields.
    _source_ids: Audience = ALL

    # Named `kinds.tools.spec.AUDIENCE_ATTRIBUTE`, which is how the loaders find it.
    @property
    def kingfisher_source_ids(self) -> Audience:
        """Who this tool admits by itself."""
        return self._source_ids


def tool(
    fn: Callable[..., Any],
    *,
    description: str | None = None,
    args_schema: Any = None,
    name: str | None = None,
    source_ids: object = None,
) -> AudiencedTool:
    """A tool made from `fn`, admitting only the callers `source_ids` describes.

    `source_ids` is written as a definition's own line is: a list of source ids, any one
    of which is enough, where an entry may be a set every name of which must be held.
    Left out, the tool admits whoever the agent granting it admits.
    """
    # `cls(...)` inside, so this is the subclass; langchain types it as the base.
    made = cast("AudiencedTool", AudiencedTool.from_function(
        coroutine=fn if inspect.iscoroutinefunction(fn) else None,
        func=None if inspect.iscoroutinefunction(fn) else fn,
        name=name,
        description=description,
        args_schema=args_schema,
    ))
    reader = Reader(source=f"tool {made.name!r}", error=ToolError)
    made._source_ids = reader.source_ids(source_ids)
    return made

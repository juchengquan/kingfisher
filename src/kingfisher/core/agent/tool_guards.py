"""The guards a compiled subagent's workspace tools carry on themselves."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from langchain_core.tools import BaseTool, StructuredTool, ToolException

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import SessionPaths, refusal_text


class GuardedTool(BaseTool):
    """One workspace tool, carrying the guards a compiled subagent cannot be given.

    deepagents runs a compiled subagent as given, so no middleware of kingfisher's is in
    front of its tools; the guards travel on the tools instead.

    A refusal is returned, never raised: nothing in a graph kingfisher did not build
    catches an exception, so the run would end. `handle_tool_error` makes it a failed
    result, so `ToolMessage.status` reports the failure.
    """

    #: Typed `Any` rather than `BaseTool` and `SessionPaths`: this is a pydantic
    #: model, and naming those would make the wrapper refuse a tool it can carry.
    inner: Any
    #: `None` where a build has no session to translate against; errors still convert.
    paths: Any = None

    def _translated(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.paths is None:
            return kwargs
        try:
            return self.paths.translated(kwargs)
        except (UnsafeReferenceError, HostPathError) as escaped:
            raise ToolException(refusal_text(escaped, self.paths)) from escaped

    def _as_tool_call(self, args: dict[str, Any]) -> dict[str, Any]:
        """The call form, the only one that carries a `content_and_artifact` tool's
        artifact back.
        """
        return {"type": "tool_call", "id": "guarded", "name": self.inner.name, "args": args}

    def _failed(self, exc: Exception) -> ToolException:
        # The type as well as the message: `FileNotFoundError: /inputs/x.csv` tells a
        # model far more than the path.
        msg = f"Error: {type(exc).__name__}: {exc}"
        return ToolException(msg)

    def _run(self, **kwargs: Any) -> Any:
        args = self._translated(kwargs)
        try:
            if self.response_format == "content_and_artifact":
                answered = self.inner.invoke(self._as_tool_call(args))
                return answered.content, answered.artifact
            return self.inner.invoke(args)
        except ToolException:
            raise
        except Exception as exc:
            raise self._failed(exc) from exc

    async def _arun(self, **kwargs: Any) -> Any:
        args = self._translated(kwargs)
        try:
            if self.response_format == "content_and_artifact":
                answered = await self.inner.ainvoke(self._as_tool_call(args))
                return answered.content, answered.artifact
            return await self.inner.ainvoke(args)
        except ToolException:
            raise
        except Exception as exc:
            raise self._failed(exc) from exc


def guarded_tools(tools: Sequence[Any], paths: SessionPaths | None) -> list[Any]:
    """The workspace tools a compiled subagent is handed, each one wrapped."""
    return [_guarded(one, paths) for one in tools]


def _guarded(one: Any, paths: SessionPaths | None) -> BaseTool:
    """One tool, wrapped without changing what it advertises.

    A plain function becomes the tool `create_agent` would make of it, refusing a
    missing docstring as that does; as a `BaseTool` it can report a refusal at all.
    """
    inner = one if isinstance(one, BaseTool) else _as_tool(one)
    # A `BaseTool` subclass may carry its arguments on `_run`, and a wrapper taking
    # `**kwargs` would otherwise advertise `kwargs` to the model.
    declared = inner.args_schema if inner.args_schema is not None else inner.get_input_schema()
    return GuardedTool(
        inner=inner,
        paths=paths,
        name=inner.name,
        description=inner.description,
        args_schema=declared,
        response_format=inner.response_format,
        return_direct=inner.return_direct,
        handle_tool_error=True,
    )


def _as_tool(one: Any) -> BaseTool:
    """A plain function as a tool, async or not."""
    if inspect.iscoroutinefunction(one):
        return StructuredTool.from_function(coroutine=one)
    return StructuredTool.from_function(one)

"""The guards an offered tool carries on itself, where no middleware reaches it."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Any

from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool, ToolException

# langchain's own reading of what a `_run` takes, so the inner tool is handed exactly what
# its `run` would hand it. It finds the config parameter by resolving the annotation, so
# `RunnableConfig` above is imported to run, not only to type.
from langchain_core.tools.base import _get_runnable_config_param, _signature_parameters

from .host_paths import HostPathError
from .middlewares.recorder import ran
from .references import UnsafeReferenceError
from .session_paths import SessionPaths, as_asked, refusal_text
from .tool_context import naming


class GuardedTool(BaseTool):
    """One offered tool, carrying the guards no middleware can give it.

    deepagents runs a compiled subagent as given, and `eval` calls a tool from code, so
    no middleware of kingfisher's is in front of either; the guards travel on the tools.

    A refusal is returned, never raised: nothing in a graph kingfisher did not build
    catches an exception, so the run would end. `handle_tool_error` makes it a failed
    result, so `ToolMessage.status` reports the failure.
    """

    #: Typed `Any` rather than `BaseTool` and `SessionPaths`: this is a pydantic
    #: model, and naming those would make the wrapper refuse a tool it can carry.
    inner: Any
    #: `None` where a build has no session to translate against; errors still convert.
    paths: Any = None
    #: The compiled subagent this tool was handed to, which `current_graph()` answers while
    #: it runs. `None` leaves the name alone: `eval`'s tools read the graph that ran `eval`.
    graph: str | None = None

    def _named(self) -> AbstractContextManager[None]:
        return nullcontext() if self.graph is None else naming(self.graph)

    def _translated(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.paths is None:
            return kwargs
        try:
            return self.paths.translated(kwargs)
        except (UnsafeReferenceError, HostPathError) as escaped:
            raise ToolException(refusal_text(escaped, self.paths)) from escaped

    def _handed(self, run: Any, config: RunnableConfig, manager: Any) -> dict[str, Any]:
        """What the inner tool's `run` would pass its `_run` besides the arguments.

        The inner `_run` is called, not its `invoke`: a second run would report every
        call twice to each callback, the run log and a deployment's tracer alike.
        """
        handed: dict[str, Any] = {}
        if manager is not None and _signature_parameters(run).get("run_manager"):
            handed["run_manager"] = manager
        if param := _get_runnable_config_param(run):
            handed[param] = config
        return handed

    def _failed(self, exc: Exception, asked: dict[str, Any]) -> ToolException:
        # The type as well as the message: `FileNotFoundError: /inputs/x.csv` tells a
        # model far more than the path.
        #
        # `asked` is what the model wrote, not what `_translated` made of it: a tool is
        # handed the host path by design, so its own `OSError` names one, and
        # `GuardedCallsMiddleware` says the same thing for a graph kingfisher builds.
        msg = f"Error: {type(exc).__name__}: {exc}"
        return ToolException(as_asked(msg, asked))

    def _run(
        self,
        *,
        config: RunnableConfig,
        run_manager: CallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> Any:
        # No recorder sees this call, so the answer it feeds is stamped with it here.
        ran(self.inner.name)
        args = self._translated(kwargs)
        with self._named():
            try:
                return self.inner._run(
                    **args, **self._handed(self.inner._run, config, run_manager)
                )
            except ToolException:
                raise
            except Exception as exc:
                raise self._failed(exc, kwargs) from exc

    async def _arun(
        self,
        *,
        config: RunnableConfig,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> Any:
        ran(self.inner.name)
        args = self._translated(kwargs)
        # Read as `arun` reads it: a tool with no `_arun` of its own runs its `_run`.
        own = type(self.inner)._arun is not BaseTool._arun
        checked = self.inner._arun if own else self.inner._run
        with self._named():
            try:
                return await self.inner._arun(
                    **args, **self._handed(checked, config, run_manager)
                )
            except ToolException:
                raise
            except Exception as exc:
                raise self._failed(exc, kwargs) from exc


def guarded_tools(
    tools: Sequence[Any], paths: SessionPaths | None, *, graph: str | None = None
) -> list[Any]:
    """The offered tools a compiled subagent or `eval` is handed, each one wrapped."""
    return [_guarded(one, paths, graph) for one in tools]


def _guarded(one: Any, paths: SessionPaths | None, graph: str | None) -> BaseTool:
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
        graph=graph,
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

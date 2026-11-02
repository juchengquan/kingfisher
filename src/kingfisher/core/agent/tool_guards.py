"""The guards an offered tool carries on itself, where no middleware reaches it."""

from __future__ import annotations

import functools
import inspect
from collections.abc import Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import replace
from typing import Any

from langchain.tools import ToolRuntime
from langchain_core.callbacks import AsyncCallbackManagerForToolRun, CallbackManagerForToolRun
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool, StructuredTool, ToolException

# langchain's own reading of what a `_run` takes, so the inner tool is handed exactly what
# its `run` would hand it. It finds the config parameter by resolving the annotation, so
# `RunnableConfig` above is imported to run, not only to type.
from langchain_core.tools.base import _get_runnable_config_param, _signature_parameters

from .host_paths import HostPathError, UnreachablePathError
from .middlewares.recorder import ran
from .middlewares.scratch_runs import HANDED
from .middlewares.stopping import NOT_RUN, on_a_thread, stopping, waited_for
from .session_paths import SessionPaths, as_asked, refusal_text
from .tool_context import ToolContext, naming


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
    paths: Any
    #: The compiled subagent this tool was handed to, which `current_graph()` answers while
    #: it runs. `None` leaves the name alone: `eval`'s tools read the graph that ran `eval`.
    graph: str | None = None
    #: The deployment's `ScratchRunLimit` on that subagent, as `limit_answer` gives it,
    #: joined with the rule of whoever handed it the work.
    limit: tuple[bool, str | None] = (True, None)

    # langgraph and `eval`'s bridge decide what to inject from the schema *and* from
    # `func` or `coroutine`, so a tool whose schema leaves `runtime` out -- one declaring
    # `args_schema` -- is handed it only if these show the inner function's signature.
    @functools.cached_property
    def func(self) -> Any:
        return _signature_only(getattr(self.inner, "func", None))

    @functools.cached_property
    def coroutine(self) -> Any:
        return _signature_only(getattr(self.inner, "coroutine", None))

    @functools.cached_property
    def _injected_args_keys(self) -> frozenset[str]:
        # Else `run` drops an injected argument the schema does not name before `_run`.
        return self.inner._injected_args_keys

    def _named(self) -> AbstractContextManager[None]:
        return nullcontext() if self.graph is None else naming(self.graph)

    def _with_runner(self, args: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        """`args` with the runtime they inject handed a runner for this call alone, under
        the rule `eval` or `task` was called with; and the runner, to close after it."""
        rule = HANDED.get()
        for key, value in args.items():
            if isinstance(value, ToolRuntime) and isinstance(value.context, ToolContext):
                runner = None if rule is None else rule.narrowed(self.limit).runner(value.context)
                if runner is None:
                    return args, None
                bound = replace(value, context=replace(value.context, scratch=runner))
                return {**args, key: bound}, runner
        return args, None

    def _translated(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        try:
            return self.paths.translated(kwargs)
        except (UnreachablePathError, HostPathError) as escaped:
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
        if stopping():
            raise ToolException(NOT_RUN)
        # No recorder sees this call, so the answer it feeds is stamped with it here.
        ran(self.inner.name)
        args, runner = self._with_runner(self._translated(kwargs))
        with self._named():
            try:
                return self.inner._run(
                    **args, **self._handed(self.inner._run, config, run_manager)
                )
            except ToolException:
                raise
            except Exception as exc:
                raise self._failed(exc, kwargs) from exc
            finally:
                if runner is not None:
                    runner.close()

    async def _arun(
        self,
        *,
        config: RunnableConfig,
        run_manager: AsyncCallbackManagerForToolRun | None = None,
        **kwargs: Any,
    ) -> Any:
        if stopping():
            raise ToolException(NOT_RUN)
        ran(self.inner.name)
        args, runner = self._with_runner(self._translated(kwargs))
        # Read as `arun` reads it: a tool with no `_arun` of its own runs its `_run`.
        own = type(self.inner)._arun is not BaseTool._arun
        checked = self.inner._arun if own else self.inner._run
        with self._named():
            try:
                running = self.inner._arun(**args, **self._handed(checked, config, run_manager))
                if not on_a_thread(self.inner):
                    return await running
                result, cancelled = await waited_for(running)
            except ToolException:
                raise
            except Exception as exc:
                raise self._failed(exc, kwargs) from exc
            finally:
                if runner is not None:
                    runner.close()
        if cancelled is not None:
            raise cancelled
        return result


def guarded_tools(
    tools: Sequence[Any],
    paths: SessionPaths,
    *,
    graph: str | None = None,
    limit: tuple[bool, str | None] = (True, None),
) -> list[Any]:
    """The offered tools a compiled subagent or `eval` is handed, each one wrapped.

    `limit` is the deployment's answer about the compiled subagent `graph`.
    """
    return [_guarded(one, paths, graph, limit) for one in tools]


def _guarded(
    one: Any, paths: SessionPaths, graph: str | None, limit: tuple[bool, str | None]
) -> BaseTool:
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
        limit=limit,
        name=inner.name,
        description=inner.description,
        args_schema=declared,
        response_format=inner.response_format,
        return_direct=inner.return_direct,
        handle_tool_error=True,
    )


def _signature_only(original: Any) -> Any:
    """`original`'s signature and annotations, on a function that refuses to run.

    Read for its signature only; handing out the inner function itself would give whatever
    calls `tool.func` one day the tool with none of its guards.
    """
    if original is None:
        return None

    @functools.wraps(original)
    def refused(*_: Any, **__: Any) -> Any:
        msg = f"{original.__name__} runs through its GuardedTool, never on its own"
        raise TypeError(msg)

    return refused


def _as_tool(one: Any) -> BaseTool:
    """A plain function as a tool, async or not."""
    if inspect.iscoroutinefunction(one):
        return StructuredTool.from_function(coroutine=one)
    return StructuredTool.from_function(one)

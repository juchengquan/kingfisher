"""The guards every graph holding workspace tools is wrapped in."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool, StructuredTool, ToolException

from kingfisher.core.agent.backend import shell_prompt_of, shell_runs_here
from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.cut_replies import CutReplies
from kingfisher.core.agent.middlewares.host_path_guard import HostPathGuard
from kingfisher.core.agent.middlewares.large_pdf_guard import LargePdfGuard
from kingfisher.core.agent.middlewares.recorder import Recorder
from kingfisher.core.agent.middlewares.shell_paths import ShellPaths
from kingfisher.core.agent.middlewares.unavailable import ModelUnavailableGuard
from kingfisher.core.agent.middlewares.workspace_tools import (
    WorkspaceToolErrors,
    WorkspaceToolPaths,
)
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import SessionPaths, refusal_text
from kingfisher.core.config import DEFAULT_LAYOUT
from kingfisher.core.layout import Layout


def tool_guards(  # noqa: PLR0913 -- `subagent` is the one way the agent's guards differ
    # from a subagent's, and a parameter object would hide it from each call that says it.
    names: frozenset[str],
    paths: SessionPaths | None,
    recorder: Recorder | None = None,
    layout: Layout = DEFAULT_LAYOUT,
    backend: Any = None,
    *,
    subagent: bool,
) -> list[AgentMiddleware]:
    """What every graph holding workspace tools gets wrapped in, built once.

    The agent, a subagent, and deepagents' `general-purpose` subagent, which is handed
    the agent's tools, all hold them; one builder, so none of them is left unguarded.

    `CutReplies`, `HostPathGuard`, `LargePdfGuard` and `ShellPaths` go on every graph:
    any model can stop at its cap and a cut call looks whole, the backend refuses a host
    path on every run, `read_file` hands a PDF over whole, and a subagent holding
    `execute` never sees `system.md`. `subagent` says what `CutReplies` does with an
    agent it stops, the one way the graphs differ. The workspace-tool guards need
    `names`, and translation needs `paths`.

    `recorder` is `None` where no turn keeps what is recorded; `backend`'s shell says
    what `ShellPaths` tells the model about it.
    """
    # First, so it records the arguments the model wrote, before `WorkspaceToolPaths`
    # rewrites them to host paths.
    guards: list[AgentMiddleware] = [] if recorder is None else [recorder]
    # Ahead of the rest, which have nothing to say about a call that is not run.
    guards.append(CutReplies(subagent=subagent, outputs=layout.outputs_route))
    # Inside `CutReplies`, so a refused call reaches it already as the stop it is.
    guards.append(ModelUnavailableGuard())
    guards.append(HostPathGuard())
    guards.append(LargePdfGuard())
    guards.append(ShellPaths(layout, shell_prompt_of(backend), here=shell_runs_here(backend)))
    if names:
        # In this order, which the subagent's stack is pinned to: translation rewrites
        # the arguments before anything after it judges them.
        guards.append(WorkspaceToolErrors(names))
        if paths is not None:
            guards.append(WorkspaceToolPaths(names, paths))
    return guards


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

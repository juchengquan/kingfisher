"""The guards every graph holding workspace tools is wrapped in."""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.tools import BaseTool, StructuredTool, ToolException

from kingfisher.domain.references import UnsafeReferenceError
from kingfisher.infrastructure.harness.host_paths import HostPathError
from kingfisher.infrastructure.harness.middlewares.host_path_guard import HostPathGuard
from kingfisher.infrastructure.harness.middlewares.shell_path_spelling import ShellPathSpelling
from kingfisher.infrastructure.harness.middlewares.stray_write_guard import StrayWriteGuard
from kingfisher.infrastructure.harness.middlewares.workspace_tools import (
    WorkspaceToolErrors,
    WorkspaceToolPaths,
)
from kingfisher.infrastructure.harness.session_paths import SessionPaths, refusal_text


def tool_guards(names: frozenset[str], root: Path | None) -> list[AgentMiddleware]:
    """What every graph holding workspace tools gets wrapped in, built once.

    Three graphs hold them -- the agent, a delegate, and the `general-purpose` delegate
    deepagents supplies, which is handed the agent's tools and so holds the same objects.
    Composed here because each of the three was composed where it was built, and the one
    built last got none of this: a tool call through `general-purpose` reached the tool
    with its paths untranslated and a host path unrefused, which is the leak
    `WorkspaceToolPaths` exists to close.

    `HostPathGuard` is unconditional: the backend refuses a host path on every run, so
    the thing that turns that refusal into a correction the model can read has to be
    there whether or not this graph holds a workspace tool. `ShellPathSpelling` is
    unconditional because every graph holding `execute` is handed deepagents'
    description of it, and `StrayWriteGuard` because every graph holding `write_file`
    can save where nothing is kept. The other two are about the tools themselves, so
    they need names -- and translation needs somewhere to translate against, which a
    build with no session does not have.
    """
    guards: list[AgentMiddleware] = [HostPathGuard(), ShellPathSpelling(), StrayWriteGuard(root)]
    if names:
        # Beside each other and in this order, which the delegate's stack is pinned to:
        # both are about a workspace tool call, and the translation rewrites the
        # arguments before anything below it decides anything about them.
        guards.append(WorkspaceToolErrors(names))
        if root is not None:
            guards.append(WorkspaceToolPaths(names, root))
    return guards


class GuardedTool(BaseTool):
    """One workspace tool, carrying the guards a compiled delegate cannot be given.

    There is a fourth graph holding workspace tools, and `tool_guards` cannot reach
    it: a delegate the workspace compiled itself. deepagents runs that graph as
    given, so no middleware of kingfisher's is in front of its tools. Measured:
    `show-your-work` called `log_levels('/data/api.log')` and the tool raised
    `FileNotFoundError` on a path nothing had translated, ending the run. What
    kingfisher still owns is the list of objects handed to `build`, so the guards
    travel on the tools.

    **A refusal is returned, never raised.** Inside a graph kingfisher did not build
    nothing catches an exception -- measured for `ToolException`, `FileNotFoundError`
    and `ValueError` alike, each of which ended the run. `handle_tool_error` turns
    what this raises into a failed result before it leaves the tool, which also keeps
    `ToolMessage.status` true: `show_your_work` reports a call as failed by reading
    that field, so a refusal reported as success would be a worse answer than a
    crash.
    """

    #: Typed `Any` rather than `BaseTool` and `SessionPaths`: this is a pydantic
    #: model, and naming those would make the wrapper refuse a tool it can carry.
    inner: Any
    #: `None` where a build has no session to translate against, which is the
    #: condition `tool_guards` puts on `WorkspaceToolPaths` for the same reason.
    #: The error half still applies.
    paths: Any = None

    def _translated(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        if self.paths is None:
            return kwargs
        try:
            return self.paths.translated(kwargs)
        except (UnsafeReferenceError, HostPathError) as escaped:
            raise ToolException(refusal_text(escaped)) from escaped

    def _as_tool_call(self, args: dict[str, Any]) -> dict[str, Any]:
        """The call form, which is the only one that carries an artifact back.

        A tool declaring `content_and_artifact` returns the pair through a
        `ToolMessage` and the plain form drops it, so a wrapper that invoked the
        plain way would quietly lose every artifact it passed on.
        """
        return {"type": "tool_call", "id": "guarded", "name": self.inner.name, "args": args}

    def _failed(self, exc: Exception) -> ToolException:
        # The type as well as the message, for the reason `WorkspaceToolErrors`
        # gives: a workspace tool's exceptions were not written to be read by a
        # model, and `FileNotFoundError: /data/x.csv` reads far better than the path.
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


def guarded_tools(tools: Sequence[Any], root: Path | None) -> list[Any]:
    """The workspace tools a compiled delegate is handed, each one wrapped.

    The names are not needed here the way `tool_guards` needs them: everything in
    this list is a workspace tool already, chosen by the grant this delegate was
    resolved against.
    """
    paths = SessionPaths(root) if root is not None else None
    return [_guarded(one, paths) for one in tools]


def _guarded(one: Any, paths: SessionPaths | None) -> BaseTool:
    """One tool, wrapped without changing what it advertises.

    A plain function is made into the tool the graph would have made of it anyway --
    `create_agent` converts callables on the way in, and refuses one with no
    docstring exactly as this does. Normalising first is what lets a plain function
    report a refusal at all, since the reporting is `BaseTool` machinery.
    """
    inner = one if isinstance(one, BaseTool) else _as_tool(one)
    # `get_input_schema()` where a tool declares none: a `BaseTool` subclass carries
    # its arguments on `_run` instead, and a wrapper taking `**kwargs` would otherwise
    # advertise `kwargs` to the model and then be called with none of them.
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

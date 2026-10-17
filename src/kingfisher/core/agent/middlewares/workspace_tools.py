"""What a workspace tool call is wrapped in: its paths translated, its failures reported."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import SessionPaths, refusal_text


class WorkspaceToolPaths(AgentMiddleware):
    """Translate the agent's own paths into real ones, per session.

    A leak as well as a convenience: a tool wanting host paths sends the agent looking for
    them, and from there it can name any session's files. The call is rewritten rather than
    each tool wrapped, since `BaseTool`s and plain functions share only the call; where there
    is no middleware, `GuardedTool` wraps instead.
    """

    def __init__(self, names: frozenset[str], paths: SessionPaths) -> None:
        self.names = names
        self.paths = paths
        super().__init__()

    def _translated(self, request: Any) -> Any:
        """The same call with its path arguments made real, or the request
        unchanged when it names no tool of ours."""
        call = request.tool_call
        if call.get("name") not in self.names:
            return request
        args = call.get("args") or {}
        return replace(request, tool_call={**call, "args": self.paths.translated(args)})

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(self._translated(request))
        except (UnsafeReferenceError, HostPathError) as escaped:
            return self._refusal(request, escaped)

    def _refusal(self, request: Any, escaped: ValueError) -> ToolMessage:
        """A path that climbs out, reported the way `reject_host_path` reports one."""
        call = request.tool_call
        return ToolMessage(
            content=refusal_text(escaped, self.paths),
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )


class WorkspaceToolErrors(AgentMiddleware):
    """Turn a workspace tool's exception into a failed tool result.

    A built-in's failure is reported and the model carries on; without this, upstream
    re-raises anything but bad arguments, and one wrong path ends the run. It cannot tell a
    tool's exception from one raised below it, so a deployment middleware, appended last and
    nearest the tool, that raises on a workspace tool's call becomes a failed result too.
    """

    def __init__(self, names: frozenset[str]) -> None:
        super().__init__()
        self.names = names

    def _mine(self, request: Any) -> str | None:
        """The tool's name if this middleware speaks for it, else `None`."""
        name = request.tool_call.get("name")
        return name if name in self.names else None

    def _as_tool_error(self, request: Any, exc: Exception) -> ToolMessage:
        call = request.tool_call
        # The type as well as the message: a workspace tool's exceptions were not written
        # for a model, and `FileNotFoundError: /inputs/x.csv` reads better than the path.
        return ToolMessage(
            content=f"Error: {type(exc).__name__}: {exc}",
            tool_call_id=call.get("id", ""),
            name=call.get("name"),
            status="error",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if self._mine(request) is None:
                raise
            return self._as_tool_error(request, exc)

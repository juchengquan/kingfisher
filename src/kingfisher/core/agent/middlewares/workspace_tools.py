"""A workspace tool call's paths, translated from the agent's into real ones."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.results import failed
from kingfisher.core.agent.references import UnsafeReferenceError
from kingfisher.core.agent.session_paths import SessionPaths, refusal_text


class WorkspaceToolPathsMiddleware(AgentMiddleware):
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
        return failed(request.tool_call, refusal_text(escaped, self.paths))

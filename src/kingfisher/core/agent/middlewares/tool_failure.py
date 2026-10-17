"""Handing a failed tool call back to the model as a failed result, so the turn goes on."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.results import failed


# The other half of `reject_host_path`.
class ToolFailureMiddleware(AgentMiddleware):
    """Turn a host path refused on any tool, and anything a workspace tool raises, into a
    failed tool result.

    A built-in's other failures are reported already and the model carries on; without
    this, upstream re-raises anything but bad arguments, and one wrong path ends the run.
    It cannot tell a workspace tool's exception from one raised below it, so a deployment
    middleware, appended last and nearest the tool, that raises on a workspace tool's call
    becomes a failed result too.
    """

    def __init__(self, names: frozenset[str] = frozenset()) -> None:
        super().__init__()
        self.names = names

    def _failed(self, request: Any, exc: Exception) -> ToolMessage | None:
        """The result to hand back, or `None` when the exception is not this one's."""
        call = request.tool_call
        # Ahead of the host-path case: a workspace tool's exceptions were not written for
        # a model, and `FileNotFoundError: /inputs/x.csv` reads better than the path.
        if call.get("name") in self.names:
            return failed(call, f"Error: {type(exc).__name__}: {exc}")
        if isinstance(exc, HostPathError):
            return failed(call, f"Error: {exc}")
        return None

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if (result := self._failed(request, exc)) is None:
                raise
            return result

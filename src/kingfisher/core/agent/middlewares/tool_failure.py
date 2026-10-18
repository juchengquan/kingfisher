"""Handing a failed tool call back to the model as a failed result, so the turn goes on."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.results import failed
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import unavailable


def _handed_back(call: Any, exc: Exception) -> ToolMessage | None:
    """A subagent's refused model call, as what its parent is told.

    Raw provider errors as well as kingfisher's own: a compiled subagent runs with no
    middleware of kingfisher's in front of its model.
    """
    if call.get("name") != TASK_TOOL or (why := unavailable(exc)) is None:
        return None
    whose = (call.get("args") or {}).get("subagent_type") or "the subagent"
    return failed(
        call,
        f"Error: {whose!r} could not reach its model ({why}). Go on without it, or "
        "say in your answer that this part could not be done.",
    )


# The other half of `reject_host_path`.
class ToolFailureMiddleware(AgentMiddleware):
    """Turn what running a tool call raised into a failed tool result: a host path refused
    on any tool, anything a workspace tool raises, and a subagent's model refusing its
    `task`.

    What running the call raised, not a call refused before it ran: that is the refusing
    middleware's to answer. A workspace tool's failure reads as it does on `GuardedTool`.
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
            # The one exception a tool writes for the model.
            if isinstance(exc, ToolException):
                return failed(call, str(exc))
            return failed(call, f"Error: {type(exc).__name__}: {exc}")
        if isinstance(exc, HostPathError):
            return failed(call, f"Error: {exc}")
        return _handed_back(call, exc)

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

"""A model call the provider could not serve: the turn's stop, or a subagent's failed task."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage

from kingfisher.core.agent.middlewares.results import failed
from kingfisher.core.definitions.subagents import TASK_TOOL
from kingfisher.core.session.runtime import ModelUnavailableError, unavailable


class ModelUnavailableGuard(AgentMiddleware):
    """Raise a provider's refusal of a model call as the turn's stop, and hand a
    subagent's back to its parent as a failed `task` result.

    On the model call rather than on the turn's exception, so a tool that times out on a
    request of its own is not taken for a model that cannot answer.
    """

    def _raised(self, exc: Exception) -> Exception:
        why = None if isinstance(exc, ModelUnavailableError) else unavailable(exc)
        return exc if why is None else ModelUnavailableError(why)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            raised = self._raised(exc)
            if raised is exc:
                raise
            raise raised from exc

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            raised = self._raised(exc)
            if raised is exc:
                raise
            raise raised from exc

    def _handed_back(self, request: Any, exc: Exception) -> ToolMessage | None:
        """A subagent's refused model call, as what its parent is told.

        Raw provider errors as well as this guard's own: a compiled subagent runs with no
        middleware of kingfisher's in front of its model.
        """
        call = request.tool_call
        if call.get("name") != TASK_TOOL or (why := unavailable(exc)) is None:
            return None
        whose = (call.get("args") or {}).get("subagent_type") or "the subagent"
        return failed(
            call,
            f"Error: {whose!r} could not reach its model ({why}). Go on without it, or "
            "say in your answer that this part could not be done.",
        )

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        try:
            return handler(request)
        except Exception as exc:
            if (told := self._handed_back(request, exc)) is None:
                raise
            return told

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        try:
            return await handler(request)
        except Exception as exc:
            if (told := self._handed_back(request, exc)) is None:
                raise
            return told

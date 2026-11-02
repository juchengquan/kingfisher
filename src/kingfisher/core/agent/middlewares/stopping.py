"""A turn that is stopping begins no more tool calls."""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from .results import failed

#: Set once the turn this runs in is stopping: its caller stopped reading, it ran out of
#: time, or its graph failed. Read on langgraph's threads, which copy the context.
STOPPING: ContextVar[threading.Event | None] = ContextVar("kingfisher_turn_stopping", default=None)

#: What a call refused for it is answered with.
NOT_RUN = "This call did not run: the turn stopped before it began."


def stopping() -> bool:
    """Whether the turn this runs in is stopping."""
    flag = STOPPING.get()
    return flag is not None and flag.is_set()


class StoppingMiddleware(AgentMiddleware):
    """Refuse a call that begins once its turn is stopping.

    Closing a graph's stream waits for every call its reply queued, and each began then:
    40 calls asked together, stopped with a few under way, all charged.
    """

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if stopping():
            return failed(request.tool_call, NOT_RUN)
        return handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if stopping():
            return failed(request.tool_call, NOT_RUN)
        return await handler(request)

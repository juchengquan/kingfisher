"""Which graph a tool call is made in, named for as long as the call runs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.agent.tool_context import GRAPH


@contextmanager
def _naming(graph: str | None) -> Iterator[None]:
    token = GRAPH.set(graph)
    try:
        yield
    finally:
        GRAPH.reset(token)


class CurrentGraphMiddleware(AgentMiddleware):
    """Set what `kingfisher.current_graph()` answers around each of this graph's tool
    calls: `graph`, `None` for the agent.

    On every call, not once where a subagent starts: a helper that subagent starts in turn
    must name itself, and the subagent's calls after it the subagent again.
    """

    def __init__(self, graph: str | None) -> None:
        super().__init__()
        self.graph = graph

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        with _naming(self.graph):
            return handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        with _naming(self.graph):
            return await handler(request)

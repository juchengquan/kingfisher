"""The `task` tool as a program's `task()` reaches it, with the callbacks the sandbox
leaves off."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL


class _Dispatching:
    """`task` handed the callbacks of the `eval` call that dispatches it.

    The bridge gives `arun` the call's `config` but no `callbacks`, and `arun` takes its
    run's callbacks from that argument alone, so the subagent ran with none: missing from
    the run log and every tracer, and silent on the stream. Callbacks it is given are
    kept, so a bridge that passes them finds this a no-op.
    """

    def __init__(self, tool: Any) -> None:
        self._tool = tool

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)

    async def arun(self, tool_input: Any, **keywords: Any) -> Any:
        if keywords.get("callbacks") is None:
            keywords["callbacks"] = (keywords.get("config") or {}).get("callbacks")
        return await self._tool.arun(tool_input, **keywords)


class ProgramDispatchMiddleware(AgentMiddleware):
    """Hand `eval` a `task` tool that a program's `task()` passes the callbacks to.

    Through the call's runtime, which is where the bridge looks the tool up.
    """

    def _dispatching(self, request: Any) -> Any:
        runtime = request.runtime
        if request.tool_call.get("name") != INTERPRETER_TOOL or runtime is None:
            return request
        tools = [
            _Dispatching(one) if one.name == TASK_TOOL else one
            for one in runtime.tools or ()
        ]
        return replace(request, runtime=replace(runtime, tools=tools))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._dispatching(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._dispatching(request))

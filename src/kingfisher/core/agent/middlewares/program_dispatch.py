"""The `task` tool as a program's `task()` reaches it: with the callbacks the sandbox
leaves off, and named as the delegation no `ToolNode` announces."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langgraph.errors import GraphInterrupt

from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL
from kingfisher.core.session.runtime import (
    DISPATCH,
    SUBAGENT_TYPE_ARG,
    dispatch_answered,
    dispatch_started,
)

from .results import answer_in


class _Dispatching:
    """`task` handed the callbacks of the `eval` call that dispatches it, and named.

    The bridge gives `arun` the call's `config` but no `callbacks`, and `arun` takes its
    run's callbacks from that argument alone, so the subagent ran with none: missing from
    the run log and every tracer, and silent on the stream. Callbacks it is given are
    kept, so a bridge that passes them finds that part a no-op.
    """

    def __init__(self, tool: Any, *, program: str, write: Callable[[Any], None] | None) -> None:
        self._tool = tool
        #: The `eval` call's id, which the ledger files the subagent's results under.
        self._program = program
        self._write = write or (lambda _: None)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._tool, name)

    async def arun(self, tool_input: Any, **keywords: Any) -> Any:
        if keywords.get("callbacks") is None:
            keywords["callbacks"] = (keywords.get("config") or {}).get("callbacks")
        dispatched = str(keywords.get("tool_call_id") or "")
        chain = [self._program, dispatched]
        # Inherited by every run inside, which is how the record and the stream find it.
        keywords["metadata"] = {**(keywords.get("metadata") or {}), DISPATCH: chain}
        asked = tool_input if isinstance(tool_input, Mapping) else {}
        self._write(
            dispatch_started(
                chain, str(asked.get(SUBAGENT_TYPE_ARG) or ""), str(asked.get("description") or "")
            )
        )
        try:
            result = await self._tool.arun(tool_input, **keywords)
        except GraphInterrupt:
            # A pause is not an end: the resumed turn runs the program, and so this, again.
            raise
        except Exception as exc:
            self._write(dispatch_answered(dispatched, f"{type(exc).__name__}: {exc}"))
            raise
        # The answer goes back into the program, so nothing else would end the delegation.
        answer = answer_in(result, dispatched)
        self._write(dispatch_answered(dispatched, str(result if answer is None else answer.text)))
        return result


class ProgramDispatchMiddleware(AgentMiddleware):
    """Hand `eval` the `task` tool a program's `task()` reaches as `_Dispatching`.

    Through the call's runtime, which is where the bridge looks the tool up.
    """

    def _dispatching(self, request: Any) -> Any:
        runtime = request.runtime
        if request.tool_call.get("name") != INTERPRETER_TOOL or runtime is None:
            return request
        program = str(request.tool_call.get("id") or "")
        write = getattr(runtime, "stream_writer", None)
        tools = [
            _Dispatching(one, program=program, write=write) if one.name == TASK_TOOL else one
            for one in runtime.tools or ()
        ]
        return replace(request, runtime=replace(runtime, tools=tools))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return handler(self._dispatching(request))

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return await handler(self._dispatching(request))

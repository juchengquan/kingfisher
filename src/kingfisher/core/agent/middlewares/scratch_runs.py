"""An offered tool's way to run commands, handed to it per call under its caller's grant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.agent.prompting import EXECUTE
from kingfisher.core.agent.scratch import ScratchRunner
from kingfisher.core.agent.tool_context import ToolContext


class ScratchRunsMiddleware(AgentMiddleware):
    """Bind `runtime.context.run` for one offered tool's call, when the agent making it
    may use the shell itself.

    By name, so `eval` is never bound: a tool its code calls inherits `eval`'s context,
    and with it anything bound there. A gated `execute` refuses too, since nothing can
    pause for a person inside a tool.
    """

    def __init__(  # noqa: PLR0913 -- one keyword per term of the rule, and what a run needs
        self,
        names: frozenset[str],
        *,
        backend: Any,
        tools: Sequence[str] | None,
        gated: Collection[str],
        seconds: int,
        limit_allows: bool,
    ) -> None:
        super().__init__()
        self.names = names
        self.backend = backend
        self.seconds = seconds
        #: `None` is an unnarrowed grant, which holds the shell like any other built-in.
        #: The deployment's `ScratchRunLimit` is joined with `and`, so it can only take away.
        self.allowed = (
            (tools is None or EXECUTE in tools) and EXECUTE not in gated and limit_allows
        )

    def _bound(self, request: Any) -> tuple[Any, ScratchRunner | None]:
        runtime = request.runtime
        context = None if runtime is None else runtime.context
        if (
            not self.allowed
            or request.tool_call.get("name") not in self.names
            # A graph the caller built is driven with its own context, or none.
            or not isinstance(context, ToolContext)
        ):
            return request, None
        runner = ScratchRunner(self.backend, seconds=self.seconds)
        bound = replace(runtime, context=replace(context, scratch=runner))
        return replace(request, runtime=bound), runner

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        bound, runner = self._bound(request)
        try:
            return handler(bound)
        finally:
            if runner is not None:
                runner.close()

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        bound, runner = self._bound(request)
        try:
            return await handler(bound)
        finally:
            if runner is not None:
                runner.close()

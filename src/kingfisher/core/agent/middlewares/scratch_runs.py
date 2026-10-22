"""An offered tool's way to run commands, handed to it per call under its caller's grant."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection, Sequence
from dataclasses import replace
from typing import Any

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.agent.prompting import EXECUTE
from kingfisher.core.agent.scratch import ScratchRunner
from kingfisher.core.agent.tool_context import ToolContext
from kingfisher.core.interfaces import ScratchRun


class RefusingRunner:
    """Bound in place of a runner to refuse every run with `reason`, where the generic
    refusal would hide the cause."""

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def run(self, *_: Any, **__: Any) -> ScratchRun:
        return ScratchRun(error=self.reason)

    async def arun(self, *_: Any, **__: Any) -> ScratchRun:
        return ScratchRun(error=self.reason)

    def close(self) -> None:
        pass


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
        limit_refusal: str | None = None,
    ) -> None:
        super().__init__()
        self.names = names
        self.backend = backend
        self.seconds = seconds
        #: `None` is an unnarrowed grant, which holds the shell like any other built-in.
        #: The deployment's `ScratchRunLimit` is joined with `and`, so it can only take away.
        ruled = (tools is None or EXECUTE in tools) and EXECUTE not in gated
        self.allowed = ruled and limit_allows
        #: Only where kingfisher's own rule allowed the run, so the cause it names is the
        #: one that took the run away.
        self.refusal = limit_refusal if ruled and not self.allowed else None

    def _bound(self, request: Any) -> tuple[Any, ScratchRunner | RefusingRunner | None]:
        runtime = request.runtime
        context = None if runtime is None else runtime.context
        if (
            request.tool_call.get("name") not in self.names
            # A graph the caller built is driven with its own context, or none.
            or not isinstance(context, ToolContext)
        ):
            return request, None
        runner: ScratchRunner | RefusingRunner
        if self.allowed:
            runner = ScratchRunner(self.backend, seconds=self.seconds)
        elif self.refusal is not None:
            runner = RefusingRunner(self.refusal)
        else:
            return request, None
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

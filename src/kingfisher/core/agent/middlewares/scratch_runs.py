"""An offered tool's way to run commands, handed to it per call under its caller's grant."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Collection, Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from langchain.agents.middleware import AgentMiddleware

from kingfisher.core.agent.prompting import EXECUTE
from kingfisher.core.agent.scratch import ScratchRunner
from kingfisher.core.agent.tool_context import ToolContext
from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL
from kingfisher.core.interfaces import ScratchRun
from kingfisher.core.logged import raised, warn

if TYPE_CHECKING:
    from kingfisher.core.interfaces import ScratchRunLimit

#: What a `ScratchRunLimit` is told a graph is.
LimitKind = Literal["agent", "subagent", "general-purpose", "compiled"]


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


def limit_answer(limit: ScratchRunLimit, name: str, kind: LimitKind) -> tuple[bool, str | None]:
    """Whether `limit` lets graph `name`'s tools run, and what a refused run is told, where
    the limit failed to answer."""
    try:
        allows = limit.allows(name, kind)
    except Exception as error:  # noqa: BLE001 -- a plugin's bug costs this graph its
        # runs, never the turn, and never grants one.
        error_type, error_at = raised(error)
        warn(
            "scratch_run_limit_failed",
            f"the scratch run limit raised {error_type} at {error_at} on {kind} {name!r}; "
            "its tools are refused a run",
            kind=kind,
            graph=name,
            error_type=error_type,
            error_at=error_at,
        )
        return False, (
            f"refused: this deployment's ScratchRunLimit raised {type(error).__name__}: {error}"
        )
    if allows is True or allows is False:
        return allows, None
    # Only `True` grants: an `async def allows` answers a coroutine, which is truthy, and
    # read as a grant it handed every graph a run.
    warn(
        "scratch_run_limit_not_a_bool",
        f"the scratch run limit answered {type(allows).__name__} on {kind} {name!r}, not "
        "True or False; its tools are refused a run",
        kind=kind,
        graph=name,
        answered=type(allows).__name__,
    )
    if inspect.iscoroutine(allows):
        allows.close()
    return False, (
        f"refused: this deployment's ScratchRunLimit answered {type(allows).__name__}, "
        "not True or False"
    )


@dataclass(frozen=True)
class RunRule:
    """Whether one graph's offered tools are handed a run, decided once for its build."""

    backend: Any
    seconds: int
    allowed: bool
    #: What a refused run is told where the limit, not the grant, took it away.
    refusal: str | None = None

    def narrowed(self, answer: tuple[bool, str | None]) -> RunRule:
        """This rule, refused too where `answer`, a `limit_answer`, refuses.

        The rule's own refusal is kept: it is the one that took the run away first.
        """
        allows, refusal = answer
        if not self.allowed or allows:
            return self
        return replace(self, allowed=False, refusal=refusal)

    def runner(self, context: ToolContext) -> ScratchRunner | RefusingRunner | None:
        """What one call is handed as `context.scratch`; `None` refuses generically."""
        if self.allowed:
            return ScratchRunner(self.backend, seconds=self.seconds, until=context.deadline)
        if self.refusal is not None:
            return RefusingRunner(self.refusal)
        return None


#: The rule of the graph whose `eval` or `task` call is running: the one a tool reached
#: from there obeys, since no `ScratchRunsMiddleware` stands between it and its caller.
HANDED: ContextVar[RunRule | None] = ContextVar("kingfisher_run_rule", default=None)


@contextmanager
def handing(rule: RunRule) -> Iterator[None]:
    """Have `HANDED` answer `rule` until the block ends."""
    token = HANDED.set(rule)
    try:
        yield
    finally:
        HANDED.reset(token)


class ScratchRunsMiddleware(AgentMiddleware):
    """Bind `runtime.context.run` for one offered tool's call, when the agent making it
    may use the shell itself.

    By name, so `eval` and `task` are never bound: what they run inherits their context,
    and with it anything bound there. They get the rule instead, which each tool they
    reach binds a runner of its own from. A gated `execute` refuses too, since nothing can
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
        #: `None` is an unnarrowed grant, which holds the shell like any other built-in.
        #: The deployment's `ScratchRunLimit` is joined with `and`, so it can only take away.
        ruled = (tools is None or EXECUTE in tools) and EXECUTE not in gated
        self.rule = RunRule(
            backend,
            seconds,
            allowed=ruled and limit_allows,
            # Only where kingfisher's own rule allowed the run, so the cause it names is the
            # one that took the run away.
            refusal=limit_refusal if ruled and not limit_allows else None,
        )

    def _bound(self, request: Any) -> tuple[Any, ScratchRunner | RefusingRunner | None]:
        runtime = request.runtime
        context = None if runtime is None else runtime.context
        if (
            request.tool_call.get("name") not in self.names
            # A graph the caller built is driven with its own context, or none.
            or not isinstance(context, ToolContext)
        ):
            return request, None
        runner = self.rule.runner(context)
        if runner is None:
            return request, None
        bound = replace(runtime, context=replace(context, scratch=runner))
        return replace(request, runtime=bound), runner

    def _hands(self, request: Any) -> bool:
        return request.tool_call.get("name") in (INTERPRETER_TOOL, TASK_TOOL)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if self._hands(request):
            with handing(self.rule):
                return handler(request)
        bound, runner = self._bound(request)
        try:
            return handler(bound)
        finally:
            if runner is not None:
                runner.close()

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if self._hands(request):
            with handing(self.rule):
                return await handler(request)
        bound, runner = self._bound(request)
        try:
            return await handler(bound)
        finally:
            if runner is not None:
                runner.close()

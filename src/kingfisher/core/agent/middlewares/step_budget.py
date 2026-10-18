"""Telling an agent near its step limit how many model calls it has left, and holding a
subagent to the same budget."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.config import get_config

from kingfisher.core.agent.middlewares.replies import last_reply, replaced, text_of

#: The share of a run's steps after which the agent is told what is left.
WARN_AT = 0.8

#: Graph steps one model call costs on kingfisher's graphs: the call, the todo list's hook
#: after it, and the tools. A deployment's own hook beside the model costs one more.
STEPS_PER_CALL = 3


def calls_left() -> int | None:
    """About how many model calls this run has after this one, once it is past `WARN_AT`.

    Read off langgraph's own step counter and limit, so it follows whatever limit a
    deployment sets. A run stops past its first step plus the limit plus one; a fresh turn's
    first step is 0, and a resumed one's later, which only warns it early.
    """
    try:
        config = get_config()
    except RuntimeError:  # not inside a graph
        return None
    limit = config.get("recursion_limit")
    step = (config.get("metadata") or {}).get("langgraph_step")
    if not isinstance(limit, int) or not isinstance(step, int) or step < WARN_AT * limit:
        return None
    return (limit + 1 - step) // STEPS_PER_CALL


def warning(left: int) -> str:
    if left == 0:
        return (
            "This is your last model call in this turn. Give your answer now, from what you "
            "have, and say what is left undone."
        )
    calls = "call" if left == 1 else "calls"
    return (
        f"You have about {left} model {calls} left in this turn. Finish now: give your "
        "answer from what you have, and say what is left undone."
    )


def _made(request: Any) -> int:
    """The model calls an agent's conversation shows it has made."""
    # The stored conversation, not the request: deepagents' summariser, outside this
    # middleware, leaves the calls its summary stands for out of the request.
    return sum(isinstance(one, AIMessage) for one in request.state.get("messages", ()))


def _stopped(reply: AIMessage, calls: int) -> AIMessage:
    """A subagent's last allowed reply as text alone, saying it stopped there."""
    return text_of(
        reply,
        f"[Stopped: this subagent reached its limit of {calls} model calls, and the answer "
        "below is what it had then.]\n\n",
    )


class StepBudgetMiddleware(AgentMiddleware):
    """Tell an agent past 80% of its budget how many calls it has left, on every such call,
    and stop a subagent at the end of its own.

    A turn either finished well inside its 50 calls or ran to the limit and stopped
    mid-work; told, it can end on an answer. The note goes last and is never stored: the
    transcript stays clean, the prompt cache is untouched, and it never piles up.
    """

    def __init__(self, *, calls: int | None = None) -> None:
        super().__init__()
        #: A subagent's budget in model calls, enforced here, since deepagents runs one under
        #: 9,999 steps of its own. `None` for the agent, whose limit langgraph enforces.
        self.calls = calls

    def _left(self, request: Any) -> int | None:
        if self.calls is None:
            return calls_left()
        made = _made(request)
        return None if made < WARN_AT * self.calls else max(0, self.calls - made - 1)

    def _told(self, request: Any) -> Any:
        if (left := self._left(request)) is None:
            return request
        return request.override(messages=[*request.messages, HumanMessage(warning(left))])

    def _held(self, request: Any, response: Any) -> Any:
        """A subagent's last allowed reply, without the calls it may no longer make."""
        if self.calls is None or _made(request) + 1 < self.calls:
            return response
        reply = last_reply(response)
        if reply is None or not reply.tool_calls:
            return response
        return replaced(response, reply, _stopped(reply, self.calls))

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._held(request, handler(self._told(request)))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return self._held(request, await handler(self._told(request)))

"""A reply the model stopped at its output cap: the call it was cut inside goes unrun, and
an agent cut off three times in a row is stopped."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.core.session.runtime import cut_off, output_limit

#: Cut replies in a row after which an agent is stopped. Each spends the model's whole
#: output reservation, and a model retrying one oversized write would otherwise go on
#: to the step limit: measured, one session retried the same whole-file write a dozen
#: times.
GIVE_UP = 3

#: The content blocks that are calls, one per wire format: the Anthropic format's, the
#: Responses API's when it carries them, and langchain's own.
CALL_BLOCKS = frozenset({"tool_use", "function_call", "tool_call"})


def _is_call(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in CALL_BLOCKS


def cut_call(reply: Any) -> str | None:
    """The id of the call a cut reply stopped inside, or `None` where every call is whole.

    Only its last call can be: the cap stops a reply at its end. Where the content puts
    something after that call, the reply was cut writing that instead. Where the content
    carries no calls, nothing says what came last, and the last call is the one refused:
    refusing a whole call costs a retry, and running a cut one saves a shortened file.
    """
    calls = getattr(reply, "tool_calls", None)
    if not calls or not cut_off(reply):
        return None
    content = reply.content if isinstance(reply.content, list) else []
    if any(_is_call(block) for block in content) and not _is_call(content[-1]):
        return None
    return calls[-1]["id"]


def _cut_in_a_row(messages: Any) -> int:
    """How many of an agent's latest replies were cut, back to the last one that was not."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        if not (isinstance(message, AIMessage) and cut_off(message)):
            break
        count += 1
    return count


def _refusal(reply: AIMessage, call: Any) -> ToolMessage:
    return ToolMessage(
        content=(
            f"Not run: your reply reached {output_limit(reply)} while this call was still "
            "being written, so its arguments are incomplete and nothing was done. Make the "
            "change in smaller pieces — for a long file, write the first part, then add the "
            "rest in further calls."
        ),
        tool_call_id=call["id"],
        name=call["name"],
        status="error",
    )


def _stopped(reply: AIMessage, *, subagent: bool) -> AIMessage:
    """The reply an agent stops on: its text, and none of its calls.

    The stop reason stays, which is what tells the turn it ended cut off. A subagent's
    says so in its text too, since its text is all its caller is handed.
    """
    text = reply.text
    if subagent:
        text = (
            f"[Incomplete: this subagent's replies reached {output_limit(reply)} "
            f"{GIVE_UP} times in a row, and the answer below is cut off.]\n\n{text}"
        )
    return AIMessage(
        content=text,
        id=reply.id,
        name=reply.name,
        response_metadata=reply.response_metadata,
        usage_metadata=reply.usage_metadata,
    )


class CutReplies(AgentMiddleware):
    """Refuse the call a reply was cut inside, and stop an agent cut off too often.

    Inside the model call and the tool call, never beside them: a `before_model` or
    `after_model` hook is a graph step, and the recursion limit counts those.
    """

    def __init__(self, *, subagent: bool) -> None:
        super().__init__()
        self.subagent = subagent

    def _judged(self, request: Any, response: Any) -> Any:
        reply = next((one for one in reversed(response.result) if isinstance(one, AIMessage)), None)
        if reply is None or not cut_off(reply):
            return response
        if _cut_in_a_row(request.messages) + 1 < GIVE_UP:
            return response
        stopped = _stopped(reply, subagent=self.subagent)
        return ModelResponse(
            result=[stopped if one is reply else one for one in response.result],
            structured_response=response.structured_response,
        )

    def _refused(self, request: Any) -> ToolMessage | None:
        call = request.tool_call
        reply = next(
            (
                one
                for one in reversed(request.state.get("messages", ()))
                if isinstance(one, AIMessage) and any(c["id"] == call["id"] for c in one.tool_calls)
            ),
            None,
        )
        if reply is None or cut_call(reply) != call["id"]:
            return None
        return _refusal(reply, call)

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return self._judged(request, handler(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        return self._judged(request, await handler(request))

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        refused = self._refused(request)
        return refused if refused is not None else handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refused = self._refused(request)
        return refused if refused is not None else await handler(request)

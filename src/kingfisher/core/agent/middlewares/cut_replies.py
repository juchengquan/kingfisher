"""A reply the model stopped at its output cap: the call it was cut inside goes unrun, a cut
answer is asked for again, shorter, and an agent cut off too often in a row is stopped."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher.core.session.runtime import (
    call_block_id,
    cut_off,
    cut_off_mark,
    output_limit,
    with_call_args,
)

#: Cut replies in a row after which an agent is stopped. Each spends the model's whole
#: output reservation, and a model retrying one oversized write would otherwise go on
#: to the step limit: measured, one session retried the same whole-file write a dozen
#: times.
GIVE_UP = 3

#: The longest argument a refused cut call keeps as written. The path it aimed at stays;
#: the text it was cut writing goes, since nothing ever reads it again and every later
#: call would send it, and clearing never shortens a call that failed.
KEPT_WHOLE = 500

#: How the result of a call refused for being cut begins. Clearing reads it to leave the
#: refusal whole once a replayed transcript has dropped its error status.
NOT_RUN = "Not run: your reply reached "

#: On a cut reply kept in the conversation: the cut answers asked again in the same call
#: before it, which never reach the conversation and would otherwise drop out of the count.
ASKED_BEFORE = "kingfisher_cut_answers_before"


def cut_call(reply: Any) -> str | None:
    """The id of the call a cut reply stopped inside, or `None` where every call it runs
    is whole.

    The cap stops a reply at its end, so the content's last block is what was cut. Text
    there means every call before it is whole; a call too broken to parse there is in
    none of the calls that run, so it cuts none of them either. Where a provider puts no
    calls in the content, nothing says what came last, and the last call is the one
    refused: refusing a whole call costs a retry, and running a cut one saves a shortened
    file.
    """
    calls = getattr(reply, "tool_calls", None)
    if not calls or not cut_off(reply):
        return None
    content = reply.content if isinstance(reply.content, list) else []
    if not any(call_block_id(block) for block in content):
        return calls[-1]["id"]
    last = call_block_id(content[-1])
    return last if any(call["id"] == last for call in calls) else None


def _unrun(reply: AIMessage, call_id: str) -> AIMessage:
    """A reply with the arguments its cut call was writing replaced by their length."""
    args = next(call["args"] for call in reply.tool_calls if call["id"] == call_id)
    shortened = {
        name: f"[{len(value):,} characters, cut off; not run]"
        for name, value in args.items()
        if isinstance(value, str) and len(value) > KEPT_WHOLE
    }
    return with_call_args(reply, call_id, shortened) if shortened else reply


def _cut_in_a_row(messages: Any) -> int:
    """How many of an agent's latest replies were cut, back to the last one that was not."""
    count = 0
    for message in reversed(messages):
        if isinstance(message, ToolMessage):
            continue
        if not (isinstance(message, AIMessage) and cut_off(message)):
            break
        count += 1 + message.response_metadata.get(ASKED_BEFORE, 0)
    return count


def _reply(response: Any) -> AIMessage | None:
    return next((one for one in reversed(response.result) if isinstance(one, AIMessage)), None)


def _gives_up(first: Any, asked: int) -> bool:
    """Whether the reply in hand, being cut, is the one too many in a row."""
    return _cut_in_a_row(first.messages) + asked + 1 >= GIVE_UP


def _replaced(response: Any, reply: AIMessage, by: AIMessage) -> Any:
    return ModelResponse(
        result=[by if one is reply else one for one in response.result],
        structured_response=response.structured_response,
    )


def _refusal(reply: AIMessage, call: Any) -> ToolMessage:
    return ToolMessage(
        content=(
            f"{NOT_RUN}{output_limit(reply)} while this call was still "
            "being written, so its arguments are incomplete and nothing was done. Make the "
            "change in smaller pieces — for a long file, write the first part, then add the "
            "rest in further calls."
        ),
        tool_call_id=call["id"],
        name=call["name"],
        status="error",
    )


def _text_of(reply: AIMessage, heading: str = "") -> AIMessage:
    """A reply as text alone, with none of its calls: a call sent back with no result
    beside it is refused by the provider."""
    return AIMessage(
        content=heading + reply.text,
        id=reply.id,
        name=reply.name,
        response_metadata=reply.response_metadata,
        usage_metadata=reply.usage_metadata,
    )


def _stopped(reply: AIMessage, *, subagent: bool) -> AIMessage:
    """The reply an agent stops on: its text, and none of its calls.

    The stop reason stays, which is what tells the turn it ended cut off. A subagent's
    says so in its text too, since its text is all its caller is handed.
    """
    if not subagent:
        return _text_of(reply)
    return _text_of(
        reply,
        f"[Incomplete: this subagent's replies reached {output_limit(reply)} "
        f"{GIVE_UP} times in a row, and the answer below is cut off.]\n\n",
    )


class CutReplies(AgentMiddleware):
    """Refuse the call a reply was cut inside, ask again for a cut answer, and stop an
    agent cut off too often.

    Inside the model call and the tool call, never beside them: a `before_model` or
    `after_model` hook is a graph step, and the recursion limit counts those.
    """

    def __init__(self, *, subagent: bool, outputs: str) -> None:
        super().__init__()
        self.subagent = subagent
        self.outputs = outputs.rstrip("/")

    def _again(self, first: Any, response: Any, asked: int) -> Any | None:
        """The request asking again after a cut answer, or `None` where the reply stands.

        Built on the first request each time, so the model sees its latest attempt and
        not every one: each is up to a whole reservation long. Neither the cut answer nor
        the note asking again reaches the conversation, since clearing never touches the
        agent's own text and every later call would send it again. Its text has already
        streamed to whoever is watching, so the cut is reported there, where the answer
        starts over.
        """
        reply = _reply(response)
        if reply is None or not cut_off(reply) or reply.tool_calls or _gives_up(first, asked):
            return None
        write = getattr(first.runtime, "stream_writer", None)
        if write is not None:
            write(cut_off_mark(reply))
        # The file first, then its path, and no path without the file: an agent told only
        # to put the long part in a file and say where names a file it never wrote.
        note = (
            f"Your answer reached {output_limit(reply)} and was cut off. Answer again, "
            f"shorter. If it needs the length, first write the long part to a file under "
            f"{self.outputs}, then answer with a summary and the file's path. Name a file "
            "only after you have written it."
        )
        return first.override(messages=[*first.messages, _text_of(reply), HumanMessage(note)])

    def _judged(self, first: Any, response: Any, asked: int) -> Any:
        reply = _reply(response)
        if reply is None or not cut_off(reply):
            return response
        if _gives_up(first, asked):
            return _replaced(response, reply, _stopped(reply, subagent=self.subagent))
        kept = reply if (cut := cut_call(reply)) is None else _unrun(reply, cut)
        if asked:
            meta = {**kept.response_metadata, ASKED_BEFORE: asked}
            kept = kept.model_copy(update={"response_metadata": meta})
        return response if kept is reply else _replaced(response, reply, kept)

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
        response, asked = handler(request), 0
        while (again := self._again(request, response, asked)) is not None:
            response, asked = handler(again), asked + 1
        return self._judged(request, response, asked)

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        response, asked = await handler(request), 0
        while (again := self._again(request, response, asked)) is not None:
            response, asked = await handler(again), asked + 1
        return self._judged(request, response, asked)

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        refused = self._refused(request)
        return refused if refused is not None else handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        refused = self._refused(request)
        return refused if refused is not None else await handler(request)

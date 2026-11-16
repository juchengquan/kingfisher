"""Tell the model where the conversation a summary replaced is, and keep that file current
after a summary made mid-turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage

from kingfisher.core.agent.prompting import piece

if TYPE_CHECKING:
    from kingfisher.core.session.conversation import ConversationRecord

#: The `lc_source` deepagents marks its summary with, and the private state key it keeps
#: the summary under; spelled here, as `core.session.runtime` and `graph_state` spell them.
_SUMMARY_SOURCE = "summarization"
_SUMMARY_EVENT = "_summarization_event"


def pointer(route: str) -> str:
    """The line a summary opens with."""
    return f"{piece('nudges/summary_pointer', file=route)}\n\n"


def _is_summary(message: Any) -> bool:
    return isinstance(message, HumanMessage) and (
        message.additional_kwargs.get("lc_source") == _SUMMARY_SOURCE
    )


def fresh_cut(request: Any) -> int | None:
    """Where the summary this request opens on cut the state's messages, where deepagents
    made it on this very call; `None` for a request carrying an older one, or none.

    deepagents hands the summary on in the request before its event reaches state, so a
    new one is the summary leading the request that the state does not already hold, at
    the cut the request's length implies: the state's messages less the ones it kept.
    """
    messages = list(request.messages)
    if not messages or not _is_summary(messages[0]):
        return None
    held = list((request.state or {}).get("messages") or ())
    cut = len(held) - (len(messages) - 1)
    event = (request.state or {}).get(_SUMMARY_EVENT)
    if isinstance(event, Mapping):
        before = event.get("summary_message")
        if event.get("cutoff_index") == cut and getattr(before, "content", None) == (
            messages[0].content
        ):
            return None
    # Under a summary the turn was handed, which no event holds: a cut of one stands for
    # that summary alone, and is the request carrying it unchanged.
    if cut < 1 or (cut == 1 and held and _is_summary(held[0])):
        return None
    return cut


class SummaryPointerMiddleware(AgentMiddleware):
    """Open each summary the model is sent with where the conversation it replaced is, and
    rebuild that file when deepagents has just summarised.

    Inside deepagents' summariser, whose request is the one rewritten, and on the agent's
    stack alone: a subagent keeps its summary only. In the request and not in state, so
    the turn log keeps the summary as deepagents wrote it.
    """

    def __init__(self, record: ConversationRecord) -> None:
        super().__init__()
        self._record = record
        self._said = pointer(record.route)

    def _pointed(self, request: Any) -> Any:
        messages = list(request.messages)
        changed = False
        for at, message in enumerate(messages):
            if _is_summary(message) and isinstance(message.content, str) and not (
                message.content.startswith(self._said)
            ):
                messages[at] = message.model_copy(update={"content": self._said + message.content})
                changed = True
        return request.override(messages=messages) if changed else request

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if (cut := fresh_cut(request)) is not None:
            self._record.summarised(request.state["messages"], request.messages[0], cut)
        return handler(self._pointed(request))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if (cut := fresh_cut(request)) is not None:
            await self._record.asummarised(request.state["messages"], request.messages[0], cut)
        return await handler(self._pointed(request))

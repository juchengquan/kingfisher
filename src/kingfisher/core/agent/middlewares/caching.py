"""deepagents' prompt caching, with the conversation's breakpoint on what the conversation
keeps."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware

#: The slot this takes. deepagents merges by name, so a class named otherwise lands ahead
#: of the slot and runs beside deepagents' own.
CACHING = AnthropicPromptCachingMiddleware.__name__


def _marked(message: Any, mark: Any) -> Any | None:
    """`message` with its last block carrying `mark`, or `None` where it has none to carry.

    The API reads a string and the one text block it stands for as the same prefix, so a
    later call, sending this message unmarked, still reads the entry this one writes.
    """
    blocks = [message.content] if isinstance(message.content, str) else list(message.content)
    last = blocks[-1] if blocks else ""
    last = {"type": "text", "text": last} if isinstance(last, str) else last
    # Anthropic refuses a breakpoint on an empty text block.
    if last.get("type") == "text" and not last.get("text"):
        return None
    return message.model_copy(update={"content": [*blocks[:-1], {**last, "cache_control": mark}]})


def kept_last(request: Any) -> Any:
    """`request` with the conversation's breakpoint on the last message the conversation
    keeps, where something sent for this call alone follows it.

    The top-level `cache_control` marks the last block, and what a middleware outside the
    slot adds for one call -- the step budget's note, a cut answer asked again -- is
    never sent again: an entry ending there is written and never read. Moved rather than
    added: with memory on, Anthropic's four are spent.
    """
    mark = request.model_settings.get("cache_control")
    if mark is None:
        return request
    stored = {one.id for one in request.state.get("messages", ()) if one.id is not None}
    messages = list(request.messages)
    at = next((i for i in reversed(range(len(messages))) if messages[i].id in stored), None)
    if at is None or at == len(messages) - 1:
        return request
    marked = _marked(messages[at], mark)
    if marked is None:
        return request
    messages[at] = marked
    settings = {k: v for k, v in request.model_settings.items() if k != "cache_control"}
    return request.override(messages=messages, model_settings=settings)


class PromptCachingMiddleware(AnthropicPromptCachingMiddleware):
    """deepagents' prompt caching, marking the conversation where the next call reads it."""

    def __init__(self) -> None:
        # As deepagents builds its own.
        super().__init__(unsupported_model_behavior="ignore")

    @property
    def name(self) -> str:
        return CACHING

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        return super().wrap_model_call(request, lambda marked: handler(kept_last(marked)))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        async def kept(marked: Any) -> Any:
            return await handler(kept_last(marked))

        return await super().awrap_model_call(request, kept)

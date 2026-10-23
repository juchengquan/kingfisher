"""A deployment's text for one turn, sent after the part of the prompt that is cached."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.messages import SystemMessage

from .caching import PromptCachingMiddleware


class TurnInstructionsMiddleware(PromptCachingMiddleware):
    """deepagents' prompt caching, then this turn's text after the block it marks.

    In that slot the text follows every middleware that writes the prompt and precedes the
    memory block, and has no breakpoint of its own: with memory on, Anthropic's four are
    the tools, the static prompt, the memory block and the conversation.
    """

    def __init__(self, text: str, *, cached: bool = True) -> None:
        # Any other model is sent the text uncached.
        super().__init__()
        self.text = text
        #: False for a graph deepagents gives no caching, which this must not start.
        self.cached = cached

    def _told(self, request: Any) -> Any:
        system = request.system_message
        blocks = [] if system is None else list(system.content_blocks)
        # Separated as deepagents separates the sections it appends.
        text = f"\n\n{self.text}" if blocks else self.text
        return request.override(
            system_message=SystemMessage(content_blocks=[*blocks, {"type": "text", "text": text}])
        )

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if not self.cached:
            return handler(self._told(request))
        return super().wrap_model_call(request, lambda marked: handler(self._told(marked)))

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if not self.cached:
            return await handler(self._told(request))

        async def told(marked: Any) -> Any:
            return await handler(self._told(marked))

        return await super().awrap_model_call(request, told)

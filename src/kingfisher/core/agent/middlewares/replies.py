"""A model call's reply, swapped for one kingfisher's middleware made of it."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelResponse
from langchain_core.messages import AIMessage


def last_reply(response: Any) -> AIMessage | None:
    return next((one for one in reversed(response.result) if isinstance(one, AIMessage)), None)


def replaced(response: Any, reply: AIMessage, by: AIMessage) -> ModelResponse:
    return ModelResponse(
        result=[by if one is reply else one for one in response.result],
        structured_response=response.structured_response,
    )


def text_of(reply: AIMessage, heading: str = "") -> AIMessage:
    """A reply as text alone, with none of its calls: a call sent back with no result
    beside it is refused by the provider."""
    return AIMessage(
        content=heading + reply.text,
        id=reply.id,
        name=reply.name,
        response_metadata=reply.response_metadata,
        usage_metadata=reply.usage_metadata,
    )

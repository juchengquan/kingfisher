"""A reply's thinking, sent back only to the endpoint and model that produced it."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import TYPE_CHECKING, Any

from langchain.agents.middleware import AgentMiddleware, ModelResponse
from langchain_core.messages import AIMessage

if TYPE_CHECKING:
    from kingfisher.core.config import Models

#: Where a reply records who answered it. No chat class sends `response_metadata` on.
SERVED_BY = "kingfisher_served_by"

#: The blocks a signature binds: Anthropic's two, and the Responses API's.
THINKING = frozenset({"thinking", "redacted_thinking", "reasoning"})


def signers_of(models: Models) -> dict[str, dict[str, str]]:
    """Who answers each catalogue model, by the id it is sent as.

    The endpoint's name and its address both: a gateway moved under the same name is
    another signer, and so is a second account at the same address.
    """
    return {
        name: {"endpoint": profile.endpoint, "address": models.endpoints[profile.endpoint].base_url}
        for name, profile in models.models.items()
    }


def model_id(model: Any) -> str | None:
    """The id a chat model sends, as langchain traces its calls and the run log names
    them: each class keeps it under a name of its own."""
    named = model._get_ls_params().get("ls_model_name")
    return named if isinstance(named, str) else None


def _readable(reply: AIMessage, signer: Mapping[str, str | None]) -> bool:
    """Whether the model about to be called produced this reply, where it answers.

    A reply from before replies were recorded, or from a model built outside the
    catalogue, says only the model the server named.
    """
    served = reply.response_metadata.get(SERVED_BY)
    if served is None:
        named = reply.response_metadata.get("model_name")
        return signer["model"] is not None and named == signer["model"]
    return served == signer


def _thinking(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in THINKING


def kept_for(request: Any, signer: Mapping[str, str | None]) -> Any:
    """`request` with the thinking of every reply its model did not produce taken out,
    or `request` itself where there is none.

    Out of the request alone: the conversation keeps every block, for the model that can
    read it again.
    """
    messages = list(request.messages)
    for at, one in enumerate(messages):
        if (
            isinstance(one, AIMessage)
            and isinstance(one.content, list)
            and any(_thinking(block) for block in one.content)
            and not _readable(one, signer)
        ):
            kept = [block for block in one.content if not _thinking(block)]
            messages[at] = one.model_copy(update={"content": kept})
    if all(new is old for new, old in zip(messages, request.messages, strict=True)):
        return request
    return request.override(messages=messages)


def served(response: Any, signer: Mapping[str, str | None]) -> Any:
    """`response` with each reply in it recording who answered."""
    return ModelResponse(
        result=[
            one.model_copy(
                update={"response_metadata": {**one.response_metadata, SERVED_BY: dict(signer)}}
            )
            if isinstance(one, AIMessage)
            else one
            for one in response.result
        ],
        structured_response=response.structured_response,
    )


class SignedThinkingMiddleware(AgentMiddleware):
    """Send a reply's thinking back only to the endpoint and model that produced it.

    A signature is one vendor's, and langchain tells vendors apart by package: a reply
    from MiniMax, which comes through `ChatAnthropic`, would reach Claude with its
    thinking signed by MiniMax, a 400 every time.
    """

    def __init__(self, signers: Mapping[str, Mapping[str, str]]) -> None:
        super().__init__()
        self.signers = {name: dict(signer) for name, signer in signers.items()}

    def _signer(self, request: Any) -> dict[str, str | None]:
        named = model_id(request.model)
        # A model built outside the catalogue answers from nowhere kingfisher can name.
        where: Mapping[str, str | None] = self.signers.get(named or "") or {
            "endpoint": None,
            "address": None,
        }
        return {**where, "model": named}

    def wrap_model_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        signer = self._signer(request)
        return served(handler(kept_for(request, signer)), signer)

    async def awrap_model_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        signer = self._signer(request)
        return served(await handler(kept_for(request, signer)), signer)

"""Model construction."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.config import NO_EXTRA, ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.language_models import BaseChatModel

    from kingfisher.config import Endpoint, ModelProfile

@dataclass(frozen=True)
class Adapter:
    """One wire format: which class speaks it, and what it needs to be told.

    kingfisher targets **gateway-shaped** endpoints — one base URL, one key. A wire
    format without that shape (Bedrock wants a region and a credentials profile) is a
    new field on `Endpoint`, not a new row in this table.
    """

    chat_class: str
    extra: Mapping[str, Any] = NO_EXTRA

    def resolve(self) -> type[BaseChatModel]:
        """Import the chat class this row names."""
        module_name, _, class_name = self.chat_class.partition(":")
        return getattr(import_module(module_name), class_name)


#: The one place a wire format is described. `models.yaml` picks from these by
#: name through an endpoint's `api`; `build_model` constructs through
#: `chat_class`. Adding a row is a kingfisher release, and needs a matching
#: `LANDING_SITES` entry in `test_models.py`.
ADAPTERS: Mapping[str, Adapter] = {
    # The gateway path. MiniMax and anything else imitating Anthropic's wire
    # format lives here — see models.yaml.example, which recommends this style.
    "anthropic": Adapter("langchain_anthropic:ChatAnthropic"),
    # OpenAI proper, on the Responses API. This adapter is *not* a general
    # OpenAI-compatible client: `/v1/responses` is what we target, and virtually no
    # gateway imitating OpenAI implements it. Point a gateway at the anthropic adapter
    # instead. A future Chat-Completions row would be a new entry here, not a flag on
    # this one.
    "openai_responses": Adapter(
        "langchain_openai:ChatOpenAI",
        MappingProxyType({"use_responses_api": True}),
    ),
}


def build_model(profile: ModelProfile, endpoint: Endpoint) -> BaseChatModel:
    """Build a chat model from `profile`, pointed at `endpoint`."""
    try:
        adapter = ADAPTERS[endpoint.api]
    except KeyError:
        msg = (
            f"endpoint {profile.endpoint!r} names api {endpoint.api!r}, which kingfisher "
            f"cannot build; known: {tuple(ADAPTERS)}"
        )
        raise ConfigError(msg) from None

    return adapter.resolve()(
        model=profile.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        **profile.kwargs(),
        **profile.extra,
        **adapter.extra,
    )

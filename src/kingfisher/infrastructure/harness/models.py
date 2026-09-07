"""Model construction.

Every model in kingfisher is a pre-built instance, never a `"provider:model"` string
— for delegates as much as for the main agent. A string would be resolved by
deepagents through `init_chat_model`, which never sees this workspace's
configuration: the endpoint, the key, the token ceiling and the timeout would all be
silently dropped. A `ModelProfile` is a *record*; `build_model` is what turns it into
an instance, and `delegation.py` routes subagent models through it too.

A wire format is **data**, not a subclass. The classes differ in exactly two ways —
which one to construct, and any kwargs peculiar to it — while the values that come
from a profile use identical names on all of them. `ChatOpenAI`, `ChatAnthropic` and
`ChatGoogleGenerativeAI` all accept `model`, `base_url`, `api_key`, `max_tokens` and
`timeout` unchanged, because LangChain aliases them (Gemini's `max_output_tokens`
among them). So there is no shared behaviour for a base class to hold, and a
hierarchy would express a one-field difference as a type.

**This table is closed, and an endpoint table is not.** Endpoints are open data in
`models.yaml`; what stays here is the part that needs a Python class behind it.

The classes are imported at module scope rather than inside a builder. A deferred
import would spare nobody: deepagents depends on `langchain-anthropic` and
`langchain-google-genai` directly, so both are always installed, and `import
kingfisher` has already loaded the whole provider stack long before anything calls
`build_model`. Measured, not assumed. `pyproject.toml` declares `langchain-anthropic`
outright, because this module names it and a transitive dependency is not a promise.
"""

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
    # OpenAI-compatible client: `/v1/responses` is what we target, and
    # virtually no gateway imitating OpenAI implements it. Point a gateway at
    # the anthropic adapter instead. A future Chat-Completions row would be a
    # new entry here, not a flag on this one.
    #
    # Named for the endpoint it speaks to rather than the vendor, because
    # `openai` was the name and the name was the trap. Everything else in this
    # ecosystem means `/v1/chat/completions` by "OpenAI-compatible", so the
    # obvious `api: openai` on a gateway was accepted at load, built without
    # complaint, and failed at the first turn with an error from somebody
    # else's server. It is now a name nothing builds, and
    # `model_catalogue.load` refuses it while naming what it can build.
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

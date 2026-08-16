"""Model construction.

Every model in kingfisher is a pre-built instance, never a `"provider:model"`
string — for delegates as much as for the main agent. A string would be
resolved by deepagents through `init_chat_model`, which never sees this
workspace's `Config`: the endpoint, the key, the token ceiling and the timeout
would all be silently dropped. `Config.model` is a *name*; `build_model` is
what turns it into an instance, and `delegation.py` routes subagent models
through it too.

A provider is **data**, not a subclass. The classes differ in exactly two ways —
which one to construct, and any kwargs peculiar to it — while the five values
that come from `Config` use identical names on all of them. `ChatOpenAI`,
`ChatAnthropic` and `ChatGoogleGenerativeAI` all accept `model`, `base_url`,
`api_key`, `max_tokens` and `timeout` unchanged, because LangChain aliases them
(Gemini's `max_output_tokens` among them). So there is no shared behaviour for a
base class to hold, and a hierarchy would express a one-field difference as a
type.

The classes are imported at module scope rather than inside a builder. The
deferred import was meant to spare a deployment using one style from importing
the other's SDK, or needing it installed at all — and it achieved neither.
deepagents depends on `langchain-anthropic` and `langchain-google-genai`
directly, so both are always installed, and `import kingfisher` has already
loaded the whole provider stack long before anything calls `build_model`.
Measured, not assumed. `pyproject.toml` declares `langchain-anthropic` outright
now, because this module names it and a transitive dependency is not a promise.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.config import Config, ConfigError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.language_models import BaseChatModel

_NO_EXTRA: Mapping[str, Any] = MappingProxyType({})


@dataclass(frozen=True)
class Provider:
    """How to reach one endpoint style: where the credentials are, what to build.

    kingfisher targets **gateway-shaped** endpoints — one base URL, one key.
    A provider without that shape (Bedrock wants a region and a credentials
    profile) is a `Config` change, not a new row in this table.

    `extra` is additive only. It carries kwargs the endpoint itself requires,
    and cannot name one of the five that come from `Config` — Python raises on
    the duplicate, which is the intended outcome: a provider row must not
    quietly overrule a value the user configured.

    `chat_class` is a `"module:Name"` path rather than the class, resolved
    when a model is actually built. Holding the classes meant importing every
    provider's SDK to describe *one* endpoint style, and a deployment can only
    use one -- `api_style` picks it. It buys nothing today, because deepagents
    imports `langchain_openai` itself wherever it is installed; what it buys is
    the option of not installing it, which naming the class made impossible.
    """

    url_env: str
    key_env: str
    chat_class: str
    extra: Mapping[str, Any] = _NO_EXTRA

    def resolve(self) -> type[BaseChatModel]:
        """Import the chat class this row names."""
        module_name, _, class_name = self.chat_class.partition(":")
        return getattr(import_module(module_name), class_name)


#: The one place a provider is described. `from_env` reads the credential
#: variable names from here; `build_model` constructs through `chat_class`.
PROVIDERS: Mapping[str, Provider] = {
    # The gateway path. MiniMax and anything else imitating Anthropic's wire
    # format lives here — see .env.example, which recommends this style.
    "anthropic": Provider(
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "langchain_anthropic:ChatAnthropic",
    ),
    # OpenAI proper, on the Responses API. This style is *not* a general
    # OpenAI-compatible client: `/v1/responses` is what we target, and
    # virtually no gateway imitating OpenAI implements it. Point a gateway at
    # the anthropic style instead. A future Chat-Completions row would be a new
    # entry here, not a flag on this one.
    "openai": Provider(
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "langchain_openai:ChatOpenAI",
        MappingProxyType({"use_responses_api": True}),
    ),
}


def build_model(cfg: Config) -> BaseChatModel:
    """Build a chat model from `cfg`, pointed at the endpoint `cfg` names.

    No role parameter. A delegate that runs somewhere else says so in its own
    definition, and `delegation.as_subagent` builds it by replacing the three
    endpoint fields here -- so "which model, where" is one question with one
    answer, read off whichever `Config` is handed in.
    """
    try:
        provider = PROVIDERS[cfg.api_style]
    except KeyError:
        msg = f"no model builder for api_style {cfg.api_style!r}; known: {tuple(PROVIDERS)}"
        raise ConfigError(msg) from None

    return provider.resolve()(
        model=cfg.model,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
        **provider.extra,
    )

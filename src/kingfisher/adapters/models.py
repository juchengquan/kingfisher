"""Model construction.

Every model in kingfisher is a pre-built instance, never a `"provider:model"`
string — for every role, not just the main one.

deepagents applies its built-in provider profiles only when it resolves a
*string*; `resolve_model` returns instances unchanged. Its openai profile forces
`use_responses_api=True`, sending requests to `/v1/responses`, which virtually no
OpenAI-compatible gateway implements. Subagent models
(`deepagents.middleware.subagents`) and the summarizer
(`deepagents.middleware.summarization`) go through the same resolution, so a
string handed over anywhere reopens the same hole — in a path nobody is
watching. `Config.model_for()` returns a string; `build_model` is what turns it
into an instance.

deepagents 0.7.6 *does* expose `register_provider_profile` to override the built-in
(its own docstring uses `use_responses_api=False` as the example), so this module
is no longer the only way past the profile. It constructs explicitly anyway: the
kwargs stay typed by `Config`, and there is no process-global registry whose
contents depend on import order.

Providers are a table rather than a class hierarchy because what differs between
them is data, not behaviour. Two of the likeliest additions — a vLLM or an Ollama
endpoint — are `ChatOpenAI` with a different base URL, which no subclass could
express; and the constructor surfaces already disagree where it matters
(`ChatGoogleGenerativeAI` takes `max_output_tokens`, not `max_tokens`), so a
shared base method would be overridden by every one of its subclasses.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from kingfisher.domain.config import Config, ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from langchain_core.language_models import BaseChatModel

    #: Resolve the role first; a builder is only ever handed a concrete model id.
    Builder = Callable[[Config, str], BaseChatModel]


def _anthropic(cfg: Config, model_id: str) -> BaseChatModel:
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=model_id,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
    )


def _openai(cfg: Config, model_id: str) -> BaseChatModel:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=model_id,
        base_url=cfg.base_url,
        api_key=cfg.api_key,
        max_tokens=cfg.max_tokens,
        timeout=cfg.timeout_s,
        # Belt and braces: instances bypass deepagents' provider profile
        # anyway, but this is the value that must never become True. Verified
        # against MiniMax; pinned by tests/test_models.py.
        use_responses_api=False,
    )


@dataclass(frozen=True)
class Provider:
    """How to reach one endpoint style: where the credentials are, how to build.

    kingfisher targets **gateway-shaped** endpoints — one base URL, one key.
    That covers the Anthropic- and OpenAI-compatible surfaces of a hosted
    gateway and every local server that imitates one. A provider without that
    shape (Bedrock wants a region and a credentials profile) is a `Config`
    change, not a new row in this table.
    """

    url_env: str
    key_env: str
    build: Builder


#: The one place a provider is described. `from_env` reads the credential
#: variable names from here; `build_model` dispatches through `build`.
PROVIDERS: Mapping[str, Provider] = {
    "anthropic": Provider("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", _anthropic),
    "openai": Provider("OPENAI_BASE_URL", "OPENAI_API_KEY", _openai),
}


def build_model(cfg: Config, role: str = "main") -> BaseChatModel:
    """Build the chat model for `role`, pointed at the configured endpoint."""
    try:
        provider = PROVIDERS[cfg.api_style]
    except KeyError:
        msg = f"no model builder for api_style {cfg.api_style!r}; known: {tuple(PROVIDERS)}"
        raise ConfigError(msg) from None
    return provider.build(cfg, cfg.model_for(role))

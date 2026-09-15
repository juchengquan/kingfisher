"""Model construction."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from kingfisher.config import NO_EXTRA, ConfigError
from kingfisher.domain.capabilities import ALL, Selection, refuse_ungranted_endpoint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.language_models import BaseChatModel

    from kingfisher.config import Config, Endpoint, ModelProfile


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
#:
#: A row names its package as a string and `resolve` imports it by name, so neither
#: provider is an import anywhere in `src/` and neither belongs in `THIRD_PARTY` --
#: which reads imports, and now refuses a grant nothing spends. Adding one back to
#: that table on the strength of a row here fails
#: `test_no_area_is_granted_a_dependency_it_does_not_import`.
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


def model_named(
    written: str, cfg: Config, *, endpoints: Selection = ALL, subject: str
) -> BaseChatModel:
    """The model a written name means, on an endpoint this request may reach.

    Shared with `model_object`, which is this with a definition in front of it rather
    than a name. A second copy of these three steps that forgot the middle one would
    send a run to an endpoint the caller refused and say nothing.
    """
    # A lookup, rather than a `replace` of four `Config` fields built from the copy.
    # A param nobody remembered to add to that copy was silently the deployment's
    # own, so a per-model `max_tokens` was dropped without a word; a profile carries
    # every param and there is nothing here to forget.
    try:
        profile, endpoint = cfg.models.resolve(written)
    except ConfigError as exc:
        # `resolve` knows the model and the catalogue; only the caller knows *who
        # asked*. Without that, the reader is told `gpt-5` cannot be run and left to
        # grep the catalogue for whoever wanted it.
        msg = f"{subject}: {exc}"
        raise ConfigError(msg) from exc
    refuse_ungranted_endpoint(profile.endpoint, granted=endpoints, subject=subject)
    return build_model(profile, endpoint)

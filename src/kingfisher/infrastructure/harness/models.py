"""Model construction."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from kingfisher.config import Adapter, ConfigError, Landing
from kingfisher.domain.capabilities import ALL, Selection, refuse_ungranted_endpoint

if TYPE_CHECKING:
    from collections.abc import Mapping

    from langchain_core.language_models import BaseChatModel

    from kingfisher.config import Config, Endpoint, ModelProfile


#: The wire formats kingfisher ships. `models.yaml` picks from these by name
#: through an endpoint's `api`, together with any a deployment adds through
#: `KINGFISHER_ADAPTERS_FACTORY`.
#:
#: A row names its package as a string and `resolve` imports it by name, so neither
#: provider is an import anywhere in `src/` and neither belongs in `THIRD_PARTY` --
#: which reads imports, and now refuses a grant nothing spends. Adding one back to
#: that table on the strength of a row here fails
#: `test_no_area_is_granted_a_dependency_it_does_not_import`.
ADAPTERS: Mapping[str, Adapter] = {
    # The gateway path. MiniMax and anything else imitating Anthropic's wire
    # format lives here — see models.yaml.example, which recommends this style.
    "anthropic": Adapter(
        "langchain_anthropic:ChatAnthropic",
        Landing(
            model="model",
            base_url="anthropic_api_url",
            api_key="anthropic_api_key",
            max_tokens="max_tokens",
            timeout="default_request_timeout",
        ),
    ),
    # OpenAI proper, on the Responses API. This adapter is *not* a general
    # OpenAI-compatible client: `/v1/responses` is what we target, and virtually no
    # gateway imitating OpenAI implements it. Point a gateway at the anthropic adapter
    # instead. A future Chat-Completions row would be a new entry here, not a flag on
    # this one.
    "openai_responses": Adapter(
        "langchain_openai:ChatOpenAI",
        Landing(
            model="model_name",
            base_url="openai_api_base",
            api_key="openai_api_key",
            max_tokens="max_tokens",
            timeout="request_timeout",
        ),
        MappingProxyType({"use_responses_api": True}),
    ),
}


def build_model(profile: ModelProfile, endpoint: Endpoint) -> BaseChatModel:
    """Build a chat model from `profile`, pointed at `endpoint`."""
    adapter = endpoint.adapter
    model = adapter.resolve()(
        model=profile.model,
        base_url=endpoint.base_url,
        api_key=endpoint.api_key,
        **profile.kwargs(),
        **profile.extra,
        **adapter.extra,
    )
    _refuse_what_did_not_land(model, adapter, profile, endpoint)
    return model


def _refuse_what_did_not_land(
    model: BaseChatModel, adapter: Adapter, profile: ModelProfile, endpoint: Endpoint
) -> None:
    """Refuse a model that did not keep a value it was built with.

    A chat class that does not know a keyword moves it to `model_kwargs` with a warning
    and builds anyway. For `base_url` that leaves the URL unset, and the request -- with
    this endpoint's key -- goes to the vendor's default host instead of the gateway.
    """
    sent = {
        "model": profile.model,
        "base_url": endpoint.base_url,
        "api_key": endpoint.api_key,
        "max_tokens": profile.max_tokens,
        "timeout": profile.timeout_s,
    }
    for value, expected in sent.items():
        site = getattr(adapter.lands, value)
        landed = getattr(model, site, None)
        if hasattr(landed, "get_secret_value"):
            landed = landed.get_secret_value()
        if landed != expected:
            # The key is named and never quoted: this message goes to a log.
            found = "something else" if value == "api_key" else repr(landed)
            msg = (
                f"endpoint {profile.endpoint!r} names api {endpoint.api!r}, whose "
                f"{adapter.chat_class} did not keep {value!r} at {site!r} (found "
                f"{found}); refusing to build a model that would send its requests "
                "somewhere nobody chose"
            )
            raise ConfigError(msg)


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

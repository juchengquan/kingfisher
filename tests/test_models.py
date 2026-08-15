"""Model construction.

Constructing a chat model touches no network, so these are ordinary unit tests
even though they build the real classes. The values they pin are the ones with
documented failure modes — everything else here is LangChain's business.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.adapters.models import PROVIDERS, build_model
from kingfisher.domain.config import API_STYLES, ConfigError


def test_every_api_style_has_a_provider():
    """A style `Config` accepts but nothing can build fails at first run.

    `API_STYLES` is derived from the `ApiStyle` literal and `PROVIDERS` is
    hand-written; this is the only thing holding the two together.
    """
    assert set(PROVIDERS) == set(API_STYLES)


def test_openai_never_uses_the_responses_api(cfg):
    """The one value that must never flip.

    deepagents' built-in openai profile sets `use_responses_api=True`, which
    sends requests to `/v1/responses` — unimplemented on the gateway. Passing
    an instance sidesteps the profile, but the kwarg is still explicit here so
    a rewrite of `_openai` cannot quietly drop it.
    """
    model = build_model(replace(cfg, api_style="openai"))

    assert model.use_responses_api is False


def test_the_main_model_is_used_by_default(cfg):
    assert build_model(cfg).model == cfg.model


def test_per_role_models_reach_the_constructor(cfg):
    """`model_for` is only load-bearing if `build_model` honours it.

    Without this, per-role cost routing could be configured and silently
    ignored — the config would look right and every role would run the
    expensive model.
    """
    routed = replace(cfg, role_models={"subagent": "cheap-model"})

    assert build_model(routed, role="subagent").model == "cheap-model"
    assert build_model(routed, role="main").model == cfg.model
    assert build_model(routed, role="summarizer").model == cfg.model


#: Where each `Config` value lands, per provider. The providers do not agree on
#: the names — which is exactly why this is a table and not a loop over one set
#: of attributes, and why adding a provider means adding a row here too.
LANDING_SITES = {
    "anthropic": {
        "model": "model",
        "base_url": "anthropic_api_url",
        "api_key": "anthropic_api_key",
        "max_tokens": "max_tokens",
        "timeout_s": "default_request_timeout",
    },
    "openai": {
        "model": "model_name",
        "base_url": "openai_api_base",
        "api_key": "openai_api_key",
        "max_tokens": "max_tokens",
        "timeout_s": "request_timeout",
    },
}


@pytest.mark.parametrize("style", API_STYLES)
def test_every_config_value_reaches_the_client(cfg, style):
    """A dropped kwarg is invisible until the endpoint rejects the request."""
    config = replace(cfg, api_style=style, max_tokens=321, timeout_s=45)
    model = build_model(config)
    sites = LANDING_SITES[style]

    assert getattr(model, sites["model"]) == config.model
    assert getattr(model, sites["base_url"]) == config.base_url
    assert getattr(model, sites["api_key"]).get_secret_value() == config.api_key
    assert getattr(model, sites["max_tokens"]) == 321
    assert getattr(model, sites["timeout_s"]) == 45


def test_every_provider_has_landing_sites():
    """Guards the table above, which is the fifth per-provider edit site."""
    assert set(LANDING_SITES) == set(PROVIDERS)


def test_an_unbuildable_style_fails_with_a_readable_error(cfg):
    """Reachable only if `ApiStyle` gains a member with no `Provider`.

    `test_every_api_style_has_a_provider` catches that in CI; this makes the
    runtime failure legible rather than a bare `KeyError` from a dict lookup.
    """
    with pytest.raises(ConfigError, match="no model builder for api_style 'gemini'"):
        build_model(replace(cfg, api_style="gemini"))

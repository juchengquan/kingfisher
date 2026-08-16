"""Model construction.

Constructing a chat model touches no network, so these are ordinary unit tests
even though they build the real classes. The values they pin are the ones with
documented failure modes — everything else here is LangChain's business.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.config import API_STYLES, ConfigError
from kingfisher.infrastructure.models import PROVIDERS, Provider, build_model


def test_every_api_style_has_a_provider():
    """A style `Config` accepts but nothing can build fails at first run.

    `API_STYLES` is derived from the `ApiStyle` literal and `PROVIDERS` is
    hand-written; this is the only thing holding the two together.
    """
    assert set(PROVIDERS) == set(API_STYLES)


def test_openai_uses_the_responses_api(cfg):
    """The openai style targets `/v1/responses`, and only that.

    `ChatOpenAI` defaults `use_responses_api` to `None`, which lets LangChain
    pick a surface per request — so the endpoint kingfisher talks to would vary
    with the features a given call happens to use. Pinning it keeps the surface
    the same on every call, which is what the run log claims when it records
    the api_style.
    """
    model = build_model(replace(cfg, api_style="openai"))

    assert model.use_responses_api is True


def test_a_provider_row_cannot_overrule_a_configured_value(cfg, monkeypatch):
    """`extra` is additive: it may not name one of the five Config kwargs.

    A row that did would silently discard a value the user set. The duplicate
    keyword raises instead, so the mistake surfaces at construction rather than
    as a request that quietly ignores `KINGFISHER_MAX_TOKENS`.
    """
    colliding = Provider(
        "OPENAI_BASE_URL", "OPENAI_API_KEY", "langchain_openai:ChatOpenAI", {"max_tokens": 1}
    )
    monkeypatch.setitem(PROVIDERS, "openai", colliding)

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(replace(cfg, api_style="openai"))


def test_the_main_model_is_used_by_default(cfg):
    assert build_model(cfg).model == cfg.model


def test_the_model_comes_from_the_config_it_is_handed(cfg):
    """How a delegate runs elsewhere, now that there is no role parameter:
    `delegation.as_subagent` swaps the endpoint fields and builds from that.

    Without this, `build_model` could quietly read some other source and every
    delegate would run the deployment's own model while its definition said
    otherwise.
    """
    elsewhere = replace(cfg, model="cheap-model")

    assert build_model(elsewhere).model == "cheap-model"
    assert build_model(cfg).model == cfg.model


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


def test_describing_a_provider_does_not_import_its_sdk():
    """A deployment uses one `api_style`, so naming the classes meant importing
    every provider's SDK to describe an endpoint none of them would build.

    This buys nothing while deepagents imports `langchain_openai` itself
    wherever it is installed. What it buys is the option of not installing it,
    which holding the class made impossible.
    """
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import kingfisher.infrastructure.models as m;"
        "print('langchain_openai' in sys.modules, 'langchain_anthropic' in sys.modules)"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False False"


def test_a_row_naming_an_absent_class_fails_where_it_is_built():
    """Deferring the import defers the error too, so it has to still be a clear
    one -- a typo'd row must not surface as a mysterious attribute failure."""
    from dataclasses import replace as dc_replace

    row = Provider("OPENAI_BASE_URL", "OPENAI_API_KEY", "langchain_openai:NoSuchModel")
    with pytest.raises(AttributeError, match="NoSuchModel"):
        row.resolve()

    missing_module = dc_replace(row, chat_class="no_such_package:Thing")
    with pytest.raises(ModuleNotFoundError, match="no_such_package"):
        missing_module.resolve()

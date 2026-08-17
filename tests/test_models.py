"""Model construction.

Constructing a chat model touches no network, so these are ordinary unit tests
even though they build the real classes. The values they pin are the ones with
documented failure modes — everything else here is LangChain's business.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.config import ConfigError, Endpoint, ModelProfile
from kingfisher.infrastructure.harness.models import ADAPTERS, Adapter, build_model

OPENAI = Endpoint("openai", "https://api.openai.com/v1", "sk-not-real")


def test_openai_uses_the_responses_api(cfg):
    """The openai adapter targets `/v1/responses`, and only that.

    `ChatOpenAI` defaults `use_responses_api` to `None`, which lets LangChain
    pick a surface per request — so the endpoint kingfisher talks to would vary
    with the features a given call happens to use. Pinning it keeps the surface
    the same on every call, which is what the run log claims when it records
    the endpoint.
    """
    model = build_model(cfg.models.models["fake-model"], OPENAI)

    assert model.use_responses_api is True


def test_an_adapter_row_cannot_overrule_a_configured_value(cfg, monkeypatch):
    """`extra` is additive: it may not name a value the profile carries.

    A row that did would silently discard a value the operator set. The
    duplicate keyword raises instead, so the mistake surfaces at construction
    rather than as a deployment that quietly ignores its own `max_tokens`.
    """
    colliding = Adapter("langchain_openai:ChatOpenAI", {"max_tokens": 1})
    monkeypatch.setitem(ADAPTERS, "openai", colliding)

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(cfg.models.models["fake-model"], OPENAI)


def test_a_model_entrys_extra_cannot_overrule_its_own_params(cfg):
    """The same rule from the other side: `extra` in `models.yaml`.

    `model_catalogue` refuses this at parse time so the error can name the
    file. This is the backstop for a `ModelProfile` built any other way — the
    duplicate keyword still raises rather than one value silently winning.
    """
    profile = replace(cfg.models.models["fake-model"], extra={"max_tokens": 1})

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(profile, OPENAI)


def test_the_model_comes_from_the_profile_it_is_handed(cfg):
    """How a delegate runs elsewhere, now that there is no `Config` parameter:
    `delegation.as_subagent` looks the profile up and hands it over.

    Without this, `build_model` could quietly read some other source and every
    delegate would run the deployment's own model while its definition said
    otherwise.
    """
    profile, endpoint = cfg.models.resolve("cheap-model")

    assert build_model(profile, endpoint).model == "cheap-model"
    assert build_model(*cfg.models.resolve()).model == cfg.models.default


def test_an_unset_param_is_not_passed_at_all(cfg):
    """Omitted means absent, not "passed as a default we chose".

    `temperature` is why the distinction is worth a test. Sending `0.0` because
    nobody wrote a number would silently change what every existing deployment
    does, from the one file whose purpose is to hand that decision over.
    """
    unset = cfg.models.models["fake-model"]
    assert unset.temperature is None
    assert "temperature" not in unset.kwargs()

    chosen = replace(unset, temperature=0.5)
    assert chosen.kwargs()["temperature"] == 0.5


#: Where each value lands, per adapter. The classes do not agree on the names —
#: which is exactly why this is a table and not a loop over one set of
#: attributes, and why adding an adapter means adding a row here too.
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


@pytest.mark.parametrize("api", sorted(ADAPTERS))
def test_every_value_reaches_the_client(api):
    """A dropped kwarg is invisible until the endpoint rejects the request."""
    endpoint = Endpoint(api, "https://example.invalid/v1", "sk-not-real")
    profile = ModelProfile("a-model", "somewhere", max_tokens=321, timeout_s=45)
    model = build_model(profile, endpoint)
    sites = LANDING_SITES[api]

    assert getattr(model, sites["model"]) == profile.model
    assert getattr(model, sites["base_url"]) == endpoint.base_url
    assert getattr(model, sites["api_key"]).get_secret_value() == endpoint.api_key
    assert getattr(model, sites["max_tokens"]) == 321
    assert getattr(model, sites["timeout_s"]) == 45


def test_every_adapter_has_landing_sites():
    """Guards the table above, which is the per-adapter edit site."""
    assert set(LANDING_SITES) == set(ADAPTERS)


def test_an_unbuildable_api_fails_with_a_readable_error(cfg):
    """An endpoint naming a wire format kingfisher does not ship.

    `model_catalogue` could refuse this at load, and the message would be
    better placed there. It is checked here too because `ADAPTERS` is the only
    thing that actually knows, and a bare `KeyError` from a dict lookup is not
    something anyone can act on.
    """
    endpoint = Endpoint("gemini", "https://example.invalid", "sk-not-real")

    with pytest.raises(ConfigError, match="names api 'gemini'"):
        build_model(cfg.models.models["fake-model"], endpoint)


def test_describing_an_adapter_does_not_import_its_sdk():
    """A deployment uses the wire formats its endpoints name, so naming the
    classes meant importing every provider's SDK to describe endpoints none of
    them would build.

    This buys nothing while deepagents imports `langchain_openai` itself
    wherever it is installed. What it buys is the option of not installing it,
    which holding the class made impossible.
    """
    import subprocess
    import sys

    probe = (
        "import sys;"
        "import kingfisher.infrastructure.harness.models as m;"
        "print('langchain_openai' in sys.modules, 'langchain_anthropic' in sys.modules)"
    )
    out = subprocess.run(  # noqa: S603 -- our own interpreter, our own literal
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False False"


def test_a_row_naming_an_absent_class_fails_where_it_is_built():
    """Deferring the import defers the error too, so it has to still be a clear
    one -- a typo'd row must not surface as a mysterious attribute failure."""
    row = Adapter("langchain_openai:NoSuchModel")
    with pytest.raises(AttributeError, match="NoSuchModel"):
        row.resolve()

    missing_module = replace(row, chat_class="no_such_package:Thing")
    with pytest.raises(ModuleNotFoundError, match="no_such_package"):
        missing_module.resolve()

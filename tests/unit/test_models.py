"""Model construction."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

import pytest

from kingfisher.config import ConfigError, Endpoint, ModelProfile
from kingfisher.infrastructure.harness.models import ADAPTERS, Adapter, build_model

if TYPE_CHECKING:
    from typing import Any

OPENAI = Endpoint("openai_responses", "https://api.openai.com/v1", "sk-not-real")


def test_openai_uses_the_responses_api(cfg):
    """The openai adapter targets `/v1/responses`, and only that."""
    model = build_model(cfg.models.models["fake-model"], OPENAI)

    assert model.use_responses_api is True


def test_an_adapter_row_cannot_overrule_a_configured_value(cfg, monkeypatch):
    """`extra` is additive: it may not name a value the profile carries."""
    shipped = ADAPTERS["openai_responses"]
    colliding = replace(shipped, extra={"max_tokens": 1})
    monkeypatch.setitem(ADAPTERS, "openai_responses", colliding)

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(cfg.models.models["fake-model"], OPENAI)


def test_a_model_entrys_extra_cannot_overrule_its_own_params(cfg):
    """The same rule from the other side: `extra` in `models.yaml`.

    `model_catalogue` refuses this at parse time so the error can name the file; this is
    the backstop for a `ModelProfile` built any other way.
    """
    profile = replace(cfg.models.models["fake-model"], extra={"max_tokens": 1})

    with pytest.raises(TypeError, match="multiple values for keyword argument"):
        build_model(profile, OPENAI)


def test_the_model_comes_from_the_profile_it_is_handed(cfg):
    """How a delegate runs elsewhere, now that there is no `Config` parameter:
    `delegation.as_subagent` looks the profile up and hands it over.
    """
    profile, endpoint = cfg.models.resolve("cheap-model")

    assert build_model(profile, endpoint).model == "cheap-model"
    assert build_model(*cfg.models.resolve()).model == cfg.models.default


#: The params `kwargs` passes only when they are set. Listed rather than written
#: into the test below, because the test below was written for `temperature` and
#: `top_p` sat one line under it in `kwargs` for as long, unnamed by anything.
OPTIONAL = ("temperature", "top_p")


def test_an_unset_param_is_not_passed_at_all(cfg):
    """Omitted means absent, not "passed as a default we chose"."""
    for field in OPTIONAL:
        unset = cfg.models.models["fake-model"]
        assert getattr(unset, field) is None
        assert field not in unset.kwargs()

        chosen = replace(unset, **{field: 0.5})
        assert chosen.kwargs()[field] == 0.5


def test_every_param_kwargs_omits_is_named_above():
    """A third optional param would otherwise arrive with no case, the way the
    second one did.
    """
    source = inspect.getsource(ModelProfile.kwargs)
    guarded = tuple(re.findall(r"if self\.(\w+) is not None:", source))

    assert guarded == OPTIONAL, (
        f"`kwargs` omits {guarded} when unset and this file names {OPTIONAL} — "
        "add the new one to OPTIONAL, which is what gives it a case"
    )


def test_every_value_reaches_the_client():
    """A shipped row whose `lands` names the wrong attribute.

    Read back here as well as inside `build_model`, so deleting that check leaves
    something red besides the test written for it.
    """
    for api, adapter in sorted(ADAPTERS.items()):
        endpoint = Endpoint(api, "https://example.invalid/v1", "sk-not-real")
        profile = ModelProfile("a-model", "somewhere", max_tokens=321, timeout_s=45)
        model = build_model(profile, endpoint)
        sites = adapter.lands

        assert getattr(model, sites.model) == profile.model
        assert getattr(model, sites.base_url) == endpoint.base_url
        assert getattr(model, sites.api_key).get_secret_value() == endpoint.api_key
        assert getattr(model, sites.max_tokens) == 321
        assert getattr(model, sites.timeout) == 45


@dataclass(frozen=True)
class _Renames(Adapter):
    """A row whose class calls one keyword something else, as most vendors' own do."""

    keyword: str = "base_url"

    def resolve(self) -> Any:
        chat_openai = super().resolve()

        def build(**kwargs: Any) -> Any:
            kwargs[f"gateway_{self.keyword}"] = kwargs.pop(self.keyword)
            return chat_openai(**kwargs)

        return build


def test_a_url_the_class_did_not_keep_is_refused(cfg):
    """A class that does not know `base_url` builds anyway, with the URL unset, and
    sends the endpoint's key to the vendor's default host.

    Driven through the real `ChatOpenAI` rather than a stub model, because what makes
    this dangerous is that class's own habit of moving an unknown keyword into
    `model_kwargs` and carrying on.
    """
    shipped = ADAPTERS["openai_responses"]
    row = _Renames(shipped.chat_class, shipped.lands, shipped.extra)

    with (
        pytest.warns(UserWarning, match="gateway_base_url"),
        pytest.raises(ConfigError, match=r"did not keep 'base_url' at 'openai_api_base'"),
    ):
        _build_with(row, cfg.models.models["fake-model"])


def test_a_refusal_never_quotes_the_key_it_found(cfg, monkeypatch):
    """A class that loses the endpoint's key falls back to one from the environment,
    and the refusal goes to a log.
    """
    ambient = "sk-ambient-not-real"
    monkeypatch.setenv("OPENAI_API_KEY", ambient)
    shipped = ADAPTERS["openai_responses"]
    row = _Renames(shipped.chat_class, shipped.lands, shipped.extra, keyword="api_key")

    with (
        pytest.warns(UserWarning, match="gateway_api_key"),
        pytest.raises(ConfigError, match="did not keep 'api_key'") as refused,
    ):
        _build_with(row, cfg.models.models["fake-model"])
    assert ambient not in str(refused.value)


def _build_with(row: Adapter, profile: ModelProfile) -> Any:
    """`build_model` on `OPENAI`, with `row` standing in for its shipped adapter."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(ADAPTERS, "openai_responses", row)
        return build_model(profile, OPENAI)


def test_an_unbuildable_api_fails_with_a_readable_error(cfg):
    """An endpoint naming a wire format kingfisher does not ship."""
    endpoint = Endpoint("gemini", "https://example.invalid", "sk-not-real")

    with pytest.raises(ConfigError, match="names api 'gemini'"):
        build_model(cfg.models.models["fake-model"], endpoint)


def test_describing_an_adapter_does_not_import_its_sdk():
    """A deployment uses the wire formats its endpoints name, so naming the classes
    meant importing every provider's SDK to describe endpoints none of them would
    build.
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
    """Deferring the import defers the error too, so it has to still be a clear one -- a
    typo'd row must not surface as a mysterious attribute failure.
    """
    row = replace(ADAPTERS["openai_responses"], chat_class="langchain_openai:NoSuchModel")
    with pytest.raises(AttributeError, match="NoSuchModel"):
        row.resolve()

    missing_module = replace(row, chat_class="no_such_package:Thing")
    with pytest.raises(ModuleNotFoundError, match="no_such_package"):
        missing_module.resolve()

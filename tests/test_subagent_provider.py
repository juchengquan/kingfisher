"""Which endpoint a delegate runs against.

A style is an endpoint here -- `anthropic` is the gateway, `openai` is OpenAI
proper -- so naming one names where the prompt goes and whose credentials pay.
That is why it is granted rather than free, and why it cannot be half-overridden.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.adapters.agent import CapabilityError, build_agent
from kingfisher.adapters.definitions import read_subagent
from kingfisher.app.config import from_env
from kingfisher.config import ConfigError, Endpoint
from kingfisher.domain.capabilities import Capabilities
from tests.conftest import FakeToolCallingModel, capture_build

ELSEWHERE = Endpoint("openai", "https://api.openai.com/v1", "sk-elsewhere")

BASE_ENV = {
    "KINGFISHER_WORKSPACE": "/tmp/kf-provider-test",
    "KINGFISHER_MODEL": "MiniMax-M3",
    "KINGFISHER_API_STYLE": "anthropic",
    "ANTHROPIC_BASE_URL": "https://api.minimaxi.com/anthropic",
    "ANTHROPIC_API_KEY": "sk-gateway",
}


def define(cfg, body: str, name: str = "reviewer") -> None:
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / f"{name}.md").write_text(body, encoding="utf-8")


def build(cfg, session_dir, monkeypatch, **caps):
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",), **caps),
    )
    (spec,) = [s for s in captured["subagents"] if s["name"] == "reviewer"]
    return spec


# -- endpoints come from credentials already configured -------------------


def test_a_second_style_with_credentials_becomes_an_endpoint():
    """`.env.example` has always carried both pairs and read only one."""
    cfg = from_env(
        {**BASE_ENV, "OPENAI_BASE_URL": "https://api.openai.com/v1", "OPENAI_API_KEY": "sk-o"}
    )

    assert set(cfg.endpoints) == {"openai"}
    assert cfg.endpoint_for("openai").base_url == "https://api.openai.com/v1"
    assert cfg.endpoint_for(None) == cfg.default_endpoint


def test_a_style_without_credentials_is_not_an_endpoint():
    assert from_env(BASE_ENV).endpoints == {}


def test_naming_an_unconfigured_endpoint_is_refused():
    """Falling back would run somewhere the author did not choose, and this is
    the field that decides where the prompt goes."""
    with pytest.raises(ConfigError, match="no endpoint configured for style 'openai'"):
        from_env(BASE_ENV).endpoint_for("openai")


# -- the delegate actually goes there -------------------------------------


def test_a_delegate_runs_against_the_endpoint_it_names(cfg, session_dir, monkeypatch):
    define(cfg, "---\nname: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n---\nGo.\n")
    routed = replace(cfg, endpoints={"openai": ELSEWHERE})

    spec = build(routed, session_dir, monkeypatch)

    assert spec["model"].model_name == "gpt-5"
    assert spec["model"].openai_api_base == ELSEWHERE.base_url


def test_omitting_provider_keeps_the_default(cfg, session_dir, monkeypatch):
    define(cfg, "---\nname: reviewer\ndescription: d\nmodel: MiniMax-M2.5\n---\nGo.\n")

    spec = build(cfg, session_dir, monkeypatch)

    assert spec["model"].anthropic_api_url == cfg.base_url


# -- granted, like middleware ---------------------------------------------


def test_an_endpoint_a_request_may_not_use_is_refused(cfg, session_dir, monkeypatch):
    define(cfg, "---\nname: reviewer\ndescription: d\nprovider: openai\n---\nGo.\n")
    routed = replace(cfg, endpoints={"openai": ELSEWHERE})

    with pytest.raises(CapabilityError, match="may not use"):
        build(routed, session_dir, monkeypatch, providers=())


def test_a_granted_endpoint_goes_through(cfg, session_dir, monkeypatch):
    define(cfg, "---\nname: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n---\nGo.\n")
    routed = replace(cfg, endpoints={"openai": ELSEWHERE})

    spec = build(routed, session_dir, monkeypatch, providers=("openai",))

    assert spec["model"].openai_api_base == ELSEWHERE.base_url


def test_an_upload_cannot_widen_where_the_run_goes():
    """The same structural rule as middleware, for a stronger reason: this one
    chooses which endpoint receives the prompts and whose credentials pay."""
    import inspect

    accepted = set(inspect.signature(Capabilities.including).parameters)

    assert "providers" not in accepted
    assert Capabilities(providers=("a",)).including(skills=("x",)).providers == ("a",)


def test_grants_clamp_endpoints_like_everything_else():
    granted = Capabilities(providers=("anthropic",))

    assert granted.intersect(Capabilities(providers=("anthropic", "openai"))).providers == (
        "anthropic",
    )


# -- the pair is atomic ---------------------------------------------------


def test_overriding_only_the_model_against_a_pinned_provider_is_refused(
    cfg, session_dir, monkeypatch
):
    """A MiniMax model name sent to OpenAI is a 404 if you are lucky and a
    wrong-model run if you are not."""
    define(cfg, "---\nname: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n---\nGo.\n")
    half = replace(cfg, endpoints={"openai": ELSEWHERE}, role_models={"subagent": "CHEAP"})

    with pytest.raises(CapabilityError, match="overrode only its model"):
        build(half, session_dir, monkeypatch)


def test_overriding_both_wins(cfg, session_dir, monkeypatch):
    """An operator who says what they mean is the point of the override."""
    define(cfg, "---\nname: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n---\nGo.\n")
    both = replace(
        cfg,
        endpoints={"openai": ELSEWHERE},
        role_models={"subagent": "MiniMax-M2.5"},
        role_providers={"subagent": "anthropic"},
    )

    spec = build(both, session_dir, monkeypatch)

    assert spec["model"].model == "MiniMax-M2.5"
    assert spec["model"].anthropic_api_url == cfg.base_url


def test_overriding_the_model_alone_is_fine_when_nothing_is_pinned(
    cfg, session_dir, monkeypatch
):
    """The refusal is about a mismatch, not about overriding."""
    define(cfg, "---\nname: reviewer\ndescription: d\nmodel: EXPENSIVE\n---\nGo.\n")

    spec = build(replace(cfg, role_models={"subagent": "CHEAP"}), session_dir, monkeypatch)

    assert spec["model"].model == "CHEAP"


# -- the format -----------------------------------------------------------


def test_the_field_parses(tmp_path):
    spec = read_subagent(
        "---\nname: r\ndescription: d\nprovider: openai\nmodel: gpt-5\n---\nBody.\n",
        tmp_path / "r.md",
    )

    assert (spec.provider, spec.model) == ("openai", "gpt-5")


def test_omitting_it_means_the_default(tmp_path):
    spec = read_subagent("---\nname: r\ndescription: d\n---\nBody.\n", tmp_path / "r.md")

    assert spec.provider is None

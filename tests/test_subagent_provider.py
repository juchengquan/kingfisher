"""Which endpoint a delegate runs against.

A style is an endpoint here -- `anthropic` is the gateway, `openai` is OpenAI
proper -- so naming one names where the prompt goes and whose credentials pay.
That is why it is granted rather than free, why a definition naming one must
say what to run there, and why nothing outside the definition may move it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.config import from_env
from kingfisher.config import ConfigError, Endpoint
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.agent import build_agent
from kingfisher.infrastructure.definitions import read_subagent
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
    (cfg.workspace / "subagents" / f"{name}.yaml").write_text(body, encoding="utf-8")


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
    define(cfg, "name: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n"
        "system_prompt: |\n  Go.\n")
    routed = replace(cfg, endpoints={"openai": ELSEWHERE})

    spec = build(routed, session_dir, monkeypatch)

    assert spec["model"].model_name == "gpt-5"
    assert spec["model"].openai_api_base == ELSEWHERE.base_url


def test_omitting_provider_keeps_the_default(cfg, session_dir, monkeypatch):
    define(cfg, "name: reviewer\ndescription: d\nmodel: MiniMax-M2.5\nsystem_prompt: |\n  Go.\n")

    spec = build(cfg, session_dir, monkeypatch)

    assert spec["model"].anthropic_api_url == cfg.base_url


# -- granted, like middleware ---------------------------------------------


def test_an_endpoint_a_request_may_not_use_is_refused(cfg, session_dir, monkeypatch):
    define(
        cfg,
        "name: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n"
        "system_prompt: |\n  Go.\n",
    )
    routed = replace(cfg, endpoints={"openai": ELSEWHERE})

    with pytest.raises(CapabilityError, match="may not use"):
        build(routed, session_dir, monkeypatch, providers=())


def test_a_granted_endpoint_goes_through(cfg, session_dir, monkeypatch):
    define(cfg, "name: reviewer\ndescription: d\nprovider: openai\nmodel: gpt-5\n"
        "system_prompt: |\n  Go.\n")
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


# -- the definition is the only author ------------------------------------


def test_the_environment_cannot_move_a_delegate_to_another_endpoint(
    cfg, session_dir, monkeypatch
):
    """`KINGFISHER_PROVIDER_SUBAGENT` used to reroute every delegate at once,
    and nothing reads it now.

    Which endpoint receives the prompt is the strongest thing this field
    decides -- it names whose credentials pay -- so a variable that moved all of
    them together was the least appropriate place to say it. A file says it, or
    it runs where the deployment does.
    """
    define(cfg, "name: reviewer\ndescription: d\nmodel: MiniMax-M2.5\nsystem_prompt: |\n  Go.\n")
    monkeypatch.setenv("KINGFISHER_PROVIDER_SUBAGENT", "openai")
    monkeypatch.setenv("KINGFISHER_MODEL_SUBAGENT", "gpt-5")

    spec = build(replace(cfg, endpoints={"openai": ELSEWHERE}), session_dir, monkeypatch)

    assert spec["model"].model == "MiniMax-M2.5"
    assert spec["model"].anthropic_api_url == cfg.base_url


# -- the format -----------------------------------------------------------


def test_the_field_parses(tmp_path):
    spec = read_subagent(
        "name: r\ndescription: d\nprovider: openai\nmodel: gpt-5\nsystem_prompt: |\n  Body.\n",
        tmp_path / "r.md",
    )

    assert (spec.provider, spec.model) == ("openai", "gpt-5")


def test_omitting_it_means_the_default(tmp_path):
    spec = read_subagent("name: r\ndescription: d\nsystem_prompt: |\n  Body.\n", tmp_path / "r.md")

    assert spec.provider is None

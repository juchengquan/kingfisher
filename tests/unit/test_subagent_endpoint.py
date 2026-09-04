"""Which model a delegate runs, and therefore where its prompt goes.

A definition names a model and nothing else. The endpoint follows from it
through the catalogue, so naming a model names whose credentials pay -- which
is why it is granted rather than free, and why nothing outside the definition
may move it.

There was a `provider:` field here too, and a rule that the two moved together.
Both are gone; `test_subagent.py` covers the format side of that.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.config import config_from_env
from kingfisher.config import ConfigError, Endpoint, ModelProfile
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel, capture_build

#: A second endpoint, on a different wire format, so a test can tell "went
#: elsewhere" from "went to the default" by which attribute the value landed on.
ELSEWHERE = Endpoint("openai_responses", "https://api.openai.com/v1", "sk-elsewhere")

CATALOGUE = """
endpoints:
  minimax:
    api: anthropic
    base_url: https://api.minimaxi.com/anthropic
    key_env: MINIMAX_API_KEY

default: MiniMax-M3

models:
  MiniMax-M3:
    endpoint: minimax
  MiniMax-M2.5:
    endpoint: minimax
    max_tokens: 2048
"""


def written(tmp_path, body: str = CATALOGUE):
    path = tmp_path / "models.yaml"
    path.write_text(body, encoding="utf-8")
    return {
        "KINGFISHER_WORKSPACE": str(tmp_path / "ws"),
        "KINGFISHER_MODELS_FILE": str(path),
        "MINIMAX_API_KEY": "sk-gateway",
    }


def define(cfg, body: str, name: str = "reviewer") -> None:
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / f"{name}.yaml").write_text(body, encoding="utf-8")


def build(cfg, session_dir, monkeypatch, *, run_on=None, **caps):
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("reviewer",), **caps),
        run_on=run_on,
    )
    (spec,) = [s for s in captured["subagents"] if s["name"] == "reviewer"]
    return spec


def elsewhere(cfg):
    """`cfg`, plus a model on a second endpoint with params of its own."""
    return replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={**cfg.models.endpoints, "openai": ELSEWHERE},
            models={
                **cfg.models.models,
                "gpt-5": ModelProfile("gpt-5", "openai", max_tokens=32000, timeout_s=90),
            },
        ),
    )


# -- endpoints and models come from the catalogue --------------------------


def test_the_catalogue_is_where_endpoints_and_models_come_from(tmp_path):
    cfg = config_from_env(written(tmp_path))

    assert set(cfg.models.endpoints) == {"minimax"}
    assert set(cfg.models.models) == {"MiniMax-M3", "MiniMax-M2.5"}
    assert cfg.models.default == "MiniMax-M3"
    assert cfg.models.resolve()[1].base_url == "https://api.minimaxi.com/anthropic"


def test_several_models_may_share_one_endpoint(tmp_path):
    """The shape the old `api_style` could not express at all: it keyed
    endpoints by wire format, so there was exactly one per format."""
    cfg = config_from_env(written(tmp_path))

    assert cfg.models.resolve("MiniMax-M3")[1] == cfg.models.resolve("MiniMax-M2.5")[1]


def test_two_endpoints_may_share_one_wire_format(tmp_path):
    """The other half, and the actual motivation: a gateway and a local server
    both speaking Anthropic's format could not both be configured."""
    body = CATALOGUE.replace(
        "default: MiniMax-M3",
        "  vllm:\n    api: anthropic\n    base_url: http://localhost:8000\n"
        "    key_env: VLLM_API_KEY\n\ndefault: MiniMax-M3",
    ).replace("  MiniMax-M2.5:\n    endpoint: minimax\n", "  local:\n    endpoint: vllm\n")
    cfg = config_from_env({**written(tmp_path, body), "VLLM_API_KEY": "sk-local"})

    assert {e.api for e in cfg.models.endpoints.values()} == {"anthropic"}
    assert cfg.models.resolve("local")[1].base_url == "http://localhost:8000"


def test_naming_a_model_the_catalogue_does_not_define_is_refused(tmp_path):
    """Closed, which is what converts a 404 mid-run into a sentence naming the
    catalogue that should have defined it."""
    cfg = config_from_env(written(tmp_path))

    with pytest.raises(ConfigError, match="no model 'gpt-5'"):
        cfg.models.resolve("gpt-5")


# -- the delegate actually goes there --------------------------------------


def test_a_delegate_runs_the_model_it_names(cfg, session_dir, monkeypatch):
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    spec = build(elsewhere(cfg), session_dir, monkeypatch)

    assert spec["model"].model_name == "gpt-5"
    assert spec["model"].openai_api_base == ELSEWHERE.base_url


def test_omitting_the_model_builds_none_of_its_own(cfg, session_dir, monkeypatch):
    """"Runs what the deployment runs" is expressed by building nothing here,
    not by building the default again: a top-level delegate inherits the model
    its parent was constructed with, and a second instance would only be a
    chance for the two to differ."""
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  Go.\n")

    spec = build(cfg, session_dir, monkeypatch)

    assert "model" not in spec


def test_naming_a_model_on_the_default_endpoint_stays_there(cfg, session_dir, monkeypatch):
    """Naming a model is not the same as going elsewhere. Several models behind
    one gateway is the ordinary case, and the endpoint follows the model rather
    than the other way round."""
    define(cfg, "name: reviewer\ndescription: d\nmodel: cheap-model\nsystem_prompt: |\n  Go.\n")

    spec = build(cfg, session_dir, monkeypatch)

    assert spec["model"].anthropic_api_url == cfg.models.resolve()[1].base_url


def test_a_delegates_own_params_reach_its_client(cfg, session_dir, monkeypatch):
    """**The guard this change exists for.**

    `test_models.py` proves the params land for the deployment's own model.
    Nothing proved it for a *delegate*, and that is precisely where the old
    code could drop one: `as_subagent` built a delegate by copying the `Config`
    with four fields swapped, so a fifth that nobody added to that copy was
    silently the deployment's value. A per-model `max_tokens` -- the whole
    point of the table -- would have been exactly that fifth field.

    `cheap-model` carries a ceiling and a timeout that differ from the
    default's, so this cannot pass by accident on the deployment's numbers.
    """
    define(cfg, "name: reviewer\ndescription: d\nmodel: cheap-model\nsystem_prompt: |\n  Go.\n")

    spec = build(cfg, session_dir, monkeypatch)

    assert spec["model"].max_tokens == 321
    assert spec["model"].default_request_timeout == 45
    assert cfg.models.models["fake-model"].max_tokens != 321  # the default it must not have taken


def test_a_delegates_params_survive_going_elsewhere(cfg, session_dir, monkeypatch):
    """The same guard across a wire format, where the attribute names differ.

    `timeout_s` lands on `default_request_timeout` for anthropic and
    `request_timeout` for openai -- the disagreement `LANDING_SITES` exists to
    record -- so a delegate routed elsewhere is the case most able to lose one.
    """
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    spec = build(elsewhere(cfg), session_dir, monkeypatch)

    assert spec["model"].max_tokens == 32000
    assert spec["model"].request_timeout == 90


def test_a_delegate_naming_an_unrunnable_model_is_refused_by_name(cfg, session_dir, monkeypatch):
    """Refused when the delegate is *activated*, not across the catalogue up
    front -- `run_on` exists so a caller can rescue a shipped definition whose
    model their credentials cannot reach, and a catalogue-wide refusal would
    fire before the override could apply.

    The message names the delegate. `resolve_model` knows the model and the
    catalogue but not who asked, and this is the one refusal that fires on a
    file the reader may not own.
    """
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    with pytest.raises(ConfigError, match=r"subagent 'reviewer'.*no model 'gpt-5'"):
        build(cfg, session_dir, monkeypatch)


def test_a_delegate_nobody_activated_cannot_break_the_build(cfg, session_dir, monkeypatch):
    """Seeding a preset you cannot run costs nothing until you ask for it. This
    is what a catalogue-wide check would have taken away."""
    define(cfg, "name: unreachable\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  Go.\n")

    assert build(cfg, session_dir, monkeypatch)["name"] == "reviewer"










def test_an_alias_a_deployment_did_not_bind_costs_nothing_until_activated(
    cfg, session_dir, monkeypatch
):
    """Seeding presets you have not bound for is free, the same rule an
    unrunnable `model:` follows."""
    define(cfg, "name: unbound\ndescription: d\nalias: missing\nsystem_prompt: |\n  Go.\n")
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  Go.\n")

    assert build(cfg, session_dir, monkeypatch)["name"] == "reviewer"




# -- granted, like middleware ----------------------------------------------


def test_an_endpoint_a_request_may_not_reach_is_refused(cfg, session_dir, monkeypatch):
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    with pytest.raises(CapabilityError, match="may not"):
        build(elsewhere(cfg), session_dir, monkeypatch, endpoints=())


def test_a_granted_endpoint_goes_through(cfg, session_dir, monkeypatch):
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    spec = build(elsewhere(cfg), session_dir, monkeypatch, endpoints=("openai",))

    assert spec["model"].openai_api_base == ELSEWHERE.base_url


def test_the_grant_is_checked_against_where_the_model_resolves(cfg, session_dir, monkeypatch):
    """A definition names no endpoint, so the grant cannot be read off it. It is
    checked against the endpoint the model landed on -- the same question, asked
    one step later."""
    define(cfg, "name: reviewer\ndescription: d\nmodel: gpt-5\nsystem_prompt: |\n  Go.\n")

    with pytest.raises(CapabilityError, match="openai"):
        build(elsewhere(cfg), session_dir, monkeypatch, endpoints=("fake",))


def test_an_upload_cannot_widen_where_the_run_goes():
    """The same structural rule as middleware, for a stronger reason: this one
    chooses which endpoint receives the prompts and whose credentials pay."""
    import inspect

    accepted = set(inspect.signature(Capabilities.including).parameters)

    assert "endpoints" not in accepted
    assert Capabilities(endpoints=("a",)).including(skills=("x",)).endpoints == ("a",)


def test_grants_clamp_endpoints_like_everything_else():
    granted = Capabilities(endpoints=("minimax",))

    assert granted.intersect(Capabilities(endpoints=("minimax", "openai"))).endpoints == (
        "minimax",
    )


# -- the definition is the only author -------------------------------------


def test_the_environment_cannot_move_a_delegate_to_another_endpoint(
    cfg, session_dir, monkeypatch
):
    """`KINGFISHER_PROVIDER_SUBAGENT` used to reroute every delegate at once,
    and nothing reads it now.

    Where the prompt goes is the strongest thing a definition decides -- it
    names whose credentials pay -- so a variable moving all of them together was
    the least appropriate place to say it. A file says it, or it runs the
    default.
    """
    define(cfg, "name: reviewer\ndescription: d\nmodel: cheap-model\nsystem_prompt: |\n  Go.\n")
    monkeypatch.setenv("KINGFISHER_PROVIDER_SUBAGENT", "openai")
    monkeypatch.setenv("KINGFISHER_MODEL_SUBAGENT", "gpt-5")

    spec = build(elsewhere(cfg), session_dir, monkeypatch)

    assert spec["model"].model == "cheap-model"
    assert spec["model"].anthropic_api_url == cfg.models.resolve()[1].base_url

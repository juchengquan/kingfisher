"""A request putting a named delegate on a different model.

Everything else a request can say only ever takes something away: it picks from
what the workspace offers and cannot invent anything, which is what makes an
untrusted caller safe to accept. This is the one thing that *chooses*, and
models differ in price by more than an order of magnitude -- so it is off until
a deployment grants it, and granted per name rather than as a switch.

The case it exists for: a shipped `second-opinion` names a model this
deployment's catalogue does not define, and the caller wants it somewhere it can
actually reach, without editing a file it may not own.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.config import Endpoint, ModelProfile
from kingfisher.domain.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.domain.request import Request
from kingfisher.domain.subagent import RunOn
from kingfisher.infrastructure.harness.agent import build_agent
from tests.conftest import FakeToolCallingModel, capture_build, subagents_dir

ELSEWHERE = Endpoint("openai_responses", "https://api.openai.com/v1", "sk-test")

PINNED = """name: second-opinion
description: Answers again, elsewhere.
model: gpt-5
system_prompt: |
  You answer on your own.
"""

PLAIN = """name: reviewer
description: Checks figures.
system_prompt: |
  You check figures.
"""


def _define(cfg, *definitions: str) -> None:
    directory = subagents_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    for body in definitions:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def _built(  # noqa: PLR0913 -- one per thing a request can say about where a
    # delegate runs, and a fixture each for the workspace, session and patcher.
    cfg,
    session_dir,
    monkeypatch,
    *,
    granted_models=ALL,
    run_on=None,
    subagents=("second-opinion",),
):
    """The specs kingfisher handed deepagents, by delegate name.

    `capture_build` records the arguments *and* lets the call through, which is
    the house rule: a stub that does not construct is blind to everything
    deepagents validates while constructing.
    """
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=subagents, models=granted_models),
        run_on=run_on,
    )
    return {spec["name"]: spec for spec in captured["subagents"]}


def _model_of(specs, name: str) -> str:
    """The model instance kingfisher built for one delegate, by its own name.

    `.model` on the Anthropic class and `.model_name` on the OpenAI one -- the
    two spell it differently, and a delegate sent elsewhere is precisely the
    case this file is about.
    """
    built = specs[name]["model"]
    return getattr(built, "model", None) or built.model_name


# -- it is off unless granted ---------------------------------------------


def test_a_request_cannot_name_a_model_by_default(cfg, session_dir, monkeypatch):
    """The default is `None`, and that is the whole safety story: a caller who
    was granted nothing can choose nothing, so nothing changes for a deployment
    that never opts in."""
    _define(cfg, PINNED)

    assert Capabilities().models is None

    with pytest.raises(CapabilityError, match="may not use"):
        _built(
            cfg,
            session_dir,
            monkeypatch,
            granted_models=None,
            run_on={"second-opinion": RunOn("expensive-model")},
        )


def test_only_the_models_a_deployment_granted(cfg, session_dir, monkeypatch):
    """Per name rather than on/off: "on" with no list means any caller may name
    the most expensive model you have credentials for."""
    _define(cfg, PINNED)

    with pytest.raises(CapabilityError, match="expensive-model"):
        _built(
            cfg,
            session_dir,
            monkeypatch,
            granted_models=("MiniMax-M2.5",),
            run_on={"second-opinion": RunOn("expensive-model")},
        )


def test_a_granted_model_goes_through(cfg, session_dir, monkeypatch):
    _define(cfg, PINNED)

    specs = _built(
        cfg,
        session_dir,
        monkeypatch,
        granted_models=("cheap-model",),
        run_on={"second-opinion": RunOn("cheap-model")},
    )

    assert _model_of(specs, "second-opinion") == "cheap-model"


def test_the_deployment_clamps_what_a_request_asks_for():
    """A service intersects before running, so a caller cannot grant itself."""
    deployment = Capabilities(models=("cheap",))

    allowed = deployment.intersect(Capabilities(models=("cheap", "expensive")))

    assert allowed.models == ("cheap",)


def test_an_upload_cannot_widen_it():
    """`including` adds back a caller's *own* definitions. A model name is not
    the caller's text -- it is a bill -- so it is never widened, the same as
    `endpoints` and `middleware`."""
    assert Capabilities(models=("cheap",)).including(subagents=("mine",)).models == ("cheap",)


# -- it replaces the endpoint, never half of it ---------------------------


def test_naming_a_model_replaces_the_file_wholesale(cfg, session_dir, monkeypatch):
    """`second-opinion` names `gpt-5`, which this deployment's catalogue does
    not define -- so without the override it would not build at all.

    There were two fields here once, and a rule that an override had to replace
    both or neither. One field cannot be half of anything, so the rule is gone
    and this is what is left of it: what the caller names is what runs, and
    where it runs follows from that.
    """
    _define(cfg, PINNED)

    specs = _built(
        cfg,
        session_dir,
        monkeypatch,
        granted_models=("cheap-model",),
        run_on={"second-opinion": RunOn("cheap-model")},
    )

    assert _model_of(specs, "second-opinion") == "cheap-model"


def test_an_override_reaches_another_endpoint(cfg, session_dir, monkeypatch):
    routed = replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={**cfg.models.endpoints, "openai": ELSEWHERE},
            models={**cfg.models.models, "gpt-5": ModelProfile("gpt-5", "openai")},
        ),
    )
    _define(routed, PLAIN)

    specs = _built(
        routed,
        session_dir,
        monkeypatch,
        granted_models=("gpt-5",),
        run_on={"reviewer": RunOn("gpt-5")},
        subagents=("reviewer",),
    )

    assert _model_of(specs, "reviewer") == "gpt-5"
    assert specs["reviewer"]["model"].openai_api_base == ELSEWHERE.base_url


def test_an_endpoint_the_request_may_not_reach_is_still_refused(cfg, session_dir):
    """The endpoint grant is checked against where the *overridden* model
    resolves. Choosing a model is not an exemption from where a request's
    prompts may go -- the two grants are separate questions, and this is the one
    about whose credentials pay."""
    routed = replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={**cfg.models.endpoints, "openai": ELSEWHERE},
            models={**cfg.models.models, "gpt-5": ModelProfile("gpt-5", "openai")},
        ),
    )
    _define(routed, PLAIN)

    with pytest.raises(CapabilityError, match="may not"):
        build_agent(
            routed,
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(
                subagents=("reviewer",), models=("gpt-5",), endpoints=("fake",)
            ),
            run_on={"reviewer": RunOn("gpt-5")},
        )


# -- and it has to name something real ------------------------------------


def test_naming_a_delegate_the_request_did_not_activate_is_refused(cfg, session_dir, monkeypatch):
    """A quietly ignored override is the failure this exists to prevent: the
    caller asked for the cheap model and would have been billed for the other.

    The activated delegate pins nothing, deliberately. With a pinned one the
    build would fail for its own reasons -- no endpoint configured -- and this
    would pass without the guard ever running, which is what it did before.
    """
    _define(cfg, PLAIN, PINNED)

    with pytest.raises(CapabilityError, match="did not activate"):
        _built(
            cfg,
            session_dir,
            monkeypatch,
            granted_models=ALL,
            subagents=("reviewer",),
            run_on={"second-opinion": RunOn("cheap-model")},
        )


def test_a_request_carries_none_by_default():
    assert Request("go").run_on == {}

"""A request putting a named subagent on a different model."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import ALL, Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.models import WIRE_FORMATS
from kingfisher.core.config import Endpoint, ModelProfile
from kingfisher.core.definitions.subagents.spec import RunOn
from kingfisher.core.request import Request
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel
from tests.unit.conftest import define_each

ELSEWHERE = Endpoint(
    "openai_responses",
    "https://api.openai.com/v1",
    "sk-test",
    wire_format=WIRE_FORMATS["openai_responses"],
)

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


def _built(
    cfg,
    session_dir,
    *,
    granted_models=ALL,
    run_on=None,
    subagents=("second-opinion",),
):
    """The specs kingfisher handed deepagents, by subagent name."""
    assembled = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=subagents, models=granted_models),
        run_on=run_on,
    )
    return {spec["name"]: spec for spec in assembled.subagents or ()}


def _model_of(specs, name: str) -> str:
    """The model instance kingfisher built for one subagent, by its own name."""
    built = specs[name]["model"]
    return getattr(built, "model", None) or built.model_name


# -- it is off unless granted ---------------------------------------------


def test_a_request_cannot_name_a_model_by_default(cfg, session_dir):
    """The default is `None`, and that is the whole safety story: a caller who was
    granted nothing can choose nothing, so nothing changes for a deployment that
    never opts in.
    """
    define_each(cfg, PINNED)

    assert Capabilities().models is None

    with pytest.raises(CapabilityError, match="may not use"):
        _built(
            cfg,
            session_dir,
            granted_models=None,
            run_on={"second-opinion": RunOn("expensive-model")},
        )


def test_only_the_models_a_deployment_granted(cfg, session_dir, monkeypatch):
    """Per name rather than on/off: "on" with no list means any caller may name the most
    expensive model you have credentials for.
    """
    define_each(cfg, PINNED)

    with pytest.raises(CapabilityError, match="expensive-model"):
        _built(
            cfg,
            session_dir,
            granted_models=("MiniMax-M2.5",),
            run_on={"second-opinion": RunOn("expensive-model")},
        )


def test_the_deployment_clamps_what_a_request_asks_for():
    """A service intersects before running, so a caller cannot grant itself."""
    deployment = Capabilities(models=("cheap",))

    allowed = deployment.intersect(Capabilities(models=("cheap", "expensive")))

    assert allowed.models == ("cheap",)


# -- it replaces the endpoint, never half of it ---------------------------


def test_naming_a_model_replaces_the_file_wholesale(cfg, session_dir):
    """`second-opinion` names `gpt-5`, which this deployment's catalogue does not define
    -- so without the override it would not build at all, and the grant admitting
    `cheap-model` is what lets it.
    """
    define_each(cfg, PINNED)

    specs = _built(
        cfg,
        session_dir,
        granted_models=("cheap-model",),
        run_on={"second-opinion": RunOn("cheap-model")},
    )

    assert _model_of(specs, "second-opinion") == "cheap-model"


def test_an_override_reaches_another_endpoint(cfg, session_dir):
    """An override to a model served elsewhere would swap the model's name and keep the
    old endpoint, sending the subagent's requests somewhere that does not serve it.
    """
    routed = replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={**cfg.models.endpoints, "openai": ELSEWHERE},
            models={**cfg.models.models, "gpt-5": ModelProfile("gpt-5", "openai")},
        ),
    )
    define_each(routed, PLAIN)

    specs = _built(
        routed,
        session_dir,
        granted_models=("gpt-5",),
        run_on={"reviewer": RunOn("gpt-5")},
        subagents=("reviewer",),
    )

    assert _model_of(specs, "reviewer") == "gpt-5"
    assert specs["reviewer"]["model"].openai_api_base == ELSEWHERE.base_url


def test_an_endpoint_the_request_may_not_reach_is_still_refused(cfg, session_dir):
    """The endpoint grant is checked against where the *overridden* model resolves."""
    routed = replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={**cfg.models.endpoints, "openai": ELSEWHERE},
            models={**cfg.models.models, "gpt-5": ModelProfile("gpt-5", "openai")},
        ),
    )
    define_each(routed, PLAIN)

    with pytest.raises(CapabilityError, match="may not"):
        build_agent(
            routed,
            backend=backend_at(routed, session_dir),
            model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
            capabilities=Capabilities(
                subagents=("reviewer",), models=("gpt-5",), endpoints=("fake",)
            ),
            run_on={"reviewer": RunOn("gpt-5")},
        )


# -- and it has to name something real ------------------------------------


def test_naming_a_subagent_the_request_did_not_activate_is_refused(cfg, session_dir):
    """A quietly ignored override is the failure this exists to prevent: the caller
    asked for the cheap model and would have been billed for the other.

    The activated subagent pins nothing on purpose: with a pinned one the build fails
    for its own reasons and this passes without the guard ever running, which is what
    it did before.
    """
    define_each(cfg, PLAIN, PINNED)

    with pytest.raises(CapabilityError, match="did not activate"):
        _built(
            cfg,
            session_dir,
            granted_models=ALL,
            subagents=("reviewer",),
            run_on={"second-opinion": RunOn("cheap-model")},
        )


@pytest.mark.parametrize("granted_models", [ALL, None], ids=["models granted", "none granted"])
def test_a_run_on_is_refused_where_no_subagent_is_granted(cfg, session_dir, granted_models):
    """`run_on` was checked only where a subagent was granted, so a request granted none
    had its override accepted without a word -- a model it may not use included.
    """
    with pytest.raises(CapabilityError, match="did not activate"):
        _built(
            cfg,
            session_dir,
            granted_models=granted_models,
            subagents=None,
            run_on={"reviewer": RunOn("cheap-model")},
        )


def test_an_agent_granting_no_subagents_refuses_a_run_on(scripted):
    """The same override, reaching the build through an agent file with no `subagents:`
    line, which is how a request is granted none without asking for none."""
    from kingfisher.core.service import Kingfisher
    from kingfisher.plugins import LocalSessions
    from tests.conftest import an_agent

    an_agent(scripted, "plain")
    kf = Kingfisher(scripted, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="did not activate"):
        kf.run(Request("go", agent="plain", run_on={"reviewer": RunOn("cheap-model")}))


def test_a_request_carries_none_by_default():
    """A request that names no override would carry one, putting a subagent on a model
    its caller never asked for.
    """
    assert Request("go").run_on == {}

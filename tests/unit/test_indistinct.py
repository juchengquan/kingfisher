"""A delegate that meant to run elsewhere, and did not.

`second-opinion` exists in order *not* to be the model that produced the
answer. When it ends up being that model anyway it still builds, still answers,
and the answer is worth nothing -- there is no error to notice and nothing in
the output that looks wrong. Silence is the failure.

Reported, never refused. Kingfisher cannot know which delegates need to differ:
`reviewer` deliberately runs on the deployment's own model, and that is right
for it. Only a definition that *asked* to be elsewhere can be disappointed.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from kingfisher.application.service import opening_events
from kingfisher.config import Endpoint, ModelProfile
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.harness.activation import indistinct_delegates
from kingfisher.subagents.harness import model_for
from kingfisher.subagents.spec import RunOn
from tests.conftest import an_agent, subagents_dir

ASKED = """name: second-opinion
description: Answers again, elsewhere.
model: gpt-5
system_prompt: |
  You answer on your own.
"""

ASKED_FOR_A_MODEL = """name: cheap
description: Reads a lot.
model: {model}
system_prompt: |
  You read.
"""

ASKED_BY_ALIAS = """name: cheap
description: Reads a lot.
alias: {alias}
system_prompt: |
  You read.
"""

ASKED_FOR_NOTHING = """name: reviewer
description: Checks figures.
system_prompt: |
  You check figures.
"""


class _NoPlacement:
    """`opening_events` reads two attributes off a placement and nothing else."""

    placed = ()
    replaced = ()


def _define(cfg, *definitions: str) -> None:
    directory = subagents_dir(cfg)
    directory.mkdir(parents=True, exist_ok=True)
    for body in definitions:
        name = body.split("\n")[0].removeprefix("name: ").strip()
        (directory / f"{name}.yaml").write_text(body, encoding="utf-8")


def _elsewhere(cfg, url: str):
    """A deployment whose `gpt-5` lives at whatever host `url` names."""
    return replace(
        cfg,
        models=replace(
            cfg.models,
            endpoints={
                **cfg.models.endpoints,
                "openai": Endpoint("openai_responses", url, "sk-test"),
            },
            models={**cfg.models.models, "gpt-5": ModelProfile("gpt-5", "openai")},
        ),
    )


def _found(cfg, session_dir, names, **kwargs):
    return dict(
        indistinct_delegates(cfg, Capabilities(subagents=names), session_dir, **kwargs)
    )


# -- the endpoint turned out to be the same machine ------------------------


def test_a_second_endpoint_pointing_at_the_same_host_is_reported(cfg, session_dir):
    """The live case, and the one a style name cannot show.

A model id says nothing about which machine serves it, so two catalogue
    entries may name one host -- and then `model: gpt-5` reads as "somewhere
    else" while being the same gateway. Measured on a real deployment: both
    endpoints resolved to `api.minimaxi.com`.
    """
    same = _elsewhere(cfg, cfg.models.resolve()[1].base_url.replace("/anthropic", "/v1"))
    _define(same, ASKED)

    found = _found(same, session_dir, ("second-opinion",))

    assert "second-opinion" in found
    assert "same host" in found["second-opinion"]


def test_a_second_endpoint_somewhere_else_is_not_reported(cfg, session_dir):
    """The negative control. Without it this reports on every run that has a
    second endpoint at all, which is noise rather than a finding."""
    elsewhere = _elsewhere(cfg, "https://api.openai.com/v1")
    _define(elsewhere, ASKED)

    assert _found(elsewhere, session_dir, ("second-opinion",)) == {}


# -- the model turned out to be the same model -----------------------------


def test_pinning_the_deployments_own_model_is_reported(cfg, session_dir):
    """Reads as a decision, behaves as a no-op -- and stops being a no-op the
    day someone runs it on a deployment whose default differs."""
    _define(cfg, ASKED_FOR_A_MODEL.format(model=cfg.models.default))

    found = _found(cfg, session_dir, ("cheap",))

    assert "same model as the main agent" in found["cheap"]


def test_pinning_a_different_model_is_not_reported(cfg, session_dir):
    """The negative control, and it has to name a model on another *host* --
    not merely another entry. Several models behind one gateway is the ordinary
    case, so a different model id is not by itself evidence of anywhere else.
    """
    elsewhere = _elsewhere(cfg, "https://api.openai.com/v1")
    _define(elsewhere, ASKED_FOR_A_MODEL.format(model="gpt-5"))

    assert _found(elsewhere, session_dir, ("cheap",)) == {}


def test_a_different_model_on_the_same_gateway_is_reported(cfg, session_dir):
    """`cheap-model` is a different model and the same machine. Worth a line for
    the reason the whole check exists: a second opinion served by the gateway
    that produced the first is the disappointment nothing else would show."""
    _define(cfg, ASKED_FOR_A_MODEL.format(model="cheap-model"))

    assert "same host" in _found(cfg, session_dir, ("cheap",))["cheap"]








def test_a_delegate_that_asked_for_nothing_is_never_reported(cfg, session_dir):
    """`reviewer` runs on the deployment's own model on purpose. Reporting it
    would put a line on every run, which is the noise this exists to avoid
    being -- and would drown the one delegate the message is about.
    """
    _define(cfg, ASKED_FOR_NOTHING)

    assert _found(cfg, session_dir, ("reviewer",)) == {}


def test_a_request_that_activated_no_delegates_is_asked_nothing(cfg, session_dir):
    """Written `subagents=None` rather than left at the default, which stopped
    meaning "none" once an agent could declare a roster of its own."""
    _define(cfg, ASKED)

    assert indistinct_delegates(cfg, Capabilities(subagents=None), session_dir) == ()


# -- an override counts as asking too --------------------------------------


def test_an_override_onto_the_deployments_own_model_is_reported(cfg, session_dir):
    """A caller may put a delegate on the main model deliberately. Saying so is
    still worth a line: they chose the model, not the consequence."""
    _define(cfg, ASKED_FOR_A_MODEL.format(model="cheap-model"))

    found = _found(
        cfg,
        session_dir,
        ("cheap",),
        run_on={"cheap": RunOn(cfg.models.default)},
    )

    assert "same model as the main agent" in found["cheap"]


# -- and the caller hears about it ----------------------------------------


def test_the_caller_is_told_before_the_turn_starts(cfg, session_dir):
    """Through the same channel as a withheld capability, and for the same
    reason: it is a fact about the run rather than a refusal.

    Asked of `_admit` rather than driven through `stream`, because an injected
    agent cannot honour narrowed capabilities -- and narrowing is how a
    delegate gets activated at all. The same route `withheld` is tested by.
    """
    from kingfisher import Kingfisher

    same = _elsewhere(cfg, cfg.models.resolve()[1].base_url.replace("/anthropic", "/v1"))
    _define(same, ASKED)
    an_agent(same, subagents="[second-opinion]")
    service = Kingfisher(same)
    service.start_session("s")

    admitted = service._admit(
        Request(
            "go",
            agent="only",
            session_id="s",
            capabilities=Capabilities(subagents=("second-opinion",)),
        )
    )

    (name, why) = admitted.indistinct[0]
    assert name == "second-opinion"
    assert "same host" in why

    # And it reaches the caller as an event, before the run starts.
    told = opening_events("/runs/t001", (), _NoPlacement(), (), admitted.indistinct)
    kinds = [e.kind for e in told]
    assert kinds.index("indistinct") < kinds.index("run_start")


def test_a_run_with_nothing_to_say_says_nothing(cfg, session_dir):
    """The negative control for the event itself: no line on an ordinary run."""
    from kingfisher import Kingfisher

    _define(cfg, ASKED_FOR_NOTHING)
    an_agent(cfg, subagents="[reviewer]")
    service = Kingfisher(cfg)
    service.start_session("quiet")

    admitted = service._admit(
        Request(
            "go",
            agent="only",
            session_id="quiet",
            capabilities=Capabilities(subagents=("reviewer",)),
        )
    )

    assert admitted.indistinct == ()
    assert not [
        e
        for e in opening_events("/runs/t001", (), _NoPlacement(), (), admitted.indistinct)
        if e.kind == "indistinct"
    ]


def test_the_message_names_the_delegate(cfg, session_dir):
    """`[indistinct] second-opinion runs 'gpt-5' on endpoint 'openai', which
    points at the same host as the default (…)` -- readable without the field."""
    from kingfisher.domain.result import RunEvent

    rendered = str(RunEvent(kind="indistinct", text="second-opinion runs 'M3'", agent="x"))

    assert "second-opinion" in rendered


# -- a definition naming several, and what passes one over --------------------
#
# Everything above reports, which is now all there is to do. A `distinct: true`
# turned the same computation into a refusal and went with `second-opinion`, its
# only user. What survives it is the other reason a candidate is passed over:
# an alias this deployment never bound, which is the case a file naming several
# anticipated -- it is saying which deployments it can still be useful in.


ELSEWHERE_BY_MODEL = """name: elsewhere
description: Answers again, elsewhere.
model: {model}
system_prompt: |
  You answer on your own.
"""

ELSEWHERE_BY_ALIAS = """name: elsewhere
description: Answers again, elsewhere.
alias: {alias}
system_prompt: |
  You answer on your own.
"""


def _spec_from(text):
    return read_subagent(text, Path("elsewhere.yaml"))










def test_naming_the_same_model_is_reported_and_never_refused(cfg, session_dir):
    """Kingfisher cannot know that a delegate needs to differ, so this is only
    ever a report: `reviewer` names the deployment's own model on purpose.

    There was a `distinct: true` that turned it into a refusal. It went with
    `second-opinion`, its only user, and the report is what is left."""
    _define(cfg, ASKED_FOR_A_MODEL.format(model=cfg.models.default))
    spec = _spec_from(
        ELSEWHERE_BY_MODEL.format(model=cfg.models.default)
    )

    assert model_for(spec) == cfg.models.default
    assert "cheap" in _found(cfg, session_dir, ("cheap",))











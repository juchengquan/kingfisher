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

import pytest

from kingfisher.application.service import opening_events
from kingfisher.config import ConfigError, Endpoint, ModelProfile
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.domain.subagent import RunOn
from kingfisher.infrastructure.catalogue.documents import read_subagent
from kingfisher.infrastructure.harness.agent import indistinct_delegates
from kingfisher.infrastructure.harness.delegation import model_for
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
                "openai": Endpoint("openai", url, "sk-test"),
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


# -- only a delegate that asked ------------------------------------------


def test_asking_by_alias_counts_as_asking(cfg, session_dir):
    """The hole this check briefly had, and the reason aliases exist.

    When the presets were stripped of their `model:` lines, `second-opinion`
    asked for nothing -- so it ran the main agent's model *and* this said
    nothing about it, which is the exact silence the whole check was written to
    break. An alias is a delegate asking to be elsewhere, so it is checked like
    one, against whatever the deployment bound it to.
    """
    bound = replace(
        cfg, models=replace(cfg.models, aliases={"alternate": cfg.models.default})
    )
    _define(bound, ASKED_BY_ALIAS.format(alias="alternate"))

    assert "same model as the main agent" in _found(bound, session_dir, ("cheap",))["cheap"]


def test_an_alias_bound_somewhere_else_is_not_reported(cfg, session_dir):
    """The negative control: a binding that did what it was for."""
    base = _elsewhere(cfg, "https://api.openai.com/v1")
    routed = replace(base, models=replace(base.models, aliases={"alternate": "gpt-5"}))
    _define(routed, ASKED_BY_ALIAS.format(alias="alternate"))

    assert _found(routed, session_dir, ("cheap",)) == {}


def test_an_unbound_alias_is_left_to_the_build_to_refuse(cfg, session_dir):
    """Reporting is not refusing -- the module says so. The build raises with
    the message worth reading; saying it twice here, worded for a different
    question, would only get in the way."""
    _define(cfg, ASKED_BY_ALIAS.format(alias="nobody-bound-this"))

    assert _found(cfg, session_dir, ("cheap",)) == {}


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


# -- when the definition says being elsewhere is the point -------------------
#
# Everything above reports. A definition that writes `distinct: true` has said
# the thing kingfisher could not know, and from there the same computation is a
# refusal. The half-built version of this was already shipping: an *unbound*
# alias stopped the build, because falling back to the default "would hand this
# delegate the one model it exists not to be, and nothing in the output would
# look wrong" -- while a bound alias resolving to that same model went through
# with a note.


DISTINCT_BY_MODEL = """name: second-opinion
description: Answers again, elsewhere.
model: {model}
distinct: true
system_prompt: |
  You answer on your own.
"""

DISTINCT_BY_ALIAS = """name: second-opinion
description: Answers again, elsewhere.
alias: {alias}
distinct: true
system_prompt: |
  You answer on your own.
"""


def _spec_from(text):
    return read_subagent(text, Path("second-opinion.yaml"))


def test_a_delegate_that_must_differ_refuses_the_deployments_own_model(cfg):
    """The gap this closes. It used to build, answer, and be worth nothing."""
    spec = _spec_from(DISTINCT_BY_MODEL.format(model=cfg.models.default))

    with pytest.raises(ConfigError) as raised:
        model_for(spec, cfg)

    message = str(raised.value)
    assert "second-opinion" in message
    assert cfg.models.default in message
    assert "same model as the main agent" in message


def test_a_delegate_that_must_differ_refuses_a_second_endpoint_on_one_host(cfg):
    """Hosts, not endpoint names -- two catalogue entries may serve one machine,
    and a second opinion from the same gateway is the disappointment."""
    same_host = _elsewhere(cfg, cfg.models.endpoints["fake"].base_url)
    spec = _spec_from(DISTINCT_BY_MODEL.format(model="gpt-5"))

    with pytest.raises(ConfigError, match="same host"):
        model_for(spec, same_host)


def test_a_delegate_that_must_differ_is_content_when_it_does(cfg):
    spec = _spec_from(DISTINCT_BY_ALIAS.format(alias="alternate"))

    assert model_for(spec, cfg) == "elsewhere-model"


def test_a_delegate_that_must_differ_is_measured_against_who_summoned_it(cfg):
    """"Elsewhere" is relative to the thing that asked, not to the deployment.

    The two agreed while only the deployment could name a model. They part the
    moment anything between the agent and this delegate names one of its own: a
    parent already running `elsewhere-model` summoning a helper bound to
    `elsewhere-model` is two of the same model side by side, and measured
    against the deployment's default it looks like a difference.

    Which is the failure this flag exists for, arriving through the one door it
    was not watching -- the delegate still builds, still answers, and the answer
    is worth nothing.
    """
    spec = _spec_from(DISTINCT_BY_ALIAS.format(alias="alternate"))

    # Under the main agent, which runs the deployment's own model, it genuinely
    # is somewhere else.
    assert model_for(spec, cfg) == "elsewhere-model"

    # Under a delegate already running that model, it is not.
    with pytest.raises(ConfigError, match="same model as whatever summoned it"):
        model_for(spec, cfg, caller="elsewhere-model")


def test_without_the_flag_the_same_model_is_still_only_reported(cfg, session_dir):
    """The default is unchanged, and has to be: `reviewer` names the
    deployment's own model on purpose."""
    _define(cfg, ASKED_FOR_A_MODEL.format(model=cfg.models.default))
    spec = _spec_from(
        DISTINCT_BY_MODEL.format(model=cfg.models.default).replace("distinct: true\n", "")
    )

    assert model_for(spec, cfg) == cfg.models.default
    assert "cheap" in _found(cfg, session_dir, ("cheap",))


def test_the_second_candidate_is_used_when_the_first_is_the_wrong_model(cfg):
    """What a list is for. The first is the deployment's own model, so it is
    passed over -- not because the file hedged, but because this deployment made
    it useless."""
    spec = _spec_from(
        "name: second-opinion\n"
        "description: d\n"
        f"model: [{cfg.models.default}, elsewhere-model]\n"
        "distinct: true\n"
        "system_prompt: |\n  You answer.\n"
    )

    assert model_for(spec, cfg) == "elsewhere-model"


def test_an_alias_nobody_bound_is_passed_over_rather_than_fatal(cfg):
    """Fatal when it is the only candidate, which is the shipped behaviour and
    stays. With another named after it, being unbound is exactly the case the
    file anticipated."""
    spec = _spec_from(
        "name: second-opinion\n"
        "description: d\n"
        "alias: [never-bound, alternate]\n"
        "distinct: true\n"
        "system_prompt: |\n  You answer.\n"
    )

    assert model_for(spec, cfg) == "elsewhere-model"


def test_one_unbound_alias_on_its_own_still_refuses(cfg):
    spec = _spec_from(DISTINCT_BY_ALIAS.format(alias="never-bound"))

    with pytest.raises(ConfigError, match="never-bound"):
        model_for(spec, cfg)


def test_the_refusal_names_every_candidate_and_why_each_failed(cfg):
    """One round trip per rejected candidate is one too many: the fix is in the
    deployment's bindings, and a reader has to know which of them to change."""
    spec = _spec_from(
        "name: second-opinion\n"
        "description: d\n"
        f"model: [{cfg.models.default}, cheap-model]\n"
        "distinct: true\n"
        "system_prompt: |\n  You answer.\n"
    )

    with pytest.raises(ConfigError) as raised:
        model_for(spec, cfg)

    message = str(raised.value)
    assert cfg.models.default in message
    assert "cheap-model" in message
    assert "same model as the main agent" in message
    assert "same host" in message


def test_a_request_override_replaces_the_whole_list(cfg):
    """Not just its head. A caller naming one model has answered the question,
    and letting the file's second choice outrank it would make the override
    conditional on a binding the caller cannot see."""
    spec = _spec_from(
        "name: second-opinion\n"
        "description: d\n"
        "alias: [never-bound, alternate]\n"
        "distinct: true\n"
        "system_prompt: |\n  You answer.\n"
    )

    assert model_for(spec, cfg, override=RunOn(model="elsewhere-model")) == "elsewhere-model"

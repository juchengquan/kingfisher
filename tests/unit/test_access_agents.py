"""Which agent a caller may run, and what they are told about the rest."""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from kingfisher import default_backend
from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError, parse
from kingfisher.domain.capabilities import CapabilityError
from kingfisher.domain.request import Request
from tests.conftest import an_agent

VOCABULARY = "source_ids: [A, B]\n"


@pytest.fixture
def two_agents(cfg):
    """`assistant` for source id A only; `surveyor` for everyone."""
    an_agent(cfg, "assistant", source_ids="[A]")
    an_agent(cfg, "surveyor")
    return replace(cfg, access=parse(yaml.safe_load(VOCABULARY), source="source_ids.yaml"))


def test_a_caller_reaches_an_agent_their_source_id_is_listed_on(two_agents):
    kf = Kingfisher(two_agents, backend=default_backend)
    assert kf.agent_named("assistant", source_ids=("A",)) is not None


def test_an_agent_out_of_reach_reads_as_one_that_does_not_exist(two_agents):
    """Decision 15."""
    kf = Kingfisher(two_agents, backend=default_backend)
    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        kf.agent_named("assistant", source_ids=("B",))


def test_the_listing_in_that_refusal_names_only_reachable_agents(two_agents):
    """The message lists what the workspace offers, and that listing is the enumeration
    this closes.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    with pytest.raises(CapabilityError) as raised:
        kf.agent_named("assistant", source_ids=("B",))
    offers = str(raised.value).split("offers", 1)[1]
    assert "surveyor" in offers
    assert "assistant" not in offers


def test_a_caller_who_reaches_no_agent_is_told_the_workspace_offers_none(cfg):
    an_agent(cfg, "assistant", source_ids="[B]")
    vocabulary = parse({"source_ids": ["A", "B"]}, source="source_ids.yaml")
    kf = Kingfisher(replace(cfg, access=vocabulary), backend=default_backend)
    with pytest.raises(CapabilityError, match="offers none"):
        kf.agent_named("anything", source_ids=("A",))


def test_unscoped_still_reaches_every_agent(two_agents):
    kf = Kingfisher(two_agents, backend=default_backend)
    assert kf.agent_named("assistant", source_ids=UNSCOPED) is not None


def test_a_deployment_with_no_vocabulary_reaches_every_agent(cfg):
    an_agent(cfg, "assistant")
    assert Kingfisher(cfg, backend=default_backend).agent_named("assistant") is not None


def test_an_agent_with_no_source_ids_line_is_reachable_by_everyone(two_agents):
    """`surveyor` writes none, so every source id opens it -- which is what makes adopting
    audiences incremental rather than all-or-nothing.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    assert kf.agent_named("surveyor", source_ids=("B",)) is not None


def test_naming_no_agent_still_says_so(two_agents):
    """The other refusal in the same function keeps working, and its listing is filtered
    too.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    with pytest.raises(CapabilityError, match="names no agent"):
        kf.agent_named(None, source_ids=("B",))


def test_agent_named_without_saying_who_is_calling_is_refused(two_agents):
    """The same rule a turn follows, at the other entry point."""
    kf = Kingfisher(two_agents, backend=default_backend)
    with pytest.raises(AccessError, match="source_ids="):
        kf.agent_named("assistant")


def test_opening_a_session_names_a_directory_rather_than_authorising(two_agents):
    """`open_session_for` mints an id and a directory; it resolves no agent, so there is
    nothing for a policy to check there.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    assert kf.open_session_for(Request(task="t", agent="assistant"))


def test_the_session_route_refuses_an_unreachable_agent(two_agents):
    """What a service calls when a caller opens a session: `agent_named` is the check,
    and it is the same one a turn makes.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    with pytest.raises(CapabilityError, match="no agent named"):
        kf.agent_named("assistant", source_ids=("B",))


def test_a_turn_on_a_pinned_agent_out_of_reach_is_refused(two_agents):
    """A session id is a bearer credential -- `kingfisher_service.access` says so
    outright -- and a session pins its agent for life.
    """
    kf = Kingfisher(two_agents, backend=default_backend)
    opened = kf.open_session_for(Request(task="t", agent="assistant"))

    with pytest.raises(CapabilityError):
        kf._agent_for(
            Request(task="again", agent="assistant", session_id=opened.id),
            opened.directory,
            source_ids=("B",),
        )


def test_a_turn_on_a_pinned_agent_still_in_reach_resolves(two_agents):
    """So the refusal above is not passing because every turn refuses."""
    kf = Kingfisher(two_agents, backend=default_backend)
    opened = kf.open_session_for(Request(task="t", agent="assistant"))

    assert kf._agent_for(
        Request(task="again", agent="assistant", session_id=opened.id),
        opened.directory,
        source_ids=("A",),
    )


# -- a session out of reach reads as one that is not there ------------------


def test_a_session_whose_agent_is_out_of_reach_reads_as_missing(two_agents):
    """A session you cannot run must be indistinguishable from one that was never there."""
    kf = Kingfisher(two_agents, backend=default_backend)
    # Opened and pinned the way `POST /sessions` does it: the id and the
    # directory come first, and the agent is remembered separately.
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id, source_ids=("A",)) is not None
    assert kf.session(session_id, source_ids=("B",)) is None


def test_a_session_is_visible_where_there_is_no_vocabulary(cfg):
    """Every deployment that predates this keeps answering as it did."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(cfg, backend=default_backend)
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id) is not None


def test_unscoped_sees_a_session_whatever_it_runs(two_agents):
    kf = Kingfisher(two_agents, backend=default_backend)
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id, source_ids=UNSCOPED) is not None


def test_a_session_with_nothing_pinned_stays_visible(two_agents):
    """It has no agent to be out of reach of."""
    kf = Kingfisher(two_agents, backend=default_backend)
    session_id = kf.start_session()

    assert kf.session(session_id, source_ids=("B",)) is not None


# -- a definition naming a source id the vocabulary does not declare ------------


def test_a_definition_naming_an_undeclared_source_id_is_refused(cfg):
    """The closed vocabulary's other end, and the one that was written and never wired."""
    an_agent(cfg, "analyst", source_ids="[analists]")
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied, backend=default_backend)


def test_that_refusal_names_the_definition_and_what_is_declared(cfg):
    """Both halves, because a reader has one file to fix and needs the spelling that
    would have worked.
    """
    an_agent(cfg, "analyst", source_ids="[analists]")
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    with pytest.raises(AccessError) as raised:
        Kingfisher(policied, backend=default_backend)

    assert "analyst" in str(raised.value)
    assert "analysts" in str(raised.value)


def test_an_entry_audience_naming_an_undeclared_source_id_is_refused(cfg):
    """Not only the definition's own line: an entry names source ids too, and a typo there
    hides one tool rather than the whole agent -- which is quieter.
    """
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\n"
        "tools:\n  - name: line_count\n    source_ids: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied, backend=default_backend)


def test_a_restricted_definition_reports_the_same_typo_the_same_way(cfg):
    """The neighbouring case, asserted so the ordering is not folklore."""
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\nsource_ids: [analysts]\n"
        "tools:\n  - name: line_count\n    source_ids: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied, backend=default_backend)


def test_a_line_narrowing_past_declared_source_ids_is_reported_not_refused(cfg):
    """Every name is real and the line asks for one the definition never mentions."""
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\nsource_ids: [analysts]\n"
        "tools:\n  - name: line_count\n    source_ids: [auditors]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(
        cfg, access=parse({"source_ids": ["analysts", "auditors"]}, source="source_ids.yaml")
    )

    kf = Kingfisher(policied, backend=default_backend)

    assert kf.access_report.narrowed == (("agent analyst: tool line_count", "auditors"),)
    assert "reaches only callers holding both" in "\n".join(kf.access_report.lines())


def test_a_subagent_is_checked_too(cfg):
    an_agent(cfg, "assistant")
    delegates = cfg.catalogue_roots["subagents"]
    delegates.mkdir(parents=True, exist_ok=True)
    (delegates / "auditor.yaml").write_text(
        "name: auditor\ndescription: A delegate.\nsource_ids: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied, backend=default_backend)


def test_a_declared_source_id_is_fine(cfg):
    an_agent(cfg, "analyst", source_ids="[analysts]")
    policied = replace(cfg, access=parse({"source_ids": ["analysts"]}, source="source_ids.yaml"))

    named = Kingfisher(policied, backend=default_backend).agent_named(
        "analyst", source_ids=("analysts",)
    )
    assert named is not None


def test_nothing_is_checked_where_there_is_no_vocabulary(cfg):
    """A `source_ids:` line on a deployment that declares none is inert, not wrong."""
    an_agent(cfg, "analyst", source_ids="[whatever]")

    assert Kingfisher(cfg, backend=default_backend).agent_named("analyst") is not None


def test_a_delegate_cannot_reach_a_looser_middleware_than_its_agent_granted():
    """Middleware is not additive in effect, which is why an agent narrows it.

    `call-cap-generous` is a *looser* ceiling than `call-cap-strict`. If
    `declares` left `middleware` at `ALL` -- as it does for `endpoints` and
    `models`, on the argument that an agent narrowing those would be a definition
    authorising itself -- a delegate could name whichever registered cap it
    liked and leave the bound its parent runs under.

    So the narrowing is the feature, and this is what says so. The cost shows up
    in `assets_examples/agents/researcher.yaml`, which grants a cap it does not
    want because `sweeper` needs it, and says as much in a comment.
    """
    from kingfisher.kinds.agents.spec import AgentSpec

    spec = AgentSpec(
        name="researcher",
        description="an agent that caps itself",
        system_prompt="You work.",
        middlewares=("call-cap-strict",),
    )

    assert spec.declares().middlewares == ("call-cap-strict",), (
        "an agent's middleware is the ceiling its delegates are clamped by; at "
        "`ALL` a delegate could pick any registered entry, including a looser cap"
    )

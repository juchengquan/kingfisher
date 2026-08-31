"""Which agent a caller may run, and what they are told about the rest.

An agent is not a `Capabilities` axis: a request names one before there is
anything to narrow. So this is checked where a session is opened rather than
where a grant is intersected -- and, because a session pins its agent for life
and a session id is a bearer credential, on every turn afterwards as well.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
import yaml

from kingfisher.application.service import Kingfisher
from kingfisher.domain.access import UNSCOPED, AccessError, parse
from kingfisher.domain.capabilities import CapabilityError
from kingfisher.domain.request import Request
from tests.conftest import an_agent

POLICY = """
groups: [A, B]
agents:
  assistant: [A]
  surveyor: ["*"]
"""


@pytest.fixture
def two_agents(cfg):
    """`assistant` for group A only; `surveyor` for everyone."""
    an_agent(cfg, "assistant")
    an_agent(cfg, "surveyor")
    return replace(cfg, access=parse(yaml.safe_load(POLICY), source="access.yaml"))


def test_a_caller_reaches_an_agent_their_group_is_listed_on(two_agents):
    kf = Kingfisher(two_agents)
    assert kf.for_groups(["A"]).agent_named("assistant") is not None


def test_an_agent_out_of_reach_reads_as_one_that_does_not_exist(two_agents):
    """Decision 15. The wording matters: 'no agent named' rather than 'not
    permitted', so nothing is learned by guessing a name."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        kf.for_groups(["B"]).agent_named("assistant")


def test_the_listing_in_that_refusal_names_only_reachable_agents(two_agents):
    """The message lists what the workspace offers, and that listing is the
    enumeration this closes."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError) as raised:
        kf.for_groups(["B"]).agent_named("assistant")
    offers = str(raised.value).split("offers", 1)[1]
    assert "surveyor" in offers
    assert "assistant" not in offers


def test_a_caller_who_reaches_no_agent_is_told_the_workspace_offers_none(cfg):
    kf = Kingfisher(replace(cfg, access=parse({"groups": ["A"]}, source="access.yaml")))
    with pytest.raises(CapabilityError, match="offers none"):
        kf.for_groups(["A"]).agent_named("anything")


def test_unscoped_still_reaches_every_agent(two_agents):
    kf = Kingfisher(two_agents)
    assert kf.for_groups(UNSCOPED).agent_named("assistant") is not None


def test_a_deployment_with_no_policy_reaches_every_agent(cfg):
    an_agent(cfg, "assistant")
    assert Kingfisher(cfg).agent_named("assistant") is not None


def test_naming_no_agent_still_says_so(two_agents):
    """The other refusal in the same function keeps working, and its listing is
    filtered too."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError, match="names no agent"):
        kf.for_groups(["B"]).agent_named(None)


def test_agent_named_without_saying_who_is_calling_is_refused(two_agents):
    """The same rule a turn follows, at the other entry point."""
    kf = Kingfisher(two_agents)
    with pytest.raises(AccessError, match="for_groups"):
        kf.agent_named("assistant")


def test_opening_a_session_names_a_directory_rather_than_authorising(two_agents):
    """`open_session_for` mints an id and a directory; it resolves no agent, so
    there is nothing for a policy to check there.

    That is not a gap, it is where the seam falls. A resuming turn legitimately
    omits `agent` and runs the one the session remembers, so a check here would
    have to refuse a request that names nothing -- and the name it would need is
    not read until `_agent_for`. Both the first turn and every later one go
    through that, which is why the check lives there and is asserted below.
    """
    kf = Kingfisher(two_agents)
    assert kf.for_groups(["B"]).open_session_for(Request(task="t", agent="assistant"))


def test_the_session_route_refuses_an_unreachable_agent(two_agents):
    """What a service calls when a caller opens a session: `agent_named` is the
    check, and it is the same one a turn makes."""
    kf = Kingfisher(two_agents)
    with pytest.raises(CapabilityError, match="no agent named"):
        kf.for_groups(["B"]).agent_named("assistant")


def test_a_turn_on_a_pinned_agent_out_of_reach_is_refused(two_agents):
    """A session id is a bearer credential -- `kingfisher_service.access` says
    so outright -- and a session pins its agent for life. Checked only at the
    open, holding one would be a durable grant to an agent its holder may not
    open, and a demoted caller would keep running what they had before."""
    kf = Kingfisher(two_agents)
    opened = kf.for_groups(["A"]).open_session_for(Request(task="t", agent="assistant"))

    with pytest.raises(CapabilityError):
        kf.for_groups(["B"])._kf._agent_for(
            Request(task="again", agent="assistant", session_id=opened.id),
            opened.id,
            groups=("B",),
        )


def test_a_turn_on_a_pinned_agent_still_in_reach_resolves(two_agents):
    """So the refusal above is not passing because every turn refuses."""
    kf = Kingfisher(two_agents)
    opened = kf.for_groups(["A"]).open_session_for(Request(task="t", agent="assistant"))

    assert kf._agent_for(
        Request(task="again", agent="assistant", session_id=opened.id),
        opened.id,
        groups=("A",),
    )

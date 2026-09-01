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

VOCABULARY = "groups: [A, B]\n"


@pytest.fixture
def two_agents(cfg):
    """`assistant` for group A only; `surveyor` for everyone.

    Each says so in its own file, which is the whole of the change: there is no
    table anywhere naming them, so there is nothing that can name an agent this
    workspace does not have.
    """
    an_agent(cfg, "assistant", groups="[A]")
    an_agent(cfg, "surveyor")
    return replace(cfg, access=parse(yaml.safe_load(VOCABULARY), source="groups.yaml"))


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
    an_agent(cfg, "assistant", groups="[B]")
    kf = Kingfisher(replace(cfg, access=parse({"groups": ["A", "B"]}, source="groups.yaml")))
    with pytest.raises(CapabilityError, match="offers none"):
        kf.for_groups(["A"]).agent_named("anything")


def test_unscoped_still_reaches_every_agent(two_agents):
    kf = Kingfisher(two_agents)
    assert kf.for_groups(UNSCOPED).agent_named("assistant") is not None


def test_a_deployment_with_no_vocabulary_reaches_every_agent(cfg):
    an_agent(cfg, "assistant")
    assert Kingfisher(cfg).agent_named("assistant") is not None


def test_an_agent_with_no_groups_line_is_reachable_by_everyone(two_agents):
    """`surveyor` writes none, so every group opens it -- which is what makes
    adopting audiences incremental rather than all-or-nothing."""
    kf = Kingfisher(two_agents)
    assert kf.for_groups(["B"]).agent_named("surveyor") is not None


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


# -- a session out of reach reads as one that is not there ------------------


def test_a_session_whose_agent_is_out_of_reach_reads_as_missing(two_agents):
    """A session you cannot run must be indistinguishable from one that was
    never there. An id answered 403 would be an id confirmed real, so holding a
    leaked one would still be worth something."""
    kf = Kingfisher(two_agents)
    # Opened and pinned the way `POST /sessions` does it: the id and the
    # directory come first, and the agent is remembered separately.
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id, groups=("A",)) is not None
    assert kf.session(session_id, groups=("B",)) is None


def test_a_session_is_visible_where_there_is_no_vocabulary(cfg):
    """Every deployment that predates this keeps answering as it did."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(cfg)
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id) is not None


def test_unscoped_sees_a_session_whatever_it_runs(two_agents):
    kf = Kingfisher(two_agents)
    session_id = kf.start_session()
    kf.remember_agent(session_id, "assistant")

    assert kf.session(session_id, groups=UNSCOPED) is not None


def test_a_session_with_nothing_pinned_stays_visible(two_agents):
    """It has no agent to be out of reach of. `POST /turns` creates a session
    before its first turn pins anything, so hiding this one would make an id
    unusable in the window between the two."""
    kf = Kingfisher(two_agents)
    session_id = kf.start_session()

    assert kf.session(session_id, groups=("B",)) is not None


# -- a definition naming a group the vocabulary does not declare ------------


def test_a_definition_naming_an_undeclared_group_is_refused(cfg):
    """The closed vocabulary's other end, and the one that was written and never
    wired. Unrefused, `groups: [analists]` is not an error -- it invents a group
    nobody is in, and the only symptom is an agent quietly reachable by no one,
    found weeks later by whoever needed it."""
    an_agent(cfg, "analyst", groups="[analists]")
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied)


def test_that_refusal_names_the_definition_and_what_is_declared(cfg):
    """Both halves, because a reader has one file to fix and needs the spelling
    that would have worked."""
    an_agent(cfg, "analyst", groups="[analists]")
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    with pytest.raises(AccessError) as raised:
        Kingfisher(policied)

    assert "analyst" in str(raised.value)
    assert "analysts" in str(raised.value)


def test_an_entry_audience_naming_an_undeclared_group_is_refused(cfg):
    """Not only the definition's own line: an entry names groups too, and a typo
    there hides one tool rather than the whole agent -- which is quieter.

    Written on a definition that restricts nobody, because that is the thinnest
    case: with no `groups:` above it, `refuse_dead` has nothing to measure the
    line against and this check is the only thing looking. Its neighbour below
    asserts that a restricted definition reports the same fault the same way.
    """
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\n"
        "tools:\n  line_count:\n    groups: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied)


def test_a_restricted_definition_reports_the_same_typo_the_same_way(cfg):
    """The neighbouring case, asserted so the ordering is not folklore.

    A typo makes the line dead *and* undeclared, and both checks can see it.
    The undeclared one goes first deliberately: `never reaches anyone` is true
    but sends its reader to reconcile two audiences, when the fault is one
    misspelled word and the other check names it and offers the spelling."""
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\ngroups: [analysts]\n"
        "tools:\n  line_count:\n    groups: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied)


def test_a_line_dead_against_declared_groups_is_still_refused(cfg):
    """What survives the reordering: every name is real, and the line still
    reaches nobody. Nothing about spelling is left to say, so the dead-policy
    check is the one with something to report."""
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "analyst.yaml").write_text(
        "name: analyst\ndescription: An agent.\ngroups: [analysts]\n"
        "tools:\n  line_count:\n    groups: [auditors]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(
        cfg, access=parse({"groups": ["analysts", "auditors"]}, source="groups.yaml")
    )

    with pytest.raises(AccessError, match="never reaches anyone"):
        Kingfisher(policied)


def test_a_subagent_is_checked_too(cfg):
    an_agent(cfg, "assistant")
    delegates = cfg.catalogue_roots["subagents"]
    delegates.mkdir(parents=True, exist_ok=True)
    (delegates / "auditor.yaml").write_text(
        "name: auditor\ndescription: A delegate.\ngroups: [analists]\n"
        "system_prompt: |\n  Do it.\n",
        encoding="utf-8",
    )
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    with pytest.raises(AccessError, match="analists"):
        Kingfisher(policied)


def test_a_declared_group_is_fine(cfg):
    an_agent(cfg, "analyst", groups="[analysts]")
    policied = replace(cfg, access=parse({"groups": ["analysts"]}, source="groups.yaml"))

    assert Kingfisher(policied).agent_named("analyst", groups=("analysts",)) is not None


def test_nothing_is_checked_where_there_is_no_vocabulary(cfg):
    """A `groups:` line on a deployment that declares none is inert, not wrong.
    Checking it would need a vocabulary to check against, and there is none."""
    an_agent(cfg, "analyst", groups="[whatever]")

    assert Kingfisher(cfg).agent_named("analyst") is not None

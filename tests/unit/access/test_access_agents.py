"""Which agent a caller may run, and what they are told about the rest."""

from __future__ import annotations

import pytest

from kingfisher.core.access.capabilities import CapabilityError
from kingfisher.core.access.source_ids import UNSCOPED, CallerError
from kingfisher.core.request import Request
from kingfisher.core.service import Kingfisher
from kingfisher.plugins import LocalSessions
from tests.conftest import agent_named, an_agent, start


@pytest.fixture
def two_agents(cfg):
    """`assistant` for source id A only; `surveyor` for everyone."""
    an_agent(cfg, "assistant", source_ids="[A]")
    an_agent(cfg, "surveyor")
    return cfg


def test_a_caller_reaches_an_agent_their_source_id_is_listed_on(two_agents):
    """A caller holding A must reach an agent locked to A, or the refusals below pass
    against a lock that admits nobody.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    assert agent_named(kf, "assistant", source_ids=("A",)) is not None


def test_an_agent_out_of_reach_reads_as_one_that_does_not_exist(two_agents):
    """Decision 15."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        agent_named(kf, "assistant", source_ids=("B",))


def test_the_listing_in_that_refusal_names_only_reachable_agents(two_agents):
    """The message lists what the definitions offer, and that listing is the enumeration
    this closes.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    with pytest.raises(CapabilityError) as raised:
        agent_named(kf, "assistant", source_ids=("B",))
    offers = str(raised.value).split("definitions offer", 1)[1]
    assert "surveyor" in offers
    assert "assistant" not in offers


def test_a_caller_who_reaches_no_agent_is_told_the_definitions_offer_none(cfg):
    """A caller who reaches no agent would be told "the definitions offer" followed by
    nothing, where the refusal owes them `none`.
    """
    an_agent(cfg, "assistant", source_ids="[B]")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match="definitions offer none"):
        agent_named(kf, "anything", source_ids=("A",))


def test_unscoped_still_reaches_every_agent(two_agents):
    """`UNSCOPED`, read as a caller holding nothing, would lock a run that has no caller
    out of every agent with a `source_ids:` line.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    assert agent_named(kf, "assistant", source_ids=UNSCOPED) is not None


def test_a_deployment_with_no_vocabulary_reaches_every_agent(cfg):
    """A deployment that writes no lock anywhere would have a call naming nobody refused
    for not saying who is calling, though nothing it runs is locked.
    """
    an_agent(cfg, "assistant")
    assert agent_named(Kingfisher(cfg, sessions=LocalSessions()), "assistant") is not None


def test_an_agent_with_no_source_ids_line_is_reachable_by_everyone(two_agents):
    """`surveyor` writes none, so every source id opens it -- which is what makes adopting
    audiences incremental rather than all-or-nothing.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    assert agent_named(kf, "surveyor", source_ids=("B",)) is not None


def test_naming_no_agent_still_says_so(two_agents):
    """The other refusal in the same function keeps working, and its listing is filtered
    too.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match="names no agent"):
        agent_named(kf, None, source_ids=("B",))


def test_the_session_route_refuses_an_unreachable_agent(two_agents):
    """What a service calls when a caller opens a session: `_agent_named` is the check,
    and it is the same one a turn makes.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match="no agent named"):
        agent_named(kf, "assistant", source_ids=("B",))


# -- a turn, first and later ------------------------------------------------


def _started_by_a(kf) -> str:
    """A session caller A ran the agent only A may run in, whose id B has come to hold."""
    session_id = start(kf.cfg, "started-by-a")
    _first_event(kf, Request(task="first", agent="assistant", session_id=session_id), ("A",))
    return session_id


def _first_event(kf, request, source_ids):
    """Setting a turn up is everything before its first event, so a refusal is here."""
    events = kf.stream(request, source_ids=source_ids)
    try:
        return next(events)
    finally:
        events.close()


def test_a_first_turn_naming_an_agent_out_of_reach_is_refused(two_agents):
    """A session with no agent yet is checked on the agent the turn names."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        _first_event(kf, Request(task="t", agent="assistant"), ("B",))


@pytest.mark.parametrize("agent", ["assistant", "missing"], ids=["out-of-reach", "unwritten"])
def test_a_first_turn_naming_nobody_is_refused_for_that_before_its_agent_is_looked_up(
    two_agents, agent
):
    """The agent is looked up with the caller the turn found, so a turn that stopped asking
    first would run `assistant` for nobody, or answer "no agent named" where it owes
    "say who is calling".
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CallerError, match="source_ids="):
        _first_event(kf, Request(task="t", agent=agent), None)


def test_a_later_turn_naming_an_agent_out_of_reach_is_refused(two_agents):
    """The hole: a session's agent was handed over without asking who was calling, so a
    caller holding another's session id ran in it with everything that agent grants.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _started_by_a(kf)

    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        _first_event(kf, Request(task="again", agent="assistant", session_id=session_id), ("B",))


def test_a_later_turn_naming_an_agent_in_reach_runs_in_anyones_session(two_agents):
    """The refusal above is at the agent, not the session: whose session it is is the
    deployment's to decide before it calls, so another caller's agent decides nothing."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _started_by_a(kf)

    assert _first_event(kf, Request(task="again", agent="surveyor", session_id=session_id), ("B",))


def test_a_refused_turn_leaves_nothing_in_the_session(two_agents, tmp_path):
    """Refused at the agent, which is the obvious place, the caller's file was already in
    the session's `/inputs` and the session marked as used -- measured, before the check
    moved to the top of the turn.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    # A turn of A's own first, so everything a turn lays out already exists and
    # anything that moves the timestamp now is the refused caller's doing.
    session_id = _started_by_a(kf)
    directory = two_agents.workspace / "sessions" / session_id
    touched = directory.stat().st_mtime_ns
    planted = tmp_path / "planted.csv"
    planted.write_text("somebody else's\n", encoding="utf-8")
    asked = Request(task="again", agent="assistant", session_id=session_id, inputs=(planted,))

    with pytest.raises(CapabilityError, match="no agent named"):
        _first_event(kf, asked, ("B",))

    assert not (directory / "inputs" / "planted.csv").exists()
    assert directory.stat().st_mtime_ns == touched


def test_a_turn_naming_nobody_is_refused_before_it_writes(two_agents, tmp_path):
    """The refusal of a call that does not say who is calling sat just as late."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _started_by_a(kf)
    planted = tmp_path / "planted.csv"
    planted.write_text("somebody else's\n", encoding="utf-8")

    with pytest.raises(CallerError, match="source_ids="):
        _first_event(kf, Request(task="again", session_id=session_id, inputs=(planted,)), None)

    assert not (two_agents.workspace / "sessions" / session_id / "inputs" / "planted.csv").exists()


# -- a tuple, a list, a string ----------------------------------------------


@pytest.mark.parametrize("held", [("B",), ["B"]], ids=["tuple", "list"])
def test_an_agent_out_of_reach_is_refused_however_the_ids_are_written(two_agents, held):
    """`["B"]` opened an agent restricted to A: the check asked for a tuple and read
    anything else as nobody in particular.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        agent_named(kf, "assistant", source_ids=held)


@pytest.mark.parametrize("held", [("B",), ["B"]], ids=["tuple", "list"])
def test_a_first_turn_is_refused_however_the_ids_are_written(two_agents, held):
    """A first turn asks `_agent_named`, so the same hole reached a turn."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="no agent named 'assistant'"):
        _first_event(kf, Request(task="t", agent="assistant"), held)


@pytest.mark.parametrize("held", [("A",), ["A"]], ids=["tuple", "list"])
def test_a_caller_in_reach_is_let_in_however_the_ids_are_written(two_agents, held):
    """So the refusals above are not passing because a list is turned away outright."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    assert agent_named(kf, "assistant", source_ids=held) is not None


def test_a_bare_string_is_refused_rather_than_read_as_nobody_in_particular(two_agents):
    """Naming an agent let `"B"` through unfiltered, and the listing spelled it out a
    letter at a time, because a string is a sequence too.
    """
    from kingfisher import inventory

    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CallerError, match="not a string"):
        agent_named(kf, "assistant", source_ids="B")
    with pytest.raises(CallerError, match="not a string"):
        inventory(two_agents, source_ids="B")


# -- a lock is in force wherever it is written ---------------------------------


def test_one_lock_is_in_force_with_nothing_else_written(cfg):
    """No file switches it on any more, so none can be missing: one `source_ids:` line
    and a call naming nobody is refused, where a missing file left the line inert."""
    an_agent(cfg, "analyst", source_ids="[analysts]")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CallerError, match="must say who is calling"):
        agent_named(kf, "analyst")
    with pytest.raises(CapabilityError, match="no agent named 'analyst'"):
        agent_named(kf, "analyst", source_ids=("auditors",))
    assert agent_named(kf, "analyst", source_ids=("analysts",)) is not None


def test_a_misspelt_lock_starts_and_locks_its_definition_shut(cfg):
    """Nothing refuses a misspelling now, so it must fail shut: `analists` opens to
    nobody holding `analysts`, which the listing shows beside the spelling meant."""
    an_agent(cfg, "analyst", source_ids="[analists]")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    with pytest.raises(CapabilityError, match="no agent named 'analyst'"):
        agent_named(kf, "analyst", source_ids=("analysts",))


def test_a_subagent_cannot_reach_a_looser_middleware_than_its_agent_granted():
    """Middleware is not additive in effect, which is why an agent narrows it.

    A deployment may register a strict cap and a looser one beside it. If `declares`
    left `middleware` at `ALL` -- as it does for `endpoints` and `models`, on the
    argument that an agent narrowing those would be a definition authorising itself --
    a subagent could name whichever registered cap it liked and leave the bound its
    parent runs under.
    """
    from kingfisher.core.definitions.agents.spec import AgentSpec

    spec = AgentSpec(
        name="capped",
        description="an agent that caps itself",
        system_prompt="You work.",
        middlewares=("strict-cap",),
    )

    assert spec.declares().middlewares == ("strict-cap",), (
        "an agent's middleware is the ceiling its subagents are clamped by; at "
        "`ALL` a subagent could pick any registered entry, including a looser cap"
    )

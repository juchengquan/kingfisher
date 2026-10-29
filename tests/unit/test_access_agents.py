"""Which agent a caller may run, and what they are told about the rest."""

from __future__ import annotations

import pytest

from kingfisher.core.access.capabilities import CapabilityError
from kingfisher.core.access.source_ids import UNSCOPED, CallerError
from kingfisher.core.request import Request
from kingfisher.core.service import Kingfisher
from kingfisher.core.session.model import UnknownSessionError
from kingfisher.plugins import LocalSessions
from tests.conftest import Through, agent_named, an_agent, pin, start


@pytest.fixture
def two_agents(cfg):
    """`assistant` for source id A only; `surveyor` for everyone."""
    an_agent(cfg, "assistant", source_ids="[A]")
    an_agent(cfg, "surveyor")
    return cfg


def test_a_caller_reaches_an_agent_their_source_id_is_listed_on(two_agents):
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


def test_a_caller_who_reaches_no_agent_is_told_the_workspace_offers_none(cfg):
    an_agent(cfg, "assistant", source_ids="[B]")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    with pytest.raises(CapabilityError, match="definitions offer none"):
        agent_named(kf, "anything", source_ids=("A",))


def test_unscoped_still_reaches_every_agent(two_agents):
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    assert agent_named(kf, "assistant", source_ids=UNSCOPED) is not None


def test_a_deployment_with_no_vocabulary_reaches_every_agent(cfg):
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


def _pinned_by_a(kf) -> str:
    """A session opened for caller A and fixed to the agent only A may run."""
    agent_named(kf, "assistant", source_ids=("A",))
    session_id = start(kf.cfg, "pinned-by-a")
    pin(kf, session_id, "assistant")
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


def test_a_later_turn_in_a_session_out_of_reach_is_refused(two_agents):
    """The hole: a pinned agent was returned without asking who was calling, so a caller
    holding another's session id ran in it with everything that agent grants.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)

    with pytest.raises(UnknownSessionError):
        _first_event(kf, Request(task="again", session_id=session_id), ("B",))


def test_that_refusal_is_word_for_word_a_session_that_is_not_there(two_agents):
    """Anything more specific than what reading an absent session answers would say, of
    a session out of reach, more than that it cannot be had.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)
    invented = "never-made"

    with pytest.raises(UnknownSessionError) as refused:
        _first_event(kf, Request(task="again", session_id=session_id), ("B",))
    with pytest.raises(UnknownSessionError) as absent:
        Through(kf).pending(invented, source_ids=("B",))

    assert str(refused.value) == str(absent.value).replace(invented, session_id)


def test_a_later_turn_in_a_session_in_reach_goes_ahead(two_agents):
    """So the refusals above are not passing because every later turn refuses."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)

    assert _first_event(kf, Request(task="again", session_id=session_id), ("A",))


def test_a_refused_turn_leaves_nothing_in_the_session(two_agents, tmp_path):
    """Refused at the agent, which is the obvious place, the caller's file was already in
    the session's `/inputs` and the session marked as used -- measured, before the check
    moved to the top of the turn.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)
    directory = two_agents.workspace / "sessions" / session_id
    # A turn of A's own first, so everything a turn lays out already exists and
    # anything that moves the timestamp now is the refused caller's doing.
    _first_event(kf, Request(task="first", session_id=session_id), ("A",))
    touched = directory.stat().st_mtime_ns
    planted = tmp_path / "planted.csv"
    planted.write_text("somebody else's\n", encoding="utf-8")

    with pytest.raises(UnknownSessionError):
        _first_event(kf, Request(task="again", session_id=session_id, inputs=(planted,)), ("B",))

    assert not (directory / "inputs" / "planted.csv").exists()
    assert directory.stat().st_mtime_ns == touched


def test_a_turn_naming_nobody_is_refused_before_it_writes(two_agents, tmp_path):
    """The refusal of a call that does not say who is calling sat just as late."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)
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


def test_reading_a_session_without_saying_who_is_calling_is_refused(two_agents):
    """The door that answered. Running a turn and asking for an agent both refuse a call
    that names nobody; reading one session took the same argument, ignored its absence,
    and answered for a session pinned to an agent the caller could not open -- because
    "nobody named" and "reaches everything" were the same value.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    with pytest.raises(CallerError, match="source_ids="):
        Through(kf).session(_pinned_by_a(kf))


def test_an_operator_reads_a_session_by_saying_so(two_agents):
    """`UNSCOPED` is what a caller writes to mean no caller, and housekeeping on the
    machine is exactly that -- which is what `kingfisher reap` passes.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    assert Through(kf).session(_pinned_by_a(kf), source_ids=UNSCOPED) is not None


def test_listing_sessions_stays_the_operators(two_agents):
    """The two housekeeping calls take no caller at all, so there is nothing to forget.
    Stated here because the refusal above makes the difference deliberate rather than an
    oversight in the same area.
    """
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)

    assert [info.id for info in kf.sessions()] == [session_id]
    assert Through(kf).delete(session_id) is None


@pytest.mark.parametrize("held", [("B",), ["B"]], ids=["tuple", "list"])
def test_a_session_out_of_reach_is_hidden_however_the_ids_are_written(two_agents, held):
    """And reading one: `["B"]` was shown a session pinned to A's agent."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    assert Through(kf).session(_pinned_by_a(kf), source_ids=held) is None


@pytest.mark.parametrize("held", [("A",), ["A"]], ids=["tuple", "list"])
def test_a_caller_in_reach_is_let_in_however_the_ids_are_written(two_agents, held):
    """So the refusals above are not passing because a list is turned away outright."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())

    assert agent_named(kf, "assistant", source_ids=held) is not None
    assert Through(kf).session(_pinned_by_a(kf), source_ids=held) is not None


def test_a_bare_string_is_refused_rather_than_read_as_nobody_in_particular(two_agents):
    """Naming an agent and reading a session let `"B"` through unfiltered, and the
    listing spelled it out a letter at a time, because a string is a sequence too.
    """
    from kingfisher import inventory

    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = _pinned_by_a(kf)

    with pytest.raises(CallerError, match="not a string"):
        agent_named(kf, "assistant", source_ids="B")
    with pytest.raises(CallerError, match="not a string"):
        Through(kf).session(session_id, source_ids="B")
    with pytest.raises(CallerError, match="not a string"):
        inventory(two_agents, source_ids="B")


# -- a session out of reach reads as one that is not there ------------------


def test_a_session_whose_agent_is_out_of_reach_reads_as_missing(two_agents):
    """A session you cannot run must be indistinguishable from one that was never there."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = start(two_agents, "pinned")
    pin(kf, session_id, "assistant")

    assert Through(kf).session(session_id, source_ids=("A",)) is not None
    assert Through(kf).session(session_id, source_ids=("B",)) is None


def test_a_session_is_visible_where_there_is_no_vocabulary(cfg):
    """Every deployment that predates this keeps answering as it did."""
    an_agent(cfg, "assistant")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    session_id = start(cfg, "pinned")
    pin(kf, session_id, "assistant")

    assert Through(kf).session(session_id) is not None


def test_unscoped_sees_a_session_whatever_it_runs(two_agents):
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = start(two_agents, "pinned")
    pin(kf, session_id, "assistant")

    assert Through(kf).session(session_id, source_ids=UNSCOPED) is not None


def test_a_session_with_nothing_pinned_stays_visible(two_agents):
    """It has no agent to be out of reach of."""
    kf = Kingfisher(two_agents, sessions=LocalSessions())
    session_id = start(two_agents, "unpinned")

    assert Through(kf).session(session_id, source_ids=("B",)) is not None


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

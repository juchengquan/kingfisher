"""What one caller can reach."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.capabilities import ALL, UNRESTRICTED, Capabilities
from kingfisher.domain.request import Request
from kingfisher.domain.session import (
    Session,
    SessionBusyError,
    UnknownSessionError,
    known,
)
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent


def service(cfg, **kwargs):
    return Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), **kwargs)


# -- T2: a session id is a bearer credential ------------------------------


def test_a_request_cannot_create_a_session_by_naming_one(cfg):
    """The whole of T2."""
    kf = service(cfg)

    with pytest.raises(UnknownSessionError, match="no session 'someone-elses'"):
        kf.run(Request("go", session_id="someone-elses"))


def test_a_request_with_no_id_starts_one_and_says_which(cfg):
    kf = service(cfg)

    result = kf.run(Request("go"))

    assert result.session_id
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()


def test_the_id_it_hands_back_can_be_resumed(cfg):
    """Holding an id is how a caller proves the session is theirs."""
    kf = service(cfg)
    first = kf.run(Request("go"))

    second = kf.run(Request("again", session_id=first.session_id))

    assert second.session_id == first.session_id
    assert second.turn_id != first.turn_id


def test_minted_ids_are_not_guessable(cfg):
    """48 bits was enough to avoid collisions, which is all it was for, and far too few
    for something that opens a conversation and its files.
    """
    kf = service(cfg)

    minted = kf.run(Request("go")).session_id

    assert len(minted) == 32  # uuid4().hex, 128 bits


def test_the_service_may_name_a_session_even_though_a_request_may_not(cfg):
    """T2 is about who is asking, not about names. The service knows."""
    kf = service(cfg)

    kf.start_session("chosen-by-the-service")

    assert kf.run(Request("go", session_id="chosen-by-the-service")).session_id == (
        "chosen-by-the-service"
    )


# -- T3: grants clamp, uploads do not need clamping -----------------------


def test_a_request_cannot_widen_past_what_the_deployment_granted(cfg, session_dir):
    """`intersect` was implemented, tested and called by nothing. Now it runs."""
    kf = Kingfisher(
        cfg, threads=StubCheckpointer(), grants=Capabilities(builtin_tools=("read_file",))
    )

    allowed = kf.grants.intersect(Capabilities(builtin_tools=("read_file", "execute")))

    assert allowed.builtin_tools == ("read_file",)


def test_grants_are_unrestricted_by_default(cfg):
    """A deployment serving one caller is unaffected by any of this."""
    assert Kingfisher(cfg, threads=StubCheckpointer()).grants == UNRESTRICTED


def test_an_uploaded_definition_is_added_back_after_clamping(cfg):
    """A grant list is written before an upload exists and its name is unknowable then,
    so clamping against it would strip every upload rather than authorise it.
    """
    granted = Capabilities(skills=("tabular-qa",), builtin_tools=("read_file",))

    allowed = granted.intersect(Capabilities()).including(skills=("theirs",))

    assert allowed.skills is not None
    assert set(allowed.skills) == {"tabular-qa", "theirs"}
    assert allowed.builtin_tools == ("read_file",)  # untouched


def test_including_cannot_widen_an_unrestricted_set(cfg):
    """`ALL` already includes them; adding names would narrow it."""
    assert Capabilities().including(skills=("theirs",)).skills == ALL
    assert Capabilities(skills=None).including(skills=("theirs",)).skills is None


# -- one turn at a time, per session --------------------------------------
#
# Two turns on one session share a conversation, and the checkpointer writes it
# whole: both read the same history, both append, last write wins. Measured
# before this existed, a turn vanished -- both callers got an answer and a run
# directory, and the conversation kept no record that one of them happened.
#
# Invisible while `stream` was the only path and a CLI served one caller. An API
# is exactly where two requests arrive for one session: two tabs, a retry, a
# double-click.


def _claim(cfg, session_id: str) -> Path:
    """One session's turn slot, which lives inside the session it guards."""
    from kingfisher.infrastructure.workspace.sessions import claim_path

    return claim_path(cfg.workspace / "sessions" / session_id)


def test_a_second_turn_on_a_busy_session_is_refused(cfg):
    """Refused, not queued: a queue hides a wait as long as whatever the other turn is
    doing, and tells a racing caller nothing.
    """
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")

    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    held.claim(service.dirs, _claim(cfg, session), stale_after=3600, now=1000.0)

    with pytest.raises(SessionBusyError, match="already has a turn running"):
        service.run(Request("go", session_id=session))


def test_the_slot_goes_back_when_the_turn_ends(cfg):
    """Or the first turn would wedge the session for an hour."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    service.start_session("s")

    service.run(Request("first", session_id="s"))
    second = service.run(Request("second", session_id="s"))

    assert second.turn_id == "t002"
    assert not _claim(cfg, "s").exists()


def test_the_slot_goes_back_when_admission_refuses(cfg, tmp_path):
    """Every check after the claim can raise, and each one holding the slot on the way
    out would wedge the session over a typo.
    """
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    service.start_session("s")

    with pytest.raises(ValueError):
        service.run(Request("go", session_id="s", data=(tmp_path / "gone.csv",)))

    assert not _claim(cfg, "s").exists()
    assert service.run(Request("after", session_id="s")).turn_id == "t001"


def test_a_claim_older_than_a_turn_could_be_is_taken_over(cfg):
    """A process that died leaves its claim behind."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    held.claim(service.dirs, _claim(cfg, session), stale_after=3600, now=1000.0)

    # The same claim, seen from far enough in the future.
    taken = held.claim(service.dirs, _claim(cfg, session), stale_after=1.0, now=1e12)

    assert taken == _claim(cfg, session)


def test_the_claim_is_somewhere_the_agent_cannot_reach(cfg):
    """The session directory is the backend root, so a claim kept there is something
    `execute` could delete -- which is why it is under `.harness` rather than merely
    inside the session.

    It used to sit outside the session entirely, and this asserted that. What moved
    it in is that a claim there could outlive the session it named; what makes it
    safe there is the pair of denials `.harness` carries, so that is what this
    asserts instead of a location.
    """
    from kingfisher.layout import HARNESS, denied_read_scopes, denied_scopes

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    claim = held.claim(service.dirs, _claim(cfg, session), stale_after=3600, now=1000.0)

    assert claim.is_relative_to(cfg.workspace / "sessions" / session)
    assert claim.parent.name == HARNESS
    assert f"/{HARNESS}/**" in denied_scopes()
    assert f"/{HARNESS}/**" in denied_read_scopes()


def test_two_sessions_do_not_block_each_other(cfg):
    """The slot is per session. One busy conversation must not stop another."""
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    busy = service.start_session("busy")
    other = service.start_session("other")

    held = Session(id=busy, directory=cfg.workspace / "sessions" / busy)
    held.claim(service.dirs, _claim(cfg, busy), stale_after=3600, now=1000.0)

    assert service.run(Request("go", session_id=other)).turn_id == "t001"


# -- asking about a session without running one ---------------------------
#
# There was no way to ask. The only way to learn a session existed was to start
# a turn and catch `UnknownSessionError` -- which builds an agent, marks the
# session used and takes its claim. A service validating an id would have
# refreshed the very clock retention reads.


def test_a_lookup_finds_a_session_and_a_stranger_gets_none(cfg):
    """`None` rather than raising: "is this still there" is an ordinary question with
    two ordinary answers.
    """
    kf = service(cfg)
    session = kf.start_session()

    assert kf.session(session).id == session
    assert kf.session("0" * 32) is None


def test_asking_does_not_disturb_the_session(cfg):
    """The whole reason this exists."""
    import os
    import time

    kf = service(cfg)
    session = kf.start_session()
    directory = cfg.workspace / "sessions" / session
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    assert kf.session(session) is not None
    assert kf.sessions()

    assert directory.stat().st_mtime == pytest.approx(stale, abs=1)
    assert not _claim(cfg, session).exists()


def test_sessions_come_back_most_recently_used_first(cfg):
    """Ordering by last-used only became truthful when a turn began recording it -- a
    turn writes *inside* a session, so before that the timestamp this sorts on was
    not moved by use at all.
    """
    kf = service(cfg)
    first = kf.start_session()
    second = kf.start_session()

    kf.run(Request("go", session_id=first))

    assert kf.sessions()[0].id == first
    assert {s.id for s in kf.sessions()} == {first, second}


def test_a_deleted_session_stops_being_listed(cfg):
    kf = service(cfg)
    kept = kf.start_session()
    gone = kf.start_session()

    kf.delete_session(gone)

    assert [s.id for s in kf.sessions()] == [kept]
    assert kf.session(gone) is None


def test_what_comes_back_names_no_path(cfg):
    """A service handed a directory would start reading files out of it, and the layout
    would become a contract nobody wrote down.
    """
    import dataclasses

    kf = service(cfg)
    kf.start_session()

    (info,) = kf.sessions()

    assert {f.name for f in dataclasses.fields(info)} == {"id", "last_used"}


def test_the_ordering_rule_needs_no_filesystem():
    """The domain half -- `known` takes what `listing` returns and nothing more, so the
    rule is testable without a workspace.
    """
    ordered = known((("old", 1.0), ("newest", 9.0), ("middle", 5.0)))

    assert [s.id for s in ordered] == ["newest", "middle", "old"]
    assert ordered[0].last_used == 9.0


def test_the_read_path_and_the_sweep_see_the_same_sessions(cfg):
    """Both read `listing`, so a session retention can end is one a caller can ask
    about.
    """
    import time

    kf = service(cfg)
    for _ in range(3):
        kf.start_session()

    listed = {s.id for s in kf.sessions()}
    swept = set(kf.reap(older_than_seconds=0.0, now=time.time() + 10).removed)

    assert listed == swept
    assert kf.sessions() == ()

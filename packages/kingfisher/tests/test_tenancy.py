"""What one caller can reach, and what a turn is allowed to destroy."""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent

from kingfisher import Kingfisher
from kingfisher.domain import retention
from kingfisher.domain.capabilities import ALL, UNRESTRICTED, Capabilities
from kingfisher.domain.request import Request
from kingfisher.domain.session import (
    Session,
    SessionBusyError,
    UnknownSessionError,
    known,
    still_held,
)


def service(cfg, **kwargs):
    return Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer(), **kwargs)


# -- T2: a session id is a bearer credential ------------------------------


def test_a_request_cannot_create_a_session_by_naming_one(cfg):
    """The whole of T2. A service that forwarded an id from its own caller
    would otherwise let that caller choose -- or guess -- the name, and read
    somebody else's conversation and files."""
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
    """48 bits was enough to avoid collisions, which is all it was for, and
    far too few for something that opens a conversation and its files."""
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
    """A deployment serving one caller is unaffected by any of this.

    `UNRESTRICTED`, not `Capabilities()`: a grant that said nothing about
    subagents would clamp away every request that named one, because for a
    *request* saying nothing means wiring none.
    """
    assert Kingfisher(cfg, threads=StubCheckpointer()).grants == UNRESTRICTED


def test_an_uploaded_definition_is_added_back_after_clamping(cfg):
    """A grant list is written before an upload exists and its name is
    unknowable then, so clamping against it would strip every upload rather
    than authorise it. The caller supplied the content; the tool clamp, which
    `including` does not touch, is what actually bounds it."""
    granted = Capabilities(skills=("tabular-qa",), builtin_tools=("read_file",))

    allowed = granted.intersect(Capabilities()).including(skills=("theirs",))

    assert allowed.skills is not None
    assert set(allowed.skills) == {"tabular-qa", "theirs"}
    assert allowed.builtin_tools == ("read_file",)  # untouched


def test_including_cannot_widen_an_unrestricted_set(cfg):
    """`ALL` already includes them; adding names would narrow it. And `None`
    asked for none, so an upload is not a way back through that door."""
    assert Capabilities().including(skills=("theirs",)).skills == ALL
    assert Capabilities(skills=None).including(skills=("theirs",)).skills is None


# -- lifecycle: disposal is asked for -------------------------------------


def test_a_turn_disposes_of_nothing(cfg):
    """Retention counted every caller's sessions together, so a busy caller
    evicted a quiet one on a turn that had nothing to do with it."""
    kf = service(cfg)
    quiet = kf.start_session()
    for _ in range(5):
        kf.run(Request("busy"))

    assert (cfg.workspace / "sessions" / quiet).is_dir()


def test_delete_session_removes_the_directory_and_the_thread(cfg):
    threads = StubCheckpointer()
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)
    session_id = kf.run(Request("go")).session_id

    assert kf.delete_session(session_id) is None
    assert not (cfg.workspace / "sessions" / session_id).exists()
    assert threads.deleted == [session_id]


def test_deleting_an_unknown_session_is_not_an_error(cfg):
    """A caller that retries a delete should not have to care."""
    assert service(cfg).delete_session("never-existed") is None


def test_reap_disposes_of_the_idle_and_leaves_the_rest(cfg):
    """Age, not count: how long a session has been idle is a property of that
    session alone, so one caller's traffic cannot evict another's."""
    import os

    kf = service(cfg)
    old, fresh = kf.start_session("old"), kf.start_session("fresh")
    os.utime(cfg.workspace / "sessions" / old, (1_000, 1_000))

    result = kf.reap(older_than_seconds=60, now=10_000)

    assert result.removed == ("old",)
    assert not (cfg.workspace / "sessions" / old).exists()
    assert (cfg.workspace / "sessions" / fresh).is_dir()


# -- threads that outlived their session -----------------------------------


class ListingCheckpointer(StubCheckpointer):
    """A store that can also say which threads it holds, as a real one can.

    `StubCheckpointer` deliberately cannot: `ThreadStore` is only "something
    that forgets a thread", and a sweep must still work when handed one. That
    case has its own test below.
    """

    def __init__(self, held: tuple[str, ...]) -> None:
        super().__init__()
        self.held = held

    # Named `list` because that is the saver's own method, which is what
    # `thread_ids` looks for. It shadows the builtin inside this class, so
    # `held` is annotated as a tuple rather than a `list[str]` that would
    # resolve to this method.
    def list(self, _config):
        from types import SimpleNamespace

        return [
            SimpleNamespace(config={"configurable": {"thread_id": t}}) for t in self.held
        ]


def test_a_thread_whose_session_is_gone_is_deleted(cfg):
    """`discard` takes the thread and the directory together, so a swept session
    leaves neither. A directory that goes any other way -- by hand, or one of
    the sessions that could not be removed until `remove_tree` learned to unlock
    `/data` -- left its thread forever, because nothing else looked. One real
    workspace held 132 of them and 1,894 checkpoints after every session had
    been reaped.
    """
    import time

    threads = ListingCheckpointer(held=("ghost-a", "ghost-b"))
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert set(result.orphans) == {"ghost-a", "ghost-b"}
    assert set(threads.deleted) == {"ghost-a", "ghost-b"}


def test_a_thread_whose_session_still_exists_is_left_alone(cfg):
    """The reconciliation must not eat live conversations."""
    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    live = kf.start_session()

    threads = ListingCheckpointer(held=(live, "ghost"))
    kf2 = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads)
    result = kf2.reap(older_than_seconds=10_000, now=1_000)  # nothing expired

    assert result.removed == ()
    assert result.orphans == ("ghost",)
    assert live not in threads.deleted
    assert (cfg.workspace / "sessions" / live).is_dir()


def test_orphans_are_reported_apart_from_sessions_this_sweep_ended(cfg):
    """They are not sessions this call decided to end; they are residue from
    ones that ended some other way, and a janitor's log should tell them apart.
    """
    import time

    kf = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    doomed = kf.start_session()

    threads = ListingCheckpointer(held=("ghost",))
    result = Kingfisher(cfg, agent=StubAgent("ok"), threads=threads).reap(
        older_than_seconds=0, now=time.time()
    )

    assert result.removed == (doomed,)
    assert result.orphans == ("ghost",)


def test_a_store_that_cannot_enumerate_still_sweeps(cfg):
    """`ThreadStore` is only `delete_thread`. Widening the port to make
    reconciliation possible would break every double, so a store that cannot
    answer is skipped rather than failing the sweep it was asked for.
    """
    import time

    kf = service(cfg)
    idle = kf.start_session()

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert result.removed == (idle,)
    assert result.orphans == ()


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


def _claims(cfg) -> Path:
    return cfg.state_dir / "claims"


def test_a_second_turn_on_a_busy_session_is_refused(cfg):
    """Refused, not queued: a queue hides a wait as long as whatever the other
    turn is doing, and tells a racing caller nothing."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")

    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    held.claim(service.dirs, _claims(cfg), stale_after=3600, now=1000.0)

    with pytest.raises(SessionBusyError, match="already has a turn running"):
        service.run(Request("go", session_id=session))


def test_the_slot_goes_back_when_the_turn_ends(cfg):
    """Or the first turn would wedge the session for an hour."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    service.start_session("s")

    service.run(Request("first", session_id="s"))
    second = service.run(Request("second", session_id="s"))

    assert second.turn_id == "t002"
    assert not (_claims(cfg) / "s").exists()


def test_the_slot_goes_back_when_admission_refuses(cfg, tmp_path):
    """Every check after the claim can raise, and each one holding the slot on
    the way out would wedge the session over a typo."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    service.start_session("s")

    with pytest.raises(ValueError):
        service.run(Request("go", session_id="s", data=(tmp_path / "gone.csv",)))

    assert not (_claims(cfg) / "s").exists()
    assert service.run(Request("after", session_id="s")).turn_id == "t001"


def test_a_claim_older_than_a_turn_could_be_is_taken_over(cfg):
    """A process that died leaves its claim behind. `turn_timeout_s` already
    bounds how long a turn may run, so past it the holder is gone or was going
    to be stopped anyway."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    held.claim(service.dirs, _claims(cfg), stale_after=3600, now=1000.0)

    # The same claim, seen from far enough in the future.
    taken = held.claim(service.dirs, _claims(cfg), stale_after=1.0, now=1e12)

    assert taken == _claims(cfg) / session


def test_the_claim_is_somewhere_the_agent_cannot_reach(cfg):
    """The session directory is the backend root, so a claim kept there is
    something `execute` could delete. `state_dir` is host-side only."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    claim = held.claim(service.dirs, _claims(cfg), stale_after=3600, now=1000.0)

    assert cfg.workspace / "sessions" not in claim.parents
    assert claim.is_relative_to(cfg.state_dir)


def test_two_sessions_do_not_block_each_other(cfg):
    """The slot is per session. One busy conversation must not stop another."""
    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    busy = service.start_session("busy")
    other = service.start_session("other")

    held = Session(id=busy, directory=cfg.workspace / "sessions" / busy)
    held.claim(service.dirs, _claims(cfg), stale_after=3600, now=1000.0)

    assert service.run(Request("go", session_id=other)).turn_id == "t001"


# -- a sweep and a session in use -----------------------------------------
#
# `expired` names sessions "untouched for longer than X" and reads one
# timestamp to decide. A turn writes *inside* a session -- `runs/`, `derived/`
# -- which leaves the session's own timestamp alone, so a conversation in daily
# use still read as idle. Measured before this: 10,000s idle immediately after
# a turn completed in it.


def test_a_turn_records_that_its_session_was_used(cfg):
    """Or the idle clock measures something other than idleness."""
    import os
    import time

    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    directory = cfg.workspace / "sessions" / session

    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))
    service.run(Request("go", session_id=session))

    assert time.time() - directory.stat().st_mtime < 60


def test_a_sweep_keeps_a_session_that_has_a_turn_running(cfg):
    """A turn may outlive the idle bound -- `turn_timeout_s` defaults to an
    hour and nothing requires a session to be kept that long -- and sweeping
    one mid-turn deletes the directory out from under an agent still writing
    to it, leaving the claim pointing at nothing."""
    import os
    import time

    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    directory = cfg.workspace / "sessions" / session

    held = Session(id=session, directory=directory)
    held.claim(service.dirs, cfg.state_dir / "claims", stale_after=3600, now=time.time())
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    result = service.reap(older_than_seconds=1.0, now=time.time())

    assert result.removed == ()
    assert directory.exists()


def test_a_busy_session_does_not_shelter_an_idle_one(cfg):
    """Per session, not a global pause on sweeping."""
    import os
    import time

    service = Kingfisher(cfg, agent=StubAgent("ok"), threads=StubCheckpointer())
    busy = service.start_session("busy")
    idle = service.start_session("idle")

    held = Session(id=busy, directory=cfg.workspace / "sessions" / busy)
    held.claim(service.dirs, cfg.state_dir / "claims", stale_after=3600, now=time.time())
    stale = time.time() - 10_000
    for name in (busy, idle):
        os.utime(cfg.workspace / "sessions" / name, (stale, stale))

    result = service.reap(older_than_seconds=1.0, now=time.time())

    assert result.removed == (idle,)
    assert (cfg.workspace / "sessions" / busy).exists()


def test_the_rule_itself_keeps_what_is_busy():
    """The domain half, without a filesystem."""
    entries = (("a", 0.0), ("b", 0.0))

    assert retention.expired(entries, 1.0, now=100.0).doomed == ("a", "b")
    assert retention.expired(entries, 1.0, now=100.0, busy=("a",)).doomed == ("b",)
    assert retention.expired(entries, 1.0, now=100.0, busy=("a",)).kept == 1


# -- asking about a session without running one ---------------------------
#
# There was no way to ask. The only way to learn a session existed was to start
# a turn and catch `UnknownSessionError` -- which builds an agent, marks the
# session used and takes its claim. A service validating an id would have
# refreshed the very clock retention reads.


def test_a_lookup_finds_a_session_and_a_stranger_gets_none(cfg):
    """`None` rather than raising: "is this still there" is an ordinary
    question with two ordinary answers. `UnknownSessionError` is for a request
    that named a session and meant to use it."""
    kf = service(cfg)
    session = kf.start_session()

    assert kf.session(session).id == session
    assert kf.session("0" * 32) is None


def test_asking_does_not_disturb_the_session(cfg):
    """The whole reason this exists. Validating an id must not refresh the
    clock retention reads, nor hold the claim a turn needs."""
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
    assert not (cfg.state_dir / "claims" / session).exists()


def test_sessions_come_back_most_recently_used_first(cfg):
    """Ordering by last-used only became truthful when a turn began recording
    it -- a turn writes *inside* a session, so before that the timestamp this
    sorts on was not moved by use at all."""
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
    """A service handed a directory would start reading files out of it, and
    the layout would become a contract nobody wrote down. Ids and last-used is
    kingfisher's own vocabulary: a name to pass back to `run`, and a clock."""
    import dataclasses

    kf = service(cfg)
    kf.start_session()

    (info,) = kf.sessions()

    assert {f.name for f in dataclasses.fields(info)} == {"id", "last_used"}


def test_the_ordering_rule_needs_no_filesystem():
    """The domain half -- `known` takes what `listing` returns and nothing
    more, so the rule is testable without a workspace."""
    ordered = known((("old", 1.0), ("newest", 9.0), ("middle", 5.0)))

    assert [s.id for s in ordered] == ["newest", "middle", "old"]
    assert ordered[0].last_used == 9.0


def test_the_read_path_and_the_sweep_see_the_same_sessions(cfg):
    """Both read `listing`, so a session retention can end is one a caller can
    ask about. Two answers to "which exist" would drift apart."""
    import time

    kf = service(cfg)
    for _ in range(3):
        kf.start_session()

    listed = {s.id for s in kf.sessions()}
    swept = set(kf.reap(older_than_seconds=0.0, now=time.time() + 10).removed)

    assert listed == swept
    assert kf.sessions() == ()


# -- a claim outliving its holder -----------------------------------------
#
# `claim` always knew a claim could go stale; retention did not. It read claim
# *names* and spared every session that had one, so the two disagreed about
# what "busy" meant and a process that died mid-turn won its session permanent
# exemption from a workspace whose sessions are supposed to expire.


def test_a_claim_left_by_a_dead_process_stops_sparing_its_session(cfg):
    """Ten years idle and still there, before this. `stale_after` is the turn
    timeout, so past it the holder is gone or was going to be stopped."""
    import time

    kf = service(cfg)
    crashed = kf.start_session()
    (cfg.state_dir / "claims" / crashed).mkdir(parents=True, exist_ok=True)

    decade = time.time() + 10 * 365 * 24 * 3600
    result = kf.reap(older_than_seconds=0.0, now=decade)

    assert crashed in result.removed
    assert kf.session(crashed) is None


def test_a_claim_someone_could_still_hold_spares_its_session(cfg):
    """The half that must not regress -- this is why `busy` exists at all."""
    import time

    kf = service(cfg)
    running = kf.start_session()
    (cfg.state_dir / "claims" / running).mkdir(parents=True, exist_ok=True)

    result = kf.reap(older_than_seconds=0.0, now=time.time())

    assert result.removed == ()
    assert kf.session(running) is not None
    assert (cfg.state_dir / "claims" / running).exists()


def test_retention_and_claim_agree_on_when_a_claim_went_stale(cfg):
    """One rule, so they cannot drift. Just inside the window the session is
    spared; just outside it, both let go."""
    import time

    kf = service(cfg)
    held = kf.start_session()
    (cfg.state_dir / "claims" / held).mkdir(parents=True, exist_ok=True)
    now = time.time()

    inside = kf.reap(older_than_seconds=0.0, now=now + cfg.turn_timeout_s - 60)
    assert inside.removed == ()

    outside = kf.reap(older_than_seconds=0.0, now=now + cfg.turn_timeout_s + 60)
    assert held in outside.removed


def test_a_sweep_leaves_no_claim_behind(cfg):
    """After the session sweep rather than before, so one pass clears a crashed
    holder: the session goes first, which is what makes the claim residue."""
    import time

    kf = service(cfg)
    crashed = kf.start_session()
    claim = cfg.state_dir / "claims" / crashed
    claim.mkdir(parents=True, exist_ok=True)

    kf.reap(older_than_seconds=0.0, now=time.time() + 10 * 365 * 24 * 3600)

    assert not claim.exists()
    assert list((cfg.state_dir / "claims").iterdir()) == []


def test_deleting_a_session_takes_its_claim_with_it(cfg):
    kf = service(cfg)
    session = kf.start_session()
    claim = cfg.state_dir / "claims" / session
    claim.mkdir(parents=True, exist_ok=True)

    kf.delete_session(session)

    assert not claim.exists()


def test_reopening_a_deleted_id_is_not_refused_as_busy(cfg):
    """Why the leftover mattered rather than merely accumulated. `start_session`
    takes a caller's id, so a service reusing one inherited a claim nobody held
    and its first turn was refused until the window ran out."""
    kf = service(cfg)
    kf.start_session("reused")
    (cfg.state_dir / "claims" / "reused").mkdir(parents=True, exist_ok=True)
    kf.delete_session("reused")

    kf.start_session("reused")

    assert kf.run(Request("go", session_id="reused")).answer == "ok"


def test_a_live_claim_is_never_residue_whatever_its_age(cfg):
    """The domain half, and the line between the two questions. Age decides
    whether a claim can be taken over -- that stays with `claim`, where
    `create_exclusive` settles the race. Residue is decided by the session
    being gone."""
    assert retention.orphaned(("a", "b"), ("b",)) == ("a",)
    assert retention.orphaned(("a",), ("a",)) == ()


def test_the_staleness_rule_needs_no_filesystem():
    ordered = still_held((("fresh", 100.0), ("old", 10.0)), stale_after=50.0, now=120.0)

    assert ordered == ("fresh",)

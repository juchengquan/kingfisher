"""What a turn is allowed to destroy."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher
from kingfisher.domain import retention
from kingfisher.domain.request import Request
from kingfisher.domain.session import (
    Session,
    SessionBusyError,
    still_held,
)
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent

# The two helpers both halves of the old file build against, left where the other
# half needs them too -- as `test_tenancy` itself takes `StubAgent` from
# `test_run`. `_claim` reads a session's turn slot, which is a question both
# "may this caller take a turn" and "may this session be swept" have to ask.
from tests.unit.test_tenancy import _claim, service

# -- lifecycle: disposal is asked for -------------------------------------


def test_a_turn_disposes_of_nothing(cfg):
    """Retention counted every caller's sessions together, so a busy caller evicted a
    quiet one on a turn that had nothing to do with it.
    """
    kf = service(cfg)
    quiet = kf.start_session()
    for _ in range(5):
        kf.run(Request("busy"))

    assert (cfg.workspace / "sessions" / quiet).is_dir()


def test_delete_session_removes_the_directory_and_the_thread(cfg):
    threads = StubCheckpointer()
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=threads)
    session_id = kf.run(Request("go")).session_id

    assert kf.delete_session(session_id) is None
    assert not (cfg.workspace / "sessions" / session_id).exists()
    assert threads.deleted == [session_id]


def test_deleting_an_unknown_session_is_not_an_error(cfg):
    """A caller that retries a delete should not have to care."""
    assert service(cfg).delete_session("never-existed") is None


def test_reap_disposes_of_the_idle_and_leaves_the_rest(cfg):
    """Age, not count: how long a session has been idle is a property of that session
    alone, so one caller's traffic cannot evict another's.
    """
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
    """A store that can also say which threads it holds, as a real one can."""

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
    """`discard` takes the thread and the directory together, so a swept session leaves
    neither.
    """
    import time

    threads = ListingCheckpointer(held=("ghost-a", "ghost-b"))
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=threads)

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert set(result.orphans) == {"ghost-a", "ghost-b"}
    assert set(threads.deleted) == {"ghost-a", "ghost-b"}


def test_a_thread_whose_session_still_exists_is_left_alone(cfg):
    """The reconciliation must not eat live conversations."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    live = kf.start_session()

    threads = ListingCheckpointer(held=(live, "ghost"))
    kf2 = Kingfisher(cfg, graph=StubAgent("ok"), threads=threads)
    result = kf2.reap(older_than_seconds=10_000, now=1_000)  # nothing expired

    assert result.removed == ()
    assert result.orphans == ("ghost",)
    assert live not in threads.deleted
    assert (cfg.workspace / "sessions" / live).is_dir()


def test_orphans_are_reported_apart_from_sessions_this_sweep_ended(cfg):
    """They are not sessions this call decided to end; they are residue from ones that
    ended some other way, and a janitor's log should tell them apart.
    """
    import time

    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    doomed = kf.start_session()

    threads = ListingCheckpointer(held=("ghost",))
    result = Kingfisher(cfg, graph=StubAgent("ok"), threads=threads).reap(
        older_than_seconds=0, now=time.time()
    )

    assert result.removed == (doomed,)
    assert result.orphans == ("ghost",)


def test_a_store_that_cannot_enumerate_still_sweeps(cfg):
    """`ThreadStore` is only `delete_thread`."""
    import time

    kf = service(cfg)
    idle = kf.start_session()

    result = kf.reap(older_than_seconds=0, now=time.time())

    assert result.removed == (idle,)
    assert result.orphans == ()


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

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    directory = cfg.workspace / "sessions" / session

    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))
    service.run(Request("go", session_id=session))

    assert time.time() - directory.stat().st_mtime < 60


def test_a_sweep_keeps_a_session_that_has_a_turn_running(cfg):
    """A turn may outlive the idle bound -- `turn_timeout_s` defaults to an hour and
    nothing requires a session to be kept that long -- and sweeping one mid-turn
    deletes the directory out from under an agent still writing to it, leaving the
    claim pointing at nothing.
    """
    import os
    import time

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session = service.start_session("s")
    directory = cfg.workspace / "sessions" / session

    held = Session(id=session, directory=directory)
    held.claim(service.dirs, _claim(cfg, session), stale_after=3600, now=time.time())
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    result = service.reap(older_than_seconds=1.0, now=time.time())

    assert result.removed == ()
    assert directory.exists()


def test_a_busy_session_does_not_shelter_an_idle_one(cfg):
    """Per session, not a global pause on sweeping."""
    import os
    import time

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    busy = service.start_session("busy")
    idle = service.start_session("idle")

    held = Session(id=busy, directory=cfg.workspace / "sessions" / busy)
    held.claim(service.dirs, _claim(cfg, busy), stale_after=3600, now=time.time())
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


# -- a claim outliving its holder -----------------------------------------
#
# `claim` always knew a claim could go stale; retention did not. It read claim
# *names* and spared every session that had one, so the two disagreed about
# what "busy" meant and a process that died mid-turn won its session permanent
# exemption from a workspace whose sessions are supposed to expire.


def test_a_claim_left_by_a_dead_process_stops_sparing_its_session(cfg):
    """Ten years idle and still there, before this."""
    import time

    kf = service(cfg)
    crashed = kf.start_session()
    _claim(cfg, crashed).mkdir(parents=True, exist_ok=True)

    decade = time.time() + 10 * 365 * 24 * 3600
    result = kf.reap(older_than_seconds=0.0, now=decade)

    assert crashed in result.removed
    assert kf.session(crashed) is None


def test_a_claim_someone_could_still_hold_spares_its_session(cfg):
    """The half that must not regress -- this is why `busy` exists at all."""
    import time

    kf = service(cfg)
    running = kf.start_session()
    _claim(cfg, running).mkdir(parents=True, exist_ok=True)

    result = kf.reap(older_than_seconds=0.0, now=time.time())

    assert result.removed == ()
    assert kf.session(running) is not None
    assert _claim(cfg, running).exists()


def test_retention_and_claim_agree_on_when_a_claim_went_stale(cfg):
    """One rule, so they cannot drift."""
    import time

    kf = service(cfg)
    held = kf.start_session()
    _claim(cfg, held).mkdir(parents=True, exist_ok=True)
    now = time.time()

    inside = kf.reap(older_than_seconds=0.0, now=now + cfg.claim_stale_after - 60)
    assert inside.removed == ()

    outside = kf.reap(older_than_seconds=0.0, now=now + cfg.claim_stale_after + 60)
    assert held in outside.removed


def test_a_claim_survives_the_deadline_that_stops_its_turn(cfg):
    """The window a turn stops *in*, which the claim used to be taken during."""
    import time

    from kingfisher.domain.session import Session, still_held

    kf = service(cfg)
    held = kf.start_session()
    slot = _claim(cfg, held)
    session = Session(id=held, directory=cfg.workspace / "sessions" / held)
    taken = time.time()
    session.claim(kf.dirs, slot, stale_after=cfg.claim_stale_after, now=taken)

    # The instant the turn runs out of time, and a little after.
    at_deadline = taken + cfg.turn_timeout_s
    assert still_held(
        ((held, taken),), stale_after=cfg.claim_stale_after, now=at_deadline
    ) == (held,)

    # And a second caller is refused for the whole of that window.
    with pytest.raises(SessionBusyError):
        Session(id=held, directory=session.directory).claim(
            kf.dirs, slot, stale_after=cfg.claim_stale_after, now=at_deadline
        )

    # Long enough after, the slot is takeable again -- a holder that died must
    # not lock the session out for good.
    assert (
        still_held(((held, taken),), stale_after=cfg.claim_stale_after, now=taken + 1e6) == ()
    )


def test_a_sweep_leaves_no_claim_behind(cfg):
    """There is no step that clears claim residue, because there is no residue.

    `_discard_dead_claims` used to run after the session sweep, so one pass cleared
    a crashed holder: the session went first, and that is what made its claim an
    orphan. A claim inside the session it guards cannot be orphaned -- it goes with
    the directory -- so that step is gone and this asserts the property directly.
    """
    import time

    kf = service(cfg)
    crashed = kf.start_session()
    claim = _claim(cfg, crashed)
    claim.mkdir(parents=True, exist_ok=True)

    kf.reap(older_than_seconds=0.0, now=time.time() + 10 * 365 * 24 * 3600)

    assert not claim.exists()
    assert not (cfg.workspace / "sessions" / crashed).exists()


def test_deleting_a_session_takes_its_claim_with_it(cfg):
    kf = service(cfg)
    session = kf.start_session()
    claim = _claim(cfg, session)
    claim.mkdir(parents=True, exist_ok=True)

    kf.delete_session(session)

    assert not claim.exists()


def test_reopening_a_deleted_id_is_not_refused_as_busy(cfg):
    """Why the leftover mattered rather than merely accumulated."""
    kf = service(cfg)
    kf.start_session("reused")
    _claim(cfg, "reused").mkdir(parents=True, exist_ok=True)
    kf.delete_session("reused")

    kf.start_session("reused")

    assert kf.run(Request("go", session_id="reused")).answer == "ok"


def test_a_live_claim_is_never_residue_whatever_its_age(cfg):
    """The domain half, and the line between the two questions."""
    assert retention.orphaned(("a", "b"), ("b",)) == ("a",)
    assert retention.orphaned(("a",), ("a",)) == ()


def test_the_staleness_rule_needs_no_filesystem():
    ordered = still_held((("fresh", 100.0), ("old", 10.0)), stale_after=50.0, now=120.0)

    assert ordered == ("fresh",)

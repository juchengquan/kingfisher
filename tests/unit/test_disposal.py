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
from kingfisher.infrastructure.workspace import LocalSessionDirs
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent

# The two helpers both halves of the old file build against, left where the other
# half needs them too -- as `test_tenancy` itself takes `StubAgent` from
# `test_run`. `_claim` reads a session's turn slot, which is a question both
# "may this caller take a turn" and "may this session be swept" have to ask.
from tests.unit.test_tenancy import _claim, service


class StuckDirs(LocalSessionDirs):
    """A filesystem where no session directory will go."""

    def remove_tree(self, path):
        return "directory not removed (Permission denied)"

# -- lifecycle: disposal is asked for -------------------------------------


def test_a_turn_disposes_of_nothing(cfg):
    """Retention counted every caller's sessions together, so a busy caller evicted a
    quiet one on a turn that had nothing to do with it.
    """
    kf = service(cfg)
    quiet = start(cfg, "quiet")
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


def test_a_session_the_store_alone_keeps_is_forgotten_when_deleted(cfg, tmp_path):
    """Under a root of the deployment's own there is no directory in the workspace, and
    `delete_session` stopped there -- so the store kept the session, `knows` still
    answered for it, and a session reported deleted could be resumed.
    """
    from kingfisher import LocalSessionStore
    from kingfisher.domain.session import UnknownSessionError
    from tests.unit.test_service import FreshEachTurn

    kept = LocalSessionStore(tmp_path / "kept")
    kf = service(cfg, sessions=kept, session_root=FreshEachTurn(tmp_path / "for-one-turn"))
    session_id = kf.run(Request("go")).session_id
    assert kept.knows(session_id), "the store never held it, so this would prove nothing"

    assert kf.delete_session(session_id) is None

    assert not kept.knows(session_id)
    with pytest.raises(UnknownSessionError):
        kf.run(Request("again", session_id=session_id))


def test_a_directory_that_would_not_go_keeps_the_store_copy_behind_it(cfg, tmp_path):
    """What `reap` already did and `delete_session` did not: forgetting the store's copy
    of a session whose directory stayed leaves a directory with no history behind it.
    """
    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept")
    kf = service(cfg, sessions=kept, dirs=StuckDirs())
    session_id = kf.run(Request("go")).session_id

    failure = kf.delete_session(session_id)

    assert failure is not None
    assert "not removed" in failure
    assert kept.knows(session_id)


def test_an_evicted_session_resumes_from_the_store_with_its_history(cfg, tmp_path):
    """Eviction frees this machine and keeps the session. One that kept the store's copy
    but not the conversation would resume as a stranger to its own first turn.
    """
    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept")
    agent = StubAgent("ok")
    kf = Kingfisher(cfg, graph=agent, threads=StubCheckpointer(), sessions=kept)
    session_id = kf.run(Request("the first task")).session_id

    assert kf.delete_session(session_id, forget=False) is None

    assert not (cfg.workspace / "sessions" / session_id).exists()
    assert kept.knows(session_id)
    kf.run(Request("again", session_id=session_id))
    assert agent.state is not None
    assert "the first task" in str(agent.state["messages"])


def test_reap_forgets_the_store_copy_of_what_it_swept(cfg, tmp_path):
    """A sweep that kept the store's copy would leave an expired session resumable by id."""
    import time

    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept")
    kf = service(cfg, sessions=kept)
    session_id = kf.run(Request("go")).session_id

    result = kf.reap(older_than_seconds=0, now=time.time() + 10)

    assert result.removed == (session_id,)
    assert not kept.knows(session_id)


def test_reap_can_evict_rather_than_forget(cfg, tmp_path):
    """A sweep that ignored the flag would forget every session it was asked to evict."""
    import time

    from kingfisher import LocalSessionStore

    kept = LocalSessionStore(tmp_path / "kept")
    kf = service(cfg, sessions=kept)
    session_id = kf.run(Request("go")).session_id

    result = kf.reap(older_than_seconds=0, now=time.time() + 10, forget=False)

    assert result.removed == (session_id,)
    assert kept.knows(session_id)


def test_reap_disposes_of_the_idle_and_leaves_the_rest(cfg):
    """Age, not count: how long a session has been idle is a property of that session
    alone, so one caller's traffic cannot evict another's.
    """
    import os

    kf = service(cfg)
    old, fresh = start(cfg, "old"), start(cfg, "fresh")
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
    live = start(cfg, "live")

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

    doomed = start(cfg, "doomed")

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
    idle = start(cfg, "idle")

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
    session = start(cfg, "s")
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
    session = start(cfg, "s")
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
    busy = start(cfg, "busy")
    idle = start(cfg, "idle")

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
    crashed = start(cfg, "crashed")
    _claim(cfg, crashed).mkdir(parents=True, exist_ok=True)

    decade = time.time() + 10 * 365 * 24 * 3600
    result = kf.reap(older_than_seconds=0.0, now=decade)

    assert crashed in result.removed
    assert kf.session(crashed) is None


def test_a_claim_someone_could_still_hold_spares_its_session(cfg):
    """The half that must not regress -- this is why `busy` exists at all."""
    import time

    kf = service(cfg)
    running = start(cfg, "running")
    _claim(cfg, running).mkdir(parents=True, exist_ok=True)

    result = kf.reap(older_than_seconds=0.0, now=time.time())

    assert result.removed == ()
    assert kf.session(running) is not None
    assert _claim(cfg, running).exists()


def test_retention_and_claim_agree_on_when_a_claim_went_stale(cfg):
    """One rule, so they cannot drift."""
    import time

    kf = service(cfg)
    held = start(cfg, "held")
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
    held = start(cfg, "held")
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
    crashed = start(cfg, "crashed")
    claim = _claim(cfg, crashed)
    claim.mkdir(parents=True, exist_ok=True)

    kf.reap(older_than_seconds=0.0, now=time.time() + 10 * 365 * 24 * 3600)

    assert not claim.exists()
    assert not (cfg.workspace / "sessions" / crashed).exists()


def test_deleting_a_session_takes_its_claim_with_it(cfg):
    kf = service(cfg)
    session = start(cfg, "s")
    claim = _claim(cfg, session)
    claim.mkdir(parents=True, exist_ok=True)

    kf.delete_session(session)

    assert not claim.exists()


def test_reopening_a_deleted_id_is_not_refused_as_busy(cfg):
    """Why the leftover mattered rather than merely accumulated."""
    kf = service(cfg)
    start(cfg, "reused")
    _claim(cfg, "reused").mkdir(parents=True, exist_ok=True)
    kf.delete_session("reused")

    start(cfg, "reused")

    assert kf.run(Request("go", session_id="reused")).answer == "ok"


def test_a_live_claim_is_never_residue_whatever_its_age(cfg):
    """The domain half, and the line between the two questions."""
    assert retention.orphaned(("a", "b"), ("b",)) == ("a",)
    assert retention.orphaned(("a",), ("a",)) == ()


def test_the_staleness_rule_needs_no_filesystem():
    ordered = still_held((("fresh", 100.0), ("old", 10.0)), stale_after=50.0, now=120.0)

    assert ordered == ("fresh",)


# -- a run that disposes of its own session -------------------------------


def test_run_disposes_of_the_session_when_it_is_told_to(cfg):
    """The library's half of `--delete-session`, so a caller in Python need not write
    the same two lines the command writes.
    """
    kf = service(cfg)

    result = kf.run(Request("go"), delete_session=True)

    assert result.completed
    assert not (cfg.workspace / "sessions" / result.session_id).exists()
    assert result.deletion_failure is None


def test_run_keeps_the_session_unless_it_is_told_otherwise(cfg):
    """The default, and the behaviour every existing caller has."""
    kf = service(cfg)

    result = kf.run(Request("go"))

    assert (cfg.workspace / "sessions" / result.session_id).is_dir()


def test_a_turn_stopped_at_a_bound_keeps_its_session(cfg):
    """Driven through a real bound rather than asserted about a `RunResult` built here:
    a turn cut short still holds the work it did and the conversation a retry is
    rebuilt from, and a test that made its own result would pass against a rule of its
    own invention.
    """
    from dataclasses import replace

    from tests.unit.test_quotas import SlowAgent

    kf = Kingfisher(
        replace(cfg, turn_timeout_s=0), graph=SlowAgent(steps=5), threads=StubCheckpointer()
    )

    result = kf.run(Request("go"), delete_session=True)

    assert result.stop_reason == "max_duration"
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()
    assert result.deletion_failure is None, "a session kept on purpose is not a failure"


def test_a_deletion_that_fails_is_on_the_result_beside_the_answer(cfg):
    """`run` threw away what `delete_session` answered, so a caller got the answer and no
    sign the session was still there.
    """
    kf = service(cfg, dirs=StuckDirs())

    result = kf.run(Request("go"), delete_session=True)

    assert result.answer == "ok"
    assert result.deletion_failure is not None
    assert "not removed" in result.deletion_failure
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()

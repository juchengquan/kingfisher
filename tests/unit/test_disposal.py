"""What a turn is allowed to destroy."""

from __future__ import annotations

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.core.session import retention
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import still_held
from tests.conftest import Through, start
from tests.unit.test_run import StubAgent

# The two helpers both halves of the old file build against, left where the other
# half needs them too -- as `test_tenancy` itself takes `StubAgent` from
# `test_run`. `_claim` reads a session's turn slot, which is a question both
# "may this caller take a turn" and "may this session be swept" have to ask.
from tests.unit.test_tenancy import _claim, service


class Stuck(LocalSessions):
    """The default, where no session will go."""

    def delete(self, cfg, session_id):
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


def test_delete_session_removes_the_directory(cfg, way):
    """Deleting a session through the service leaves nothing of it on disk."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    session_id = kf.run(Request("go")).session_id

    assert Through(kf, way).delete(session_id) is None
    assert not (cfg.workspace / "sessions" / session_id).exists()


def test_deleting_an_unknown_session_is_not_an_error(cfg, way):
    """A caller that retries a delete should not have to care."""
    assert Through(service(cfg), way).delete("never-existed") is None


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


def test_a_session_named_with_a_backslash_is_swept_like_any_other(cfg):
    """A session id rule refusing `\\`, which a POSIX name may hold, left such a session
    listed but unopenable, and `reap` raised at it and swept nothing.
    """
    import time

    kf = service(cfg)
    odd, plain = start(cfg, "a\\b"), start(cfg, "plain")

    result = kf.reap(older_than_seconds=0, now=time.time() + 60)

    assert set(result.removed) == {odd, plain}


# -- a sweep and a session in use -----------------------------------------
#
# `expired` names sessions "untouched for longer than X" and reads one
# timestamp to decide. A turn writes *inside* a session -- `runs/`, `outputs/`
# -- which leaves the session's own timestamp alone, so a conversation in daily
# use still read as idle. Measured before this: 10,000s idle immediately after
# a turn completed in it.


def test_a_turn_records_that_its_session_was_used(cfg):
    """Or the idle clock measures something other than idleness."""
    import os
    import time

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )
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

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )
    session = start(cfg, "s")
    directory = cfg.workspace / "sessions" / session

    assert LocalSessions().claim(cfg, session, stale_after=3600)
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    result = service.reap(older_than_seconds=1.0, now=time.time())

    assert result.removed == ()
    assert directory.exists()


def test_a_busy_session_does_not_shelter_an_idle_one(cfg):
    """Per session, not a global pause on sweeping."""
    import os
    import time

    service = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )
    busy = start(cfg, "busy")
    idle = start(cfg, "idle")

    assert LocalSessions().claim(cfg, busy, stale_after=3600)
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


def test_a_turns_claim_is_taken_with_the_grace_period_not_the_deadline(cfg, monkeypatch):
    """What `_admit` actually passes, which is the half
    `test_a_claim_survives_the_deadline_that_stops_its_turn` cannot reach: that rule
    supplies `claim_stale_after` by hand at every call, so it holds the constant's own
    property and says nothing about the wiring.

    Measured: reinstating the bug at that call site -- `stale_after=cfg.turn_timeout_s`
    -- left all 2,546 tests green, while the same mutation at `Disposal` reddens
    `test_retention_and_claim_agree_on_when_a_claim_went_stale`. One of the two readers
    of this constant was held and the other was not.
    """
    taken: list[float] = []
    claiming = LocalSessions.claim

    def watched(self, cfg, session_id, /, *, stale_after, now=None):
        taken.append(stale_after)
        return claiming(self, cfg, session_id, stale_after=stale_after, now=now)

    monkeypatch.setattr(LocalSessions, "claim", watched)
    service(cfg).run(Request("go"))

    # Not vacuous, and this is the assertion that makes it so: the two numbers were the
    # same once, and a rule comparing against either would have passed then.
    assert cfg.claim_stale_after > cfg.turn_timeout_s
    assert taken == [cfg.claim_stale_after]


def test_a_claim_survives_the_deadline_that_stops_its_turn(cfg):
    """The window a turn stops *in*, which the claim used to be taken during."""
    import time

    held = start(cfg, "held")
    taken = time.time()
    assert LocalSessions().claim(cfg, held, stale_after=cfg.claim_stale_after, now=taken)

    # The instant the turn runs out of time, and a little after.
    at_deadline = taken + cfg.turn_timeout_s
    assert still_held(
        ((held, taken),), stale_after=cfg.claim_stale_after, now=at_deadline
    ) == (held,)

    # And a second caller is refused for the whole of that window.
    assert not LocalSessions().claim(
        cfg, held, stale_after=cfg.claim_stale_after, now=at_deadline
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


def test_deleting_a_session_takes_its_claim_with_it(cfg, way):
    kf = service(cfg)
    session = start(cfg, "s")
    claim = _claim(cfg, session)
    claim.mkdir(parents=True, exist_ok=True)

    Through(kf, way).delete(session)

    assert not claim.exists()


def test_reopening_a_deleted_id_is_not_refused_as_busy(cfg, way):
    """Why the leftover mattered rather than merely accumulated."""
    kf = service(cfg)
    start(cfg, "reused")
    _claim(cfg, "reused").mkdir(parents=True, exist_ok=True)
    Through(kf, way).delete("reused")

    start(cfg, "reused")

    assert kf.run(Request("go", session_id="reused")).answer == "ok"


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
        replace(cfg, turn_timeout_s=0),
        graph=SlowAgent(steps=5),
        sessions=LocalSessions(),
    )

    result = kf.run(Request("go"), delete_session=True)

    assert result.stop_reason == "max_duration"
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()
    assert result.deletion_failure is None, "a session kept on purpose is not a failure"


def test_a_deletion_that_fails_is_on_the_result_beside_the_answer(cfg, monkeypatch):
    """`run` threw away what `delete_session` answered, so a caller got the answer and no
    sign the session was still there.
    """
    monkeypatch.setattr(LocalSessions, "delete", Stuck.delete)
    kf = service(cfg)

    result = kf.run(Request("go"), delete_session=True)

    assert result.answer == "ok"
    assert result.deletion_failure is not None
    assert "not removed" in result.deletion_failure
    assert (cfg.workspace / "sessions" / result.session_id).is_dir()

"""What a turn is allowed to destroy."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.core.session import retention
from kingfisher.plugins import LocalSessions
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


def test_delete_session_removes_the_directory(cfg):
    """Deleting a session through the service leaves nothing of it on disk."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    session_id = kf.run(Request("go")).session_id

    assert Through(kf).delete(session_id) is None
    assert not (cfg.workspace / "sessions" / session_id).exists()


def test_deleting_an_unknown_session_is_not_an_error(cfg):
    """A caller that retries a delete should not have to care."""
    assert Through(service(cfg)).delete("never-existed") is None


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


class ClaimedRightAfterTheCheck(LocalSessions):
    """A turn that claims its session the instant a sweep has asked whether it is held."""

    tokens: list[str | None]

    def __init__(self) -> None:
        super().__init__()
        self.tokens = []

    def held(self, cfg, session_id, /, *, stale_after, now=None):
        answer = super().held(cfg, session_id, stale_after=stale_after, now=now)
        # Once: `claim` asks `held` of a slot it finds taken.
        if not self.tokens:
            self.mark_used(cfg, session_id)
            self.tokens.append(self.claim(cfg, session_id, stale_after=stale_after))
        return answer


def test_a_turn_claiming_after_the_sweep_asked_keeps_its_session(cfg):
    """`reap` asked `held` and then deleted unclaimed, so a turn claiming between the two
    had its session removed mid-turn."""
    import os
    import time

    sessions = ClaimedRightAfterTheCheck()
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=sessions)
    session = start(cfg, "s")
    directory = cfg.workspace / "sessions" / session
    stale = time.time() - 10_000
    os.utime(directory, (stale, stale))

    result = kf.reap(older_than_seconds=1.0, now=time.time())

    assert sessions.tokens and sessions.tokens[0] is not None, "the turn took its claim"
    assert result.removed == ()
    assert (result.kept, result.busy) == (1, 1), "spared as busy, not as young"
    assert directory.exists()
    assert sessions.held(cfg, session, stale_after=cfg.claim_stale_after)


def test_a_sweep_gives_back_the_claim_it_took_when_the_delete_fails(cfg, monkeypatch):
    """A sweep's own claim left behind would lock a session it could not remove out of
    every turn until the claim went stale."""
    import os
    import time

    monkeypatch.setattr(LocalSessions, "delete", Stuck.delete)
    kf = service(cfg)
    session = start(cfg, "s")
    stale = time.time() - 10_000
    os.utime(cfg.workspace / "sessions" / session, (stale, stale))

    result = kf.reap(older_than_seconds=1.0, now=time.time())

    assert result.failures
    assert not _claim(cfg, session).exists()


class RemovedFirst(LocalSessions):
    """Another sweep removing the session between this one's listing and its claim."""

    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        self.delete(cfg, session_id)
        return super().claim(cfg, session_id, stale_after=stale_after, now=now)


class Unclaimable(LocalSessions):
    def claim(self, cfg, session_id, /, *, stale_after, now=None):
        msg = "the lock store is down"
        raise OSError(msg)


def test_a_session_another_sweep_removed_first_does_not_stop_this_one(cfg):
    """Its claim fails for want of the session, which is what a sweep was after; raised,
    it ended the sweep for every session after it."""
    import os
    import time

    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=RemovedFirst())
    stale = time.time() - 10_000
    for name in ("a", "b"):
        os.utime(cfg.workspace / "sessions" / start(cfg, name), (stale, stale))

    result = kf.reap(older_than_seconds=1.0, now=time.time())

    assert sorted(result.removed) == ["a", "b"]
    assert result.failures == ()


def test_claiming_a_session_that_is_gone_does_not_make_it_again(cfg):
    """A claim making its `.harness` with its parents would leave a session deleted under
    a starting turn listed again, holding nothing but the claim."""
    start(cfg, "kept")

    with pytest.raises(FileNotFoundError):
        LocalSessions().claim(cfg, "gone", stale_after=60)
    assert not (cfg.workspace / "sessions" / "gone").exists()


def test_a_claim_that_fails_on_a_session_still_there_is_raised(cfg):
    """The control: a sweep that read every failed claim as a removal would delete
    nothing and report every session gone."""
    import os
    import time

    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=Unclaimable())
    stale = time.time() - 10_000
    os.utime(cfg.workspace / "sessions" / start(cfg, "s"), (stale, stale))

    with pytest.raises(OSError, match="lock store"):
        kf.reap(older_than_seconds=1.0, now=time.time())
    assert (cfg.workspace / "sessions" / "s").exists()


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
    assert LocalSessions().held(cfg, held, stale_after=cfg.claim_stale_after, now=at_deadline)

    # And a second caller is refused for the whole of that window.
    assert not LocalSessions().claim(
        cfg, held, stale_after=cfg.claim_stale_after, now=at_deadline
    )

    # Long enough after, the slot is takeable again -- a holder that died must
    # not lock the session out for good.
    assert not LocalSessions().held(
        cfg, held, stale_after=cfg.claim_stale_after, now=taken + 1e6
    )


#: Longer than every bound the `cfg` fixture carries, so whichever one a case lengthens
#: is unambiguously the bound the window has to cover.
LONGEST = 900


def _timing_out_after(cfg, seconds):
    """`cfg` with every model bounded at `seconds`, the catalogue otherwise unchanged."""
    bounded = {
        name: replace(profile, timeout_s=seconds)
        for name, profile in cfg.models.models.items()
    }
    return replace(cfg, models=replace(cfg.models, models=bounded))


@pytest.mark.parametrize(
    ("held_by", "lengthen"),
    [
        ("a shell command", lambda cfg: replace(cfg, execution_timeout_s=LONGEST)),
        ("a model call", lambda cfg: _timing_out_after(cfg, LONGEST)),
    ],
)
def test_a_claim_outlives_whatever_held_the_superstep_the_deadline_fell_in(
    cfg, held_by, lengthen
):
    """A turn stops between stream chunks, so a deadline passing inside a superstep
    stops nothing until that superstep ends -- and `execution_timeout_s` bounds one as
    surely as a model's `timeout_s` does.

    The shell command is the bug: `claim_stale_after` added the model timeout alone, so
    raising `execution_timeout_s` for a build or a test suite reopened the overlap the
    constant exists to close. Measured on 2026-10-20, a 6s command under a 1s turn and a
    1s model timeout let a second caller take the session 3.0s into a turn that ran to
    6.0s. The model call is the control, and it is not redundant: the fixture's longest
    model timeout equals the `ModelProfile` default the `max` falls back to, so a rule
    built on the fixture alone holds neither half of it.
    """
    import time

    bounded = lengthen(cfg)
    held = start(bounded, "held")
    taken = time.time()
    sessions = LocalSessions()
    assert sessions.claim(bounded, held, stale_after=bounded.claim_stale_after, now=taken)

    # While the turn's own work can still be running: its deadline, then the superstep
    # that straddled it, inside the bound by the same minute the retention rule above
    # uses. Not the bound exactly -- `held` reads the age off the claim's mtime rather
    # than the `now` it was taken with, so the bound itself falls either side of that
    # `<` by the filesystem's timestamp granularity: asked there, this was green on
    # APFS and red on ext4.
    running = taken + bounded.turn_timeout_s + LONGEST - 60
    assert sessions.held(bounded, held, stale_after=bounded.claim_stale_after, now=running)
    assert not sessions.claim(
        bounded, held, stale_after=bounded.claim_stale_after, now=running
    )

    # The control beside the escape: with that one bound back where the fixture left it,
    # the same instant is past the window -- so the assertions above were carried by the
    # bound this case lengthened and not by another that happens to be long enough.
    assert not sessions.held(cfg, held, stale_after=cfg.claim_stale_after, now=running)


def test_a_claim_outlives_the_deadline_in_a_deployment_that_bounds_nothing(cfg):
    """The window is never the deadline alone, however little a deployment bounds.

    `claim_stale_after` takes the longest bound on a superstep, and every one of them
    can be absent: a catalogue with no models, and no bound on a command. The `max`
    falls back to `ModelProfile.timeout_s` for exactly this, and nothing held that --
    neither here nor under the `default=` this replaced. Zero would make the claim
    takeable the instant the deadline passed, which is the overlap the constant exists
    to close.
    """
    import time

    # Only the two bounds are read here, so an empty catalogue needs no reachable default.
    bounds_nothing = replace(
        cfg, execution_timeout_s=0, models=replace(cfg.models, models={})
    )
    assert bounds_nothing.claim_stale_after > bounds_nothing.turn_timeout_s

    held = start(bounds_nothing, "held")
    taken = time.time()
    sessions = LocalSessions()
    stale_after = bounds_nothing.claim_stale_after
    assert sessions.claim(bounds_nothing, held, stale_after=stale_after, now=taken)

    # The instant the deadline passes, which is when the turn is still stopping.
    passed = taken + bounds_nothing.turn_timeout_s
    assert sessions.held(bounds_nothing, held, stale_after=stale_after, now=passed)
    assert not sessions.claim(bounds_nothing, held, stale_after=stale_after, now=passed)


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

    Through(kf).delete(session)

    assert not claim.exists()


def test_reopening_a_deleted_id_is_not_refused_as_busy(cfg):
    """Why the leftover mattered rather than merely accumulated."""
    kf = service(cfg)
    start(cfg, "reused")
    _claim(cfg, "reused").mkdir(parents=True, exist_ok=True)
    Through(kf).delete("reused")

    start(cfg, "reused")

    assert kf.run(Request("go", session_id="reused")).answer == "ok"


def test_a_claim_is_stale_the_moment_its_age_reaches_stale_after(cfg):
    """Catches `<` becoming `<=`: a claim exactly `stale_after` old is no longer held."""
    import os

    session = start(cfg, "aged")
    assert LocalSessions().claim(cfg, session, stale_after=50.0)
    # Whole seconds, set rather than read: an mtime from the clock would put the
    # boundary between two floats this could not name exactly.
    os.utime(_claim(cfg, session), (1000.0, 1000.0))

    assert LocalSessions().held(cfg, session, stale_after=50.0, now=1049.0)
    assert not LocalSessions().held(cfg, session, stale_after=50.0, now=1050.0)


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

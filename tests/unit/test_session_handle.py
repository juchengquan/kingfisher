"""A session as the caller it was handed to reaches it: made only when asked for, checked
once, and refusing what it cannot do once the session has gone."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher, UnknownSessionError
from kingfisher.core.access.source_ids import Datazone
from kingfisher.core.session.steps import drive
from kingfisher.core.session.store import remember_zones
from kingfisher.plugins import LocalSessions
from tests.conftest import Through, an_agent, harness_of, start
from tests.unit.test_session_files import Counting

#: A caller in the datazone the sessions below were made in.
IN_FINANCE = ("A", Datazone("finance"))


@pytest.fixture
def policied(cfg):
    """`assistant` for source id A only, which switches checking on."""
    an_agent(cfg, "assistant", source_ids="[A]")
    return cfg


def _in_finance(kf, session_id: str = "mine") -> str:
    """A session a `finance` turn ran in, out of reach of a caller in no datazone."""
    start(kf.cfg, session_id)
    drive(remember_zones(harness_of(kf.cfg, session_id), frozenset({"finance"})))
    return session_id


def _call(handle, method: str, *args):
    return getattr(handle, method)(*args)


def test_asking_makes_a_session_only_when_told_to(cfg):
    """A lookup that made the session it asked about would be a write nobody asked for,
    and an empty session listed and kept for a typo."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    reads = Through(kf)

    assert reads.session("fresh") is None
    assert kf.sessions() == ()

    made = reads.session("fresh", create=True)

    assert made is not None
    assert made.id == "fresh"
    assert [s.id for s in kf.sessions()] == ["fresh"]


def test_create_does_not_reveal_a_session_out_of_reach(policied):
    """A handle for a session that exists and is someone else's would confirm the id,
    which is what answering `None` for both is for."""
    kf = Kingfisher(policied, sessions=LocalSessions())
    session_id = _in_finance(kf)
    reads = Through(kf)

    assert reads.session(session_id, source_ids=("A",), create=True) is None
    assert reads.session(session_id, source_ids=IN_FINANCE, create=True) is not None


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("pending", ()),
        ("artifact", ("outputs/a.txt",)),
        ("result", ("A-0001",)),
        ("files", ()),
        ("size", ()),
    ],
    ids=["pending", "artifact", "result", "files", "size"],
)
def test_a_handle_whose_session_has_gone_answers_as_a_wrong_id_does(cfg, method, args):
    """Read through the backend it kept, it found no pause and no files; opened again,
    it would have made the session it had been told was gone."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "gone")
    handle = kf.session("gone")
    handle.files()
    assert handle.delete() is None

    with pytest.raises(UnknownSessionError):
        _call(handle, method, *args)
    assert kf.sessions() == (), "a read made the session again"


def test_deleting_twice_is_not_an_error(cfg):
    """A caller that retries a delete should not have to care."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "twice")
    handle = kf.session("twice")

    assert _call(handle, "delete") is None
    assert _call(handle, "delete") is None


def test_the_listing_narrows_to_a_caller_only_when_one_is_named(policied):
    """Naming nobody is the operator's view of every session; naming a caller leaves out
    a session in datazones that are not theirs, as asking for it by id does."""
    kf = Kingfisher(policied, sessions=LocalSessions())
    mine = _in_finance(kf)
    anyones = start(policied, "open")

    def listed(**kwargs) -> set[str]:
        found = kf.sessions(**kwargs)
        return {s.id for s in found}

    assert listed() == {mine, anyones}
    assert listed(source_ids=("A",)) == {anyones}
    assert listed(source_ids=IN_FINANCE) == {mine, anyones}


def test_a_session_s_size_is_its_backend_s_count(cfg):
    """The listing the command prints sizes beside reads this."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "s")
    (cfg.workspace / "sessions" / "s" / "outputs" / "a.txt").write_text("x" * 100)

    assert Through(kf).size("s") >= 100


def test_a_narrowed_read_opens_the_session_once(policied):
    """Deciding whether the caller reaches the session opens its backend; reading through
    a second opening was a second sandbox per query on a remote store."""
    counting = Counting()
    kf = Kingfisher(policied, sessions=counting)
    session_id = _in_finance(kf)
    counting.asked.clear()

    Through(kf).pending(session_id, source_ids=IN_FINANCE)

    assert counting.asked == [session_id]

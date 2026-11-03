"""A session handle: made only when asked for, and refusing what it cannot do once the
session has gone."""

from __future__ import annotations

import pytest

from kingfisher import Kingfisher, UnknownSessionError
from kingfisher.plugins import LocalSessions
from tests.conftest import Through, start


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


def test_a_session_s_size_is_its_backend_s_count(cfg):
    """The listing the command prints sizes beside reads this."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "s")
    (cfg.workspace / "sessions" / "s" / "outputs" / "a.txt").write_text("x" * 100)

    assert Through(kf).size("s") >= 100

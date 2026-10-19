"""A session as the caller it was handed to reaches it: made only when asked for, checked
once, and refusing what it cannot do once the session has gone."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
import yaml

from kingfisher import Kingfisher, UnknownSessionError
from kingfisher.core.access.policy import parse
from kingfisher.plugins import LocalSessions
from tests.conftest import Through, an_agent, pin, start
from tests.unit.test_session_files import Counting

VOCABULARY = "source_ids: [A, B]\n"


@pytest.fixture
def policied(cfg):
    """`assistant` for source id A only, under a vocabulary naming A and B."""
    an_agent(cfg, "assistant", source_ids="[A]")
    return replace(cfg, access=parse(yaml.safe_load(VOCABULARY), source="source_ids.yaml"))


def _pinned_to_a(kf, session_id: str = "mine") -> str:
    start(kf.cfg, session_id)
    pin(kf, session_id, "assistant")
    return session_id


def _call(handle, way: str, method: str, *args):
    if way == "async":
        return asyncio.run(getattr(handle, f"a{method}")(*args))
    return getattr(handle, method)(*args)


def test_asking_makes_a_session_only_when_told_to(cfg, way):
    """A lookup that made the session it asked about would be a write nobody asked for,
    and an empty session listed and kept for a typo."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    reads = Through(kf, way)

    assert reads.session("fresh") is None
    assert kf.sessions() == ()

    made = reads.session("fresh", create=True)

    assert made is not None
    assert made.id == "fresh"
    assert [s.id for s in kf.sessions()] == ["fresh"]


def test_create_does_not_reveal_a_session_out_of_reach(policied, way):
    """A handle for a session that exists and is someone else's would confirm the id,
    which is what answering `None` for both is for."""
    kf = Kingfisher(policied, sessions=LocalSessions())
    session_id = _pinned_to_a(kf)
    reads = Through(kf, way)

    assert reads.session(session_id, source_ids=("B",), create=True) is None
    assert reads.session(session_id, source_ids=("A",), create=True) is not None


@pytest.mark.parametrize(
    ("method", "args"),
    [("pending", ()), ("artifact", ("outputs/a.txt",)), ("files", ()), ("size", ())],
    ids=["pending", "artifact", "files", "size"],
)
def test_a_handle_whose_session_has_gone_answers_as_a_wrong_id_does(cfg, way, method, args):
    """Read through the backend it kept, it found no pause and no files; opened again,
    it would have made the session it had been told was gone."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "gone")
    handle = kf.session("gone")
    handle.files()
    assert handle.delete() is None

    with pytest.raises(UnknownSessionError):
        _call(handle, way, method, *args)
    assert kf.sessions() == (), "a read made the session again"


def test_deleting_twice_is_not_an_error(cfg, way):
    """A caller that retries a delete should not have to care."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "twice")
    handle = kf.session("twice")

    assert _call(handle, way, "delete") is None
    assert _call(handle, way, "delete") is None


def test_the_listing_narrows_to_a_caller_only_when_one_is_named(policied, way):
    """Naming nobody is the operator's view of every session; naming a caller leaves out
    a session pinned to an agent they cannot run, as asking for it by id does."""
    kf = Kingfisher(policied, sessions=LocalSessions())
    mine = _pinned_to_a(kf)
    anyones = start(policied, "open")

    def listed(**kwargs) -> set[str]:
        found = asyncio.run(kf.asessions(**kwargs)) if way == "async" else kf.sessions(**kwargs)
        return {s.id for s in found}

    assert listed() == {mine, anyones}
    assert listed(source_ids=("B",)) == {anyones}
    assert listed(source_ids=("A",)) == {mine, anyones}


def test_a_session_s_size_is_its_backend_s_count(cfg, way):
    """Both ways, since the listing the command prints sizes beside reads this."""
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "s")
    (cfg.workspace / "sessions" / "s" / "outputs" / "a.txt").write_text("x" * 100)

    assert Through(kf, way).size("s") >= 100


def test_a_narrowed_read_opens_the_session_once(policied, way):
    """Deciding whether the caller reaches the session opens its backend; reading through
    a second opening was a second sandbox per query on a remote store."""
    counting = Counting()
    kf = Kingfisher(policied, sessions=counting)
    session_id = _pinned_to_a(kf)
    counting.asked.clear()

    Through(kf, way).pending(session_id, source_ids=("A",))

    assert counting.asked == [session_id]

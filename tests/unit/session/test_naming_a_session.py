"""Which session a turn runs in: the one its request names, or `default`."""

from __future__ import annotations

import time

import pytest

from kingfisher import Kingfisher, Request, UnknownSessionError
from kingfisher.cli.__main__ import main
from kingfisher.core.layout import DEFAULT_LAYOUT, HARNESS
from kingfisher.core.request import Decision, Resume
from kingfisher.plugins import LocalSessions
from tests.conftest import Through
from tests.unit.service.test_files_for import DOORS, _turn
from tests.unit.stand_ins import StubAgent

DEFAULT = "default"


def _service(cfg) -> Kingfisher:
    return Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )


def _sent(kf: Kingfisher) -> str:
    """Everything the graph was sent on its last turn, as one string."""
    return " ".join(str(getattr(m, "content", m)) for m in kf._graph.state["messages"])


# -- a request naming none --------------------------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_a_request_naming_no_session_runs_in_default_and_carries_it_on(cfg, door):
    """Catches a request naming no session starting one of its own each time, which
    leaves a caller who wanted one conversation an id to carry from turn to turn -- on
    the async doors as well as the sync ones.
    """
    kf = _service(cfg)

    first = _turn(kf, door, Request(task="the number is forty"))
    again = _turn(kf, door, Request(task="and now?"))

    assert (first.session_id, again.session_id) == (DEFAULT, DEFAULT)
    assert "forty" in _sent(kf), "the second turn did not continue the first's conversation"
    folder = cfg.workspace / "sessions" / DEFAULT
    assert {p.name for p in folder.iterdir()} >= {*DEFAULT_LAYOUT.session_dirs, HARNESS}


def test_naming_default_reaches_the_same_session(cfg):
    """`default` is an ordinary id, so naming it is the session naming none runs in."""
    kf = _service(cfg)
    kf.run(Request(task="the number is forty"))

    result = kf.run(Request(task="and now?", session_id=DEFAULT))

    assert result.session_id == DEFAULT
    assert "forty" in _sent(kf)


def test_the_default_session_is_listed_and_reaped_like_any_other(cfg):
    """Catches `default` being kept out of the listing -- and so out of `reap` -- which
    would leave its conversation and files to grow for as long as the workspace lives.
    """
    kf = _service(cfg)
    kf.run(Request(task="the number is forty"))

    assert [info.id for info in kf.sessions()] == [DEFAULT]
    assert kf.reap(now=time.time()).removed == (DEFAULT,)
    assert kf.run(Request(task="and now?")).session_id == DEFAULT
    assert "forty" not in _sent(kf), "the conversation outlived its session"


# -- a request naming one ----------------------------------------------------


@pytest.mark.parametrize("door", DOORS)
def test_a_first_turn_naming_an_id_starts_that_session(cfg, door):
    """Catches an id nobody has used being refused on any door, which left a caller with
    ids of its own -- a chat thread, a ticket -- a table from its ids to kingfisher's.
    """
    result = _turn(_service(cfg), door, Request(task="go", session_id="q3-report"))

    assert result.session_id == "q3-report"
    assert (cfg.workspace / "sessions" / "q3-report" / HARNESS).is_dir()


def test_two_ids_are_two_conversations(cfg):
    """Catches two named sessions sharing a conversation -- or a named one carrying on
    `default`'s.
    """
    kf = _service(cfg)
    kf.run(Request(task="the number is forty"))

    named = kf.run(Request(task="and now?", session_id="another"))

    assert named.session_id == "another"
    assert "forty" not in _sent(kf), "a named session carried on default's conversation"


def test_reading_a_session_nobody_has_made_answers_no_session_and_makes_none(cfg):
    """Catches a read or a delete making the session it asked about, which would be a
    write nobody asked for -- and an empty session listed and kept for a typo.
    """
    kf = _service(cfg)

    assert kf.session("never-made") is None
    with pytest.raises(UnknownSessionError, match="no session 'never-made'"):
        Through(kf).pending("never-made")
    assert Through(kf).delete("never-made") is None

    assert kf.sessions() == ()
    assert not (cfg.workspace / "sessions" / "never-made").exists()


def test_a_resume_naming_a_session_nobody_has_made_is_refused_and_makes_none(cfg):
    """Catches a resume opening -- and so making -- the session it names, only to find
    nothing waiting there: a resume answers a turn that stopped.
    """
    kf = _service(cfg)

    with pytest.raises(UnknownSessionError, match="no session 'never-made'"):
        kf.run(Resume(session_id="never-made", decisions=(Decision("call-1", "approve"),)))

    assert not (cfg.workspace / "sessions" / "never-made").exists()


def test_a_hidden_folder_in_sessions_is_not_a_session(cfg):
    """Catches a dot-named folder -- an editor's, a shell's -- being listed as a session
    that no request could name, and that `reap` would then stop at.
    """
    kf = _service(cfg)
    kf.run(Request(task="go", session_id="seen"))
    (cfg.workspace / "sessions" / ".stray").mkdir()

    assert [info.id for info in kf.sessions()] == ["seen"]


def test_reaping_by_name_at_the_command_line_removes_it(at_the_command_line, capsys):
    """`reap --session` looks the id up before deleting, and the id is one a request
    named."""
    _service(at_the_command_line).run(Request(task="go", session_id="named"))

    assert main(["reap", "--session", "named"]) == 0

    assert "reaped named" in capsys.readouterr().out
    assert not (at_the_command_line.workspace / "sessions" / "named").exists()


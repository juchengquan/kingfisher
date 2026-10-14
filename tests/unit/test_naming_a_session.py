"""Which session a turn runs in: the one its request names, or `default`."""

from __future__ import annotations

import os
import platform
import time

import pytest

from kingfisher import Kingfisher, Request, UnknownSessionError, default_backends
from kingfisher.domain.request import Decision, Resume
from kingfisher.layout import HARNESS, PINNED_AGENT, SESSION_DIRS
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import StubCheckpointer
from tests.unit.test_files_for import DOORS, _turn
from tests.unit.test_run import StubAgent

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

DEFAULT = "default"


def _service(cfg) -> Kingfisher:
    return Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
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
    assert {p.name for p in folder.iterdir()} >= {*SESSION_DIRS, HARNESS}


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
    assert kf.reap(older_than_seconds=0, now=time.time()).removed == (DEFAULT,)
    assert kf.run(Request(task="and now?")).session_id == DEFAULT
    assert "forty" not in _sent(kf), "the conversation outlived its session"


def test_the_idle_clock_is_the_last_turn(cfg):
    """`mark_used` touching the session's folder is its clock, so a turn has to move it
    and a sweep has to read it -- or a session in daily use is reaped as idle.
    """
    kf = _service(cfg)
    kf.run(Request(task="go"))
    os.utime(cfg.workspace / "sessions" / DEFAULT, (1_000, 1_000))

    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == (DEFAULT,)
    kf.run(Request(task="back again"))
    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == ()


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

    with pytest.raises(UnknownSessionError, match="no session 'never-made'"):
        kf.pending("never-made")
    with pytest.raises(UnknownSessionError, match="no session 'never-made'"):
        kf.artifact("never-made", "outputs/report.md")
    assert kf.delete_session("never-made") is None

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


# -- the shell, which no file tool rule reaches ---------------------------


@macos
def test_the_shell_cannot_write_or_remove_its_session_s_harness(cfg):
    """Catches a session's pinned agent, conversation and paused turn being left to its
    own shell."""
    backend = default_backends.open(cfg, DEFAULT)
    harness = cfg.workspace / "sessions" / DEFAULT / HARNESS
    pinned = harness / PINNED_AGENT
    pinned.write_text("name: pinned\n", encoding="utf-8")

    backend.execute(f'printf "name: mine" > "{pinned}"')
    backend.execute(f'rm -f "{pinned}"; mv "{harness}" "{harness}.gone"; rm -rf "{harness}"')

    assert harness.is_dir()
    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_the_shell_can_still_write_the_rest_of_its_session(cfg):
    """The bound on the rule: `.harness` one level down is the session's own folder with
    a name, not the harness."""
    backend = default_backends.open(cfg, DEFAULT)

    assert backend.execute("echo fine > outputs/ok.txt").exit_code == 0
    nested = "mkdir -p outputs/.harness && echo fine > outputs/.harness/ok"
    assert backend.execute(nested).exit_code == 0


@macos
def test_a_shell_cannot_make_sessions_harness(cfg, session_dir):
    """Made, it is a folder sitting where sessions are, under the name the harness rule
    guards."""
    backend = default_backends.open(cfg, session_dir.name)

    outcome = backend.execute(f'mkdir "{cfg.workspace / "sessions" / HARNESS}"')

    assert outcome.exit_code != 0
    assert not (cfg.workspace / "sessions" / HARNESS).exists()


# -- the enclosing folder cannot be moved out from under the harness rule --------


def _pin(workspace, *parts):
    harness = workspace.joinpath(*parts, HARNESS)
    harness.mkdir(parents=True, exist_ok=True)
    pinned = harness / PINNED_AGENT
    pinned.write_text("name: pinned\n", encoding="utf-8")
    return pinned


@macos
def test_the_shell_cannot_relocate_sessions_to_reach_a_harness(cfg, session_dir):
    """The measured gap: the harness is denied by where it sits, so moving `sessions/`
    aside slid it out from under the rule, let the pin be rewritten, and moved it back.
    """
    backend = default_backends.open(cfg, session_dir.name)
    pinned = _pin(cfg.workspace, "sessions", session_dir.name)
    ws = cfg.workspace

    backend.execute(
        f'mv "{ws}/sessions" "{ws}/moved" '
        f'&& printf "name: mine" > "{ws}/moved/{session_dir.name}/{HARNESS}/{PINNED_AGENT}"; '
        f'mv "{ws}/moved" "{ws}/sessions"'
    )

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_moving_one_session_out_of_sessions_does_not_free_its_harness(cfg, session_dir):
    """A session folder carried out of `sessions/` would take its `.harness` to a path
    the rule no longer matches.
    """
    backend = default_backends.open(cfg, session_dir.name)
    pinned = _pin(cfg.workspace, "sessions", session_dir.name)
    ws, sid = cfg.workspace, session_dir.name

    backend.execute(
        f'mv "{ws}/sessions/{sid}" "{ws}/out" '
        f'&& printf "name: mine" > "{ws}/out/{HARNESS}/{PINNED_AGENT}"; '
        f'mv "{ws}/out" "{ws}/sessions/{sid}"'
    )

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_denying_the_move_leaves_the_session_s_own_work_writable(cfg, session_dir):
    """The bound: the node is denied, not its contents, so the session keeps all of its
    own folders to write in. A deny that stopped those would be a worse rule.
    """
    backend = default_backends.open(cfg, session_dir.name)

    assert backend.execute("echo fine > outputs/ok.txt").exit_code == 0
    nested = backend.execute("mkdir -p outputs/nested && echo fine > outputs/nested/ok")
    assert nested.exit_code == 0


@macos
def test_the_shell_cannot_fabricate_a_sibling_session(cfg, session_dir):
    """Laying down a session folder is the harness's to do, unconfined, not the agent's
    -- and a shell that could would also be a shell that could move one.
    """
    backend = default_backends.open(cfg, session_dir.name)

    assert backend.execute(f'mkdir "{cfg.workspace / "sessions" / "newcomer"}"').exit_code != 0


@macos
def test_a_link_from_a_writable_folder_does_not_reach_the_harness(cfg):
    """Measured closed, and pinned so it stays closed: a hard or soft link made in a
    writable folder is not a way to write the pin the `.harness` rule guards.
    """
    backend = default_backends.open(cfg, DEFAULT)
    pinned = _pin(cfg.workspace, "sessions", DEFAULT)
    target = cfg.workspace / "sessions" / DEFAULT / HARNESS / PINNED_AGENT

    backend.execute(f'ln "{target}" outputs/hard 2>/dev/null; printf "name: mine" > outputs/hard')
    soft = f'ln -s "{target}" outputs/soft 2>/dev/null; printf "name: mine" > outputs/soft'
    backend.execute(soft)

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_a_session_can_reorganize_its_own_folders(cfg):
    """The rule names the session's folder node and not what it holds, so the session's
    own directories -- the harness aside -- can still be made, renamed and removed."""
    backend = default_backends.open(cfg, DEFAULT)

    out = backend.execute("mkdir work && mv work renamed && rmdir renamed && echo ok")

    assert out.exit_code == 0 and "ok" in out.output

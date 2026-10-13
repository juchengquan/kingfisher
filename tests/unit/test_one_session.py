"""A process serving one session, kept in `sessions/<id>/` like any other."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import replace

import pytest

from kingfisher import Kingfisher, Request, UnknownSessionError, default_backends
from kingfisher.domain.session import unknown_session
from kingfisher.infrastructure.workspace import ensure_session_layout
from kingfisher.layout import HARNESS, PINNED_AGENT, SESSION_DIRS
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import StubCheckpointer, Through
from tests.unit.test_files_for import DOORS, _turn
from tests.unit.test_run import StubAgent

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

THE_ONE = "the-one"
ANOTHER = "another"


@pytest.fixture
def one(cfg):
    """`cfg`, for a process configured to serve one session."""
    return replace(cfg, session_id=THE_ONE)


def _service(cfg) -> Kingfisher:
    return Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )


def _sent(kf: Kingfisher) -> str:
    """Everything the graph was sent on its last turn, as one string."""
    return " ".join(str(getattr(m, "content", m)) for m in kf._graph.state["messages"])


# -- where it is, and what is listed --------------------------------------


def test_the_one_session_has_a_folder_named_for_it(one):
    """Catches the one session being laid out anywhere but `sessions/<id>/` -- in
    `sessions/` itself, as it once was, where no other process could keep a session
    beside it and nothing on disk said which session it was.
    """
    default_backends.open(one, THE_ONE)

    sessions = one.workspace / "sessions"
    assert [p.name for p in sessions.iterdir()] == [THE_ONE]
    assert {p.name for p in (sessions / THE_ONE).iterdir()} == {*SESSION_DIRS, HARNESS}


def test_the_one_session_is_listed_before_anything_has_run(one):
    """Listed only once a turn had made it, a first turn naming it was refused as an id
    nobody issued -- though the process's own setting issued it.
    """
    assert [info.id for info in Kingfisher(one, backends=default_backends).sessions()] == [
        THE_ONE
    ]


@pytest.mark.parametrize("door", DOORS)
def test_a_first_turn_may_name_the_configured_id(one, door):
    """The session exists because the process says so, so naming it on the first turn is
    naming an issued id."""
    result = _turn(_service(one), door, Request(task="go", session_id=THE_ONE))

    assert result.session_id == THE_ONE
    assert (one.workspace / "sessions" / THE_ONE / HARNESS).is_dir()


@pytest.mark.parametrize("door", DOORS)
def test_a_turn_naming_no_session_runs_in_the_one(one, door):
    """A missing id minted a fresh one, and a process serving one session has nowhere to
    put a second -- on the async doors as well as the sync ones.
    """
    kf = _service(one)

    first = _turn(kf, door, Request(task="the number is forty"))
    again = _turn(kf, door, Request(task="and now?"))

    assert (first.session_id, again.session_id) == (THE_ONE, THE_ONE)
    assert "forty" in _sent(kf), "the second turn did not continue the first's conversation"


@pytest.mark.parametrize("door", DOORS)
def test_another_id_is_refused_as_an_unissued_one_is(one, door):
    """The process serves one session, so any other id is one nobody issued, and is
    answered in the words that id gets everywhere else."""
    with pytest.raises(UnknownSessionError) as refused:
        _turn(_service(one), door, Request(task="go", session_id=ANOTHER))

    assert str(refused.value) == str(unknown_session(ANOTHER))
    assert not (one.workspace / "sessions" / ANOTHER).exists()


@pytest.mark.parametrize("asked", ["open", "mark_used", "size", "delete"])
def test_the_backends_know_no_other_id(one, asked):
    """Asked directly, as a deployment may: another id would open, touch, measure or
    delete a session this process does not serve -- possibly another process's.
    """
    ensure_session_layout(one.workspace / "sessions" / ANOTHER)
    kept = one.workspace / "sessions" / ANOTHER / "outputs" / "kept.txt"
    kept.write_text("still here", encoding="utf-8")

    with pytest.raises(UnknownSessionError):
        getattr(default_backends, asked)(one, ANOTHER)

    assert kept.read_text(encoding="utf-8") == "still here"


def test_a_turn_handed_its_files_and_no_id_runs_in_the_one(one):
    """Refused as a turn that would mint a session and run in another's files, which in
    this process no turn can: naming no session names the one there is."""
    kf = _service(one)

    result = kf.run(Request(task="go"), files=kf.files_for(THE_ONE))

    assert result.session_id == THE_ONE


def test_files_for_reaches_the_one_session(one, way):
    """Before any turn, as a caller placing files ahead of the first one would."""
    kf = Kingfisher(one, backends=default_backends)

    files = Through(kf, way).files_for(THE_ONE)
    files.upload_files([("/outputs/placed.txt", b"placed")])

    placed = one.workspace / "sessions" / THE_ONE / "outputs" / "placed.txt"
    assert placed.read_bytes() == b"placed"


# -- going, and coming back -----------------------------------------------


def _delete_session(kf: Kingfisher) -> None:
    assert kf.delete_session(THE_ONE) is None


def _delete_after_the_turn(kf: Kingfisher) -> None:
    assert kf.run(Request(task="done"), delete_session=True).deletion_failure is None


def _reap_it(kf: Kingfisher) -> None:
    assert kf.reap(older_than_seconds=0, now=time.time()).removed == (THE_ONE,)


@pytest.mark.parametrize("deleting", [_delete_session, _delete_after_the_turn, _reap_it])
def test_a_deleted_session_comes_back_empty_under_the_same_id(one, tmp_path, deleting):
    """Catches a delete leaving the one session's folder or conversation behind, or
    taking its id off the listing -- which would refuse the next turn as one naming an id
    nobody issued.

    With a read-only input placed, so the folder removed is one holding something a
    plain `rmtree` cannot delete.
    """
    placed = tmp_path / "numbers.csv"
    placed.write_text("forty\n", encoding="utf-8")
    kf = _service(one)
    kf.run(Request(task="the number is forty", inputs=(placed,)))

    deleting(kf)

    assert not (one.workspace / "sessions" / THE_ONE).exists()
    assert [info.id for info in kf.sessions()] == [THE_ONE]
    assert kf.run(Request(task="and now?")).session_id == THE_ONE
    assert "forty" not in _sent(kf), "the conversation outlived the session"


def test_deleting_a_session_no_turn_has_made_is_not_a_failure(one):
    """The id is listed before any turn has made its folder, so a sweep asks to delete
    one that is not there -- and a failure reported for that would be reported forever.
    """
    kf = _service(one)

    assert kf.delete_session(THE_ONE) is None
    assert kf.reap(older_than_seconds=0, now=time.time()).failures == ()


def test_reaping_by_name_at_the_command_line_removes_it(at_the_command_line, monkeypatch, capsys):
    """`reap --session` looks the id up before deleting, so a listing of folders alone
    would answer "no session" for one no turn had made -- read from the environment, as a
    deployment sets it.
    """
    monkeypatch.setenv("KINGFISHER_SESSION_ID", THE_ONE)
    default_backends.open(replace(at_the_command_line, session_id=THE_ONE), THE_ONE)

    assert main(["reap", "--session", THE_ONE]) == 0

    assert f"reaped {THE_ONE}" in capsys.readouterr().out
    assert not (at_the_command_line.workspace / "sessions" / THE_ONE).exists()


def test_the_idle_clock_is_the_last_turn(one):
    """`mark_used` touching the session's folder is its clock, so a turn has to move it
    and a sweep has to read it -- or a session in daily use is reaped as idle.
    """
    kf = _service(one)
    kf.run(Request(task="go"))
    os.utime(one.workspace / "sessions" / THE_ONE, (1_000, 1_000))

    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == (THE_ONE,)
    kf.run(Request(task="back again"))
    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == ()
    (info,) = kf.sessions()
    assert time.time() - info.last_used < 60


def test_the_command_lists_the_one_session(at_the_command_line, monkeypatch, capsys):
    """Before any turn, where a listing of `sessions/`'s folders would say there were
    none."""
    monkeypatch.setenv("KINGFISHER_SESSION_ID", THE_ONE)

    assert main(["sessions", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    default_backends.open(replace(at_the_command_line, session_id=THE_ONE), THE_ONE)
    assert main(["sessions", "--json"]) == 0
    after = json.loads(capsys.readouterr().out)

    assert [held["id"] for held in before["sessions"]] == [THE_ONE]
    assert [held["id"] for held in after["sessions"]] == [THE_ONE]


# -- a workspace other processes share ----------------------------------------


def test_sessions_other_processes_keep_beside_it_are_left_alone(one):
    """Catches a process serving one session refusing to start beside sessions other
    processes keep in the same workspace, or listing and reaping them as its own.
    """
    neighbour = ensure_session_layout(one.workspace / "sessions" / "abc123")
    kf = _service(one)
    kf.run(Request(task="go"))

    assert [info.id for info in kf.sessions()] == [THE_ONE]
    assert kf.reap(older_than_seconds=0, now=time.time()).removed == (THE_ONE,)
    assert neighbour.is_dir()


def test_unsetting_the_id_lists_the_session_with_the_rest(cfg, one):
    """Catches a workspace one process served refusing to start once the setting is
    gone, or hiding that session: it is a workspace of many, and its sessions are listed
    and swept like any other's.
    """
    default_backends.open(one, THE_ONE)
    default_backends.open(cfg, "abc123")

    kf = Kingfisher(cfg, backends=default_backends)

    assert sorted(info.id for info in kf.sessions()) == ["abc123", THE_ONE]


def test_a_changed_id_starts_a_session_of_its_own(one):
    """Catches a changed `KINGFISHER_SESSION_ID` carrying the old session's conversation,
    agent and memory on as its own -- what mapping every id onto one folder did -- or
    disturbing the old session's folder.
    """
    _service(one).run(Request(task="the number is forty"))
    before = one.workspace / "sessions" / THE_ONE / HARNESS
    held = {p.name: p.read_bytes() for p in before.iterdir()}

    other = _service(replace(one, session_id=ANOTHER))
    result = other.run(Request(task="and now?"))

    assert result.session_id == ANOTHER
    assert "forty" not in _sent(other), "the new id carried on the old conversation"
    assert {p.name: p.read_bytes() for p in before.iterdir()} == held


def test_a_restart_continues_the_one_session(one):
    """The control beside the changed id: the same id after a restart is the same
    session, conversation and all."""
    _service(one).run(Request(task="the number is forty"))

    again = _service(one)
    again.run(Request(task="and now?"))

    assert "forty" in _sent(again), "the restart did not continue the conversation"


# -- the shell, which no file tool rule reaches ---------------------------


@macos
def test_the_shell_cannot_write_or_remove_the_one_session_s_harness(one):
    """Catches the session a process serves alone being left out of the rule that keeps
    every session's pinned agent, conversation and paused turn from its own shell.
    """
    backend = default_backends.open(one, THE_ONE)
    harness = one.workspace / "sessions" / THE_ONE / HARNESS
    pinned = harness / PINNED_AGENT
    pinned.write_text("name: pinned\n", encoding="utf-8")

    backend.execute(f'printf "name: mine" > "{pinned}"')
    backend.execute(f'rm -f "{pinned}"; mv "{harness}" "{harness}.gone"; rm -rf "{harness}"')

    assert harness.is_dir()
    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_the_shell_can_still_write_the_rest_of_the_one_session(one):
    """The bound on the rule: `.harness` one level down is the session's own folder with
    a name, not the harness."""
    backend = default_backends.open(one, THE_ONE)

    assert backend.execute("echo fine > outputs/ok.txt").exit_code == 0
    nested = "mkdir -p outputs/.harness && echo fine > outputs/.harness/ok"
    assert backend.execute(nested).exit_code == 0


@macos
def test_a_shell_cannot_make_sessions_harness(cfg, session_dir):
    """Made, it is a folder the listing reads as a session of its own, which no turn
    issued and `reap` would sweep."""
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
def test_a_link_from_a_writable_folder_does_not_reach_the_harness(one):
    """Measured closed, and pinned so it stays closed: a hard or soft link made in a
    writable folder is not a way to write the pin the `.harness` rule guards.
    """
    backend = default_backends.open(one, THE_ONE)
    pinned = _pin(one.workspace, "sessions", THE_ONE)
    target = one.workspace / "sessions" / THE_ONE / HARNESS / PINNED_AGENT

    backend.execute(f'ln "{target}" outputs/hard 2>/dev/null; printf "name: mine" > outputs/hard')
    soft = f'ln -s "{target}" outputs/soft 2>/dev/null; printf "name: mine" > outputs/soft'
    backend.execute(soft)

    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_the_one_session_can_reorganize_its_own_folders(one):
    """The rule names the session's folder node and not what it holds, so the session's
    own directories -- the harness aside -- can still be made, renamed and removed."""
    backend = default_backends.open(one, THE_ONE)

    out = backend.execute("mkdir work && mv work renamed && rmdir renamed && echo ok")

    assert out.exit_code == 0 and "ok" in out.output

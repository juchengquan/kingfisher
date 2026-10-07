"""A workspace that holds one session, laid out in `sessions/` itself."""

from __future__ import annotations

import json
import os
import platform
import time
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher import Kingfisher, Request, UnknownSessionError, default_backends
from kingfisher.config import ConfigError
from kingfisher.domain.session import unknown_session
from kingfisher.infrastructure.workspace import ensure_layout, ensure_session_layout
from kingfisher.infrastructure.workspace.layout import check_sessions
from kingfisher.layout import HARNESS, PINNED_AGENT, SESSION_DIRS, SESSION_ID_RECORD
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import StubCheckpointer, Through
from tests.unit.test_files_for import DOORS, _turn
from tests.unit.test_harness_directory import _drive
from tests.unit.test_run import StubAgent

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

THE_ONE = "the-one"


@pytest.fixture
def one(cfg):
    """`cfg`, for a workspace configured to hold one session."""
    return replace(cfg, session_id=THE_ONE)


def _service(cfg) -> Kingfisher:
    return Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )


def _sent(kf: Kingfisher) -> str:
    """Everything the graph was sent on its last turn, as one string."""
    return " ".join(str(getattr(m, "content", m)) for m in kf._graph.state["messages"])


# -- where it is, and what is listed --------------------------------------


def test_the_session_is_laid_out_in_sessions_itself(one):
    """Mapped as any other id, the one session went to `sessions/<id>/`, a level the
    service supplying the id never asked for."""
    default_backends.open(one, THE_ONE)

    sessions = one.workspace / "sessions"
    assert {p.name for p in sessions.iterdir()} == {*SESSION_DIRS, HARNESS}


def test_the_one_session_is_listed_before_anything_has_run(one):
    """Listed only once a turn had made it, a first turn naming it was refused as an id
    nobody issued -- though the workspace's own setting issued it.
    """
    assert [info.id for info in Kingfisher(one, backends=default_backends).sessions()] == [
        THE_ONE
    ]


def test_its_own_folders_are_never_listed_as_sessions(one):
    """Listed as a folder of sessions, `inputs/` and `memory/` are sessions of their own,
    and `reap` deletes them one at a time.
    """
    kf = _service(one)
    kf.run(Request(task="go"))

    assert [info.id for info in kf.sessions()] == [THE_ONE]


@pytest.mark.parametrize("door", DOORS)
def test_a_first_turn_may_name_the_configured_id(one, door):
    """The session exists because the workspace says so, so naming it on the first turn
    is naming an issued id."""
    result = _turn(_service(one), door, Request(task="go", session_id=THE_ONE))

    assert result.session_id == THE_ONE
    assert (one.workspace / "sessions" / HARNESS).is_dir()


@pytest.mark.parametrize("door", DOORS)
def test_a_turn_naming_no_session_runs_in_the_one(one, door):
    """A missing id minted a fresh one, and a workspace holding one session has nowhere
    to put a second -- on the async doors as well as the sync ones.
    """
    kf = _service(one)

    first = _turn(kf, door, Request(task="the number is forty"))
    again = _turn(kf, door, Request(task="and now?"))

    assert (first.session_id, again.session_id) == (THE_ONE, THE_ONE)
    assert "forty" in _sent(kf), "the second turn did not continue the first's conversation"


@pytest.mark.parametrize("door", DOORS)
def test_another_id_is_refused_as_an_unissued_one_is(one, door):
    """The workspace holds one session, so any other id is one nobody issued, and is
    answered in the words that id gets everywhere else."""
    with pytest.raises(UnknownSessionError) as refused:
        _turn(_service(one), door, Request(task="go", session_id="another"))

    assert str(refused.value) == str(unknown_session("another"))
    assert not (one.workspace / "sessions" / "another").exists()


@pytest.mark.parametrize("asked", ["open", "mark_used", "size", "delete"])
def test_the_backends_know_no_other_id(one, asked):
    """Asked directly, as a deployment may: mapped to `sessions/` whatever it was called,
    another id would be the one session -- and `delete` given one would empty it.
    """
    default_backends.open(one, THE_ONE)
    kept = one.workspace / "sessions" / "outputs" / "kept.txt"
    kept.write_text("still here", encoding="utf-8")

    with pytest.raises(UnknownSessionError):
        getattr(default_backends, asked)(one, "another")

    assert kept.read_text(encoding="utf-8") == "still here"


def test_a_turn_handed_its_files_and_no_id_runs_in_the_one(one):
    """Refused as a turn that would mint a session and run in another's files, which in
    this workspace no turn can: naming no session names the one there is."""
    kf = _service(one)

    result = kf.run(Request(task="go"), files=kf.files_for(THE_ONE))

    assert result.session_id == THE_ONE


def test_files_for_reaches_the_one_session(one, way):
    """Before any turn, as a caller placing files ahead of the first one would."""
    kf = Kingfisher(one, backends=default_backends)

    files = Through(kf, way).files_for(THE_ONE)
    files.upload_files([("/outputs/placed.txt", b"placed")])

    assert (one.workspace / "sessions" / "outputs" / "placed.txt").read_bytes() == b"placed"


# -- going, and coming back -----------------------------------------------


def _delete_session(kf: Kingfisher) -> None:
    assert kf.delete_session(THE_ONE) is None


def _delete_after_the_turn(kf: Kingfisher) -> None:
    assert kf.run(Request(task="done"), delete_session=True).deletion_failure is None


def _reap_it(kf: Kingfisher) -> None:
    assert kf.reap(older_than_seconds=0, now=time.time()).removed == (THE_ONE,)


@pytest.mark.parametrize("deleting", [_delete_session, _delete_after_the_turn, _reap_it])
def test_a_deleted_session_comes_back_empty_under_the_same_id(one, tmp_path, deleting):
    """Removed and not emptied, `sessions/` goes with it -- and where a deployment has
    mounted storage there, a mountpoint cannot be removed, so every delete reported a
    failure after it had worked.

    With what a session leaves at its root besides folders -- a read-only input, a file
    and a link the shell made where it starts -- so emptying is not only `rmtree` per
    folder.
    """
    placed = tmp_path / "numbers.csv"
    placed.write_text("forty\n", encoding="utf-8")
    kf = _service(one)
    kf.run(Request(task="the number is forty", inputs=(placed,)))
    sessions = one.workspace / "sessions"
    (sessions / "stray.txt").write_text("left", encoding="utf-8")
    (sessions / "link").symlink_to(sessions / "outputs")

    deleting(kf)

    assert sessions.is_dir()
    assert not list(sessions.iterdir())
    assert [info.id for info in kf.sessions()] == [THE_ONE]
    assert kf.run(Request(task="and now?")).session_id == THE_ONE
    assert "forty" not in _sent(kf), "the conversation outlived the session"


def test_reaping_by_name_at_the_command_line_empties_it(at_the_command_line, monkeypatch, capsys):
    """`reap --session` looks the id up before deleting, so a listing of folders answers
    "no session" for the one session -- read from the environment, as a deployment sets it.
    """
    monkeypatch.setenv("KINGFISHER_SESSION_ID", THE_ONE)
    default_backends.open(replace(at_the_command_line, session_id=THE_ONE), THE_ONE)

    assert main(["reap", "--session", THE_ONE]) == 0

    assert f"reaped {THE_ONE}" in capsys.readouterr().out
    assert not list((at_the_command_line.workspace / "sessions").iterdir())


def test_the_idle_clock_is_the_last_turn(one):
    """`mark_used` touching `sessions/` is the one session's clock, so a turn has to move
    it and a sweep has to read it -- or a session in daily use is reaped as idle.
    """
    kf = _service(one)
    sessions = one.workspace / "sessions"
    kf.run(Request(task="go"))
    os.utime(sessions, (1_000, 1_000))

    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == (THE_ONE,)
    kf.run(Request(task="back again"))
    assert kf.reap(older_than_seconds=3600, now=time.time()).removed == ()
    (info,) = kf.sessions()
    assert time.time() - info.last_used < 60


def test_the_command_lists_the_one_session(at_the_command_line, monkeypatch, capsys):
    """Before any turn, where a listing of `sessions/`'s folders would say there were
    none -- and after one, where it would name the session's own folders."""
    monkeypatch.setenv("KINGFISHER_SESSION_ID", THE_ONE)

    assert main(["sessions", "--json"]) == 0
    before = json.loads(capsys.readouterr().out)
    default_backends.open(replace(at_the_command_line, session_id=THE_ONE), THE_ONE)
    assert main(["sessions", "--json"]) == 0
    after = json.loads(capsys.readouterr().out)

    assert [held["id"] for held in before["sessions"]] == [THE_ONE]
    assert [held["id"] for held in after["sessions"]] == [THE_ONE]


# -- a workspace used both ways ---------------------------------------------


def test_one_session_refuses_a_workspace_holding_sessions_of_their_own(cfg, one):
    """Laid out over them, the one session's agent reads every other session's files at
    `/<id>/` -- refused at startup, and where a deployment opens one without starting.
    """
    ensure_session_layout(cfg.workspace / "sessions" / "abc123")

    with pytest.raises(ConfigError, match="abc123"):
        Kingfisher(one, backends=default_backends)
    with pytest.raises(ConfigError, match="abc123"):
        default_backends.open(one, THE_ONE)
    assert not (cfg.workspace / "sessions" / HARNESS).exists()


def test_many_sessions_refuse_a_workspace_holding_one(cfg, one):
    """Read as a folder of sessions, the one session's `inputs/` and `memory/` are
    listed, and `reap` deletes them -- refused at startup, and where a deployment lists
    without starting.
    """
    default_backends.open(one, THE_ONE)

    with pytest.raises(ConfigError, match="KINGFISHER_SESSION_ID"):
        Kingfisher(cfg, backends=default_backends)
    with pytest.raises(ConfigError, match="KINGFISHER_SESSION_ID"):
        default_backends.sessions(cfg)


def test_unsetting_the_id_is_told_what_it_was(cfg, one):
    """With the setting gone, the record is the one place its value survives, and a
    refusal saying "set it to that session's id" sends the operator hunting for it.
    """
    default_backends.open(one, THE_ONE)

    with pytest.raises(ConfigError, match=f"KINGFISHER_SESSION_ID to '{THE_ONE}'"):
        Kingfisher(cfg, backends=default_backends)


def test_a_session_laid_out_before_the_record_is_still_refused_without_one(cfg):
    """A `sessions/` from before ids were recorded has none to name, and must not be let
    through for lacking it.
    """
    ensure_session_layout(cfg.workspace / "sessions")

    with pytest.raises(ConfigError, match="that session's id"):
        Kingfisher(cfg, backends=default_backends)


def test_a_stray_folder_at_the_session_root_does_not_lock_the_workspace(one):
    """Under bubblewrap and on macOS the shell may `mkdir` where it starts, which here is
    `sessions/` -- so a refusal of anything but the session's own folders would let one
    command stop the workspace starting."""
    default_backends.open(one, THE_ONE)
    (one.workspace / "sessions" / "stray").mkdir()

    Kingfisher(one, backends=default_backends)


def test_a_harness_the_agent_writes_does_not_lock_the_workspace(one):
    """Driven through a real file tool rather than made by hand, because that is the
    premise: `/.harness/` is denied to it and `/outputs/.harness/` is not, so a refusal of
    any folder holding a `.harness/` could be set off from inside the session.
    """
    default_backends.open(one, THE_ONE)
    directory = one.workspace / "sessions"
    for path in ("/outputs/.harness/notes.md", "/stray/.harness/notes.md"):
        _drive(one, directory, "write_file", {"file_path": path, "content": "mine"})
    assert (directory / "stray" / HARNESS / "notes.md").is_file(), "the premise moved"

    Kingfisher(one, backends=default_backends)


def test_an_ordinary_workspace_still_starts(cfg):
    """So the refusals above are not passing because every workspace is refused."""
    ensure_session_layout(cfg.workspace / "sessions" / "abc123")

    Kingfisher(cfg, backends=default_backends)


def test_seeding_is_not_asked_which_mode_it_serves(one):
    """Seeding reads no session setting, so asked the mode it would refuse a workspace
    holding one session for a setting it never read."""
    default_backends.open(one, THE_ONE)

    assert ensure_layout(one.workspace) == one.workspace


def test_the_refusal_names_a_few_and_counts_the_rest(cfg):
    """A shared workspace may hold thousands, and a refusal naming every one buries
    what to do about it."""
    for name in ("a", "b", "c", "d", "e"):
        ensure_session_layout(cfg.workspace / "sessions" / name)

    with pytest.raises(ConfigError, match=r"a, b, c and 2 more"):
        check_sessions(cfg.workspace, only=THE_ONE)


# -- the id it was laid out for ---------------------------------------------

ANOTHER = "another"


def _record(cfg) -> Path:
    return cfg.workspace / "sessions" / HARNESS / SESSION_ID_RECORD


def _start(cfg) -> None:
    Kingfisher(cfg, backends=default_backends)


def _open(cfg) -> None:
    default_backends.open(cfg, cfg.session_id)


def _list(cfg) -> None:
    default_backends.sessions(cfg)


@pytest.mark.parametrize("asking", [_start, _open, _list], ids=["startup", "open", "listing"])
def test_a_changed_id_is_refused(one, asking):
    """Mapped onto the same `sessions/`, a changed `KINGFISHER_SESSION_ID` carried the
    old session's conversation, agent and memory on under the new id, and a caller
    still holding the old one was refused.
    """
    _service(one).run(Request(task="the number is forty"))
    harness = one.workspace / "sessions" / HARNESS
    held = {p.name: p.read_bytes() for p in harness.iterdir()}

    with pytest.raises(ConfigError) as refused:
        asking(replace(one, session_id=ANOTHER))

    said = str(refused.value)
    assert f"KINGFISHER_SESSION_ID back to {THE_ONE!r}" in said, said
    assert f"to start {ANOTHER!r} afresh" in said, said
    assert "KINGFISHER_WORKSPACE" in said, said
    assert {p.name: p.read_bytes() for p in harness.iterdir()} == held


def test_the_same_id_is_accepted_again(one):
    """So the refusal above is not passing because a recorded workspace is refused
    whatever it is started with -- which would stop every one at its second start.
    """
    _service(one).run(Request(task="the number is forty"))

    again = _service(one)
    again.run(Request(task="and now?"))

    assert "forty" in _sent(again), "the restart did not continue the conversation"
    assert _record(one).read_text(encoding="utf-8") == THE_ONE


@pytest.mark.parametrize("deleting", [_delete_session, _delete_after_the_turn, _reap_it])
def test_a_new_id_is_accepted_once_the_session_is_gone(one, deleting):
    """Kept anywhere a delete does not empty, the record would refuse the new id that
    the refusal itself says clearing `sessions/` will admit.
    """
    kf = _service(one)
    kf.run(Request(task="the number is forty"))
    deleting(kf)
    other = replace(one, session_id=ANOTHER)

    result = _service(other).run(Request(task="and now?"))

    assert result.session_id == ANOTHER
    assert _record(one).read_text(encoding="utf-8") == ANOTHER


def test_a_session_laid_out_before_the_record_is_accepted_and_given_one(one):
    """Refused for lacking a record, every workspace holding one session laid out
    before the id was recorded would stop starting; let through and never given one,
    it would stay open to the change this refuses.
    """
    default_backends.open(one, THE_ONE)
    _record(one).unlink()

    _start(one)
    _open(one)

    assert _record(one).read_text(encoding="utf-8") == THE_ONE
    with pytest.raises(ConfigError, match=ANOTHER):
        _start(replace(one, session_id=ANOTHER))


def test_a_shared_workspace_records_no_id(cfg):
    """Shared mode is untouched: written whatever the mode, every session there would
    carry an id its folder's name already says, in a file nothing reads.
    """
    for name in ("abc123", "def456"):
        default_backends.open(cfg, name)

    kf = Kingfisher(cfg, backends=default_backends)

    assert sorted(info.id for info in kf.sessions()) == ["abc123", "def456"]
    assert not list((cfg.workspace / "sessions").glob(f"*/{HARNESS}/{SESSION_ID_RECORD}"))


def test_no_file_tool_reaches_the_record(one):
    """Driven through real file tools, because the record is only as safe as the
    folder it is kept in: moved out of `.harness/`, the agent could read it, or
    rewrite it and hand its session to another id.
    """
    default_backends.open(one, THE_ONE)
    sessions = one.workspace / "sessions"
    # From `_record` rather than spelled here, so a record moved elsewhere moves what
    # the tools are asked for, instead of leaving them refused at a path it has left.
    path = "/" + _record(one).relative_to(sessions).as_posix()

    read = _drive(one, sessions, "read_file", {"file_path": path})
    _drive(one, sessions, "write_file", {"file_path": path, "content": ANOTHER})
    listed = _drive(one, sessions, "ls", {"path": "/"})

    assert THE_ONE not in read, read
    assert _record(one).read_text(encoding="utf-8") == THE_ONE
    assert HARNESS not in listed, listed


# -- the shell, which no file tool rule reaches ---------------------------


@macos
def test_the_shell_cannot_write_or_remove_the_one_session_s_harness(one):
    """`sessions/*/.harness` does not match `sessions/.harness`, so the shell could
    rewrite the pinned agent, the conversation and a paused turn -- and removing the
    folder would leave the workspace looking like one holding many.
    """
    backend = default_backends.open(one, THE_ONE)
    harness = one.workspace / "sessions" / HARNESS
    pinned = harness / PINNED_AGENT
    pinned.write_text("name: pinned\n", encoding="utf-8")

    backend.execute(f'printf "name: mine" > "{pinned}"')
    backend.execute(f'rm -f "{pinned}"; mv "{harness}" "{harness}.gone"; rm -rf "{harness}"')

    assert harness.is_dir()
    assert pinned.read_text(encoding="utf-8") == "name: pinned\n"


@macos
def test_the_shell_cannot_rewrite_or_remove_the_record(one):
    """The half no file tool rule reaches: rewritten, the session would answer to
    another id; removed, a changed id would be let through as one never recorded.
    """
    backend = default_backends.open(one, THE_ONE)
    record = _record(one)

    backend.execute(f'printf "{ANOTHER}" > "{record}"')
    backend.execute(f'rm -f "{record}"')

    assert record.read_text(encoding="utf-8") == THE_ONE


@macos
def test_the_shell_can_still_write_the_rest_of_the_one_session(one):
    """The bound on the rule: `.harness` one level down is the session's own folder
    with a name, not the harness, here."""
    backend = default_backends.open(one, THE_ONE)

    assert backend.execute("echo fine > outputs/ok.txt").exit_code == 0
    nested = "mkdir -p outputs/.harness && echo fine > outputs/.harness/ok"
    assert backend.execute(nested).exit_code == 0


@macos
def test_a_shell_in_a_shared_session_cannot_make_sessions_harness(cfg, session_dir):
    """Its presence is how a workspace holding one session is told apart, so a shell able
    to make it could stop a shared workspace starting."""
    backend = default_backends.open(cfg, session_dir.name)

    outcome = backend.execute(f'mkdir "{cfg.workspace / "sessions" / HARNESS}"')

    assert outcome.exit_code != 0
    check_sessions(cfg.workspace, only=None)

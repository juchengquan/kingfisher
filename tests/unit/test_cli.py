"""`kingfisher seed`, `run` and `serve`, and the rule they are held to."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.kinds.skills.catalogue import LocalSkillRepository
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import start, verbs


def test_bare_invocation_prints_help_and_does_nothing(capsys):
    """The safe default a shipped command needs."""
    assert main([]) == 0

    printed = capsys.readouterr().out
    assert "seed" in printed
    assert "list" in printed


def test_seeding_needs_no_model_catalogue(cfg, monkeypatch, capsys, shipped):
    """The point of running on the paths half of the configuration."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)
    assert not (cfg.workspace / "models.yaml").exists()

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "seeded" in printed
    assert (cfg.workspace / "models.yaml.example").is_file()


def test_seeding_puts_the_example_where_the_catalogue_is_read_from(
    cfg, monkeypatch, capsys, shipped, tmp_path
):
    """`compose.yaml` sets `KINGFISHER_MODELS_FILE`, so this is the shipped case."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    elsewhere = tmp_path / "config"
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(elsewhere / "models.yaml"))

    assert main(["seed"]) == 0

    assert (elsewhere / "models.yaml.example").is_file()


def test_the_skip_message_carries_the_line_to_write(cfg, monkeypatch, capsys, shipped):
    """A remedy is only actionable if it is about the source ids you are missing."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "groups.yaml.example" not in printed, "still naming a file that is gone"
    assert "source_ids: [audit_log, sales_db, sales_db_pii, warehouse]" in printed, (
        "the remedy does not carry the line to write, so the reader is told to "
        "produce a format they have not been shown"
    )


def test_the_line_is_printed_once_for_every_definition_skipped(
    cfg, monkeypatch, capsys, shipped
):
    """Two definitions are skipped for source ids and they want overlapping but different
    sets.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert printed.count("the source_ids.yaml that unblocks") == 1
    unchecked = [ln for ln in printed.splitlines() if "does not check your source_ids.yaml" in ln]
    assert len(unchecked) > 1


def test_no_line_is_printed_when_nothing_wanted_a_source_id(cfg, monkeypatch, capsys, tmp_path):
    """It earns its lines or it has none."""
    plain = tmp_path / "plain" / "skills" / "only"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text(
        "---\nname: only\ndescription: A skill.\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(tmp_path / "plain"))

    assert main(["seed"]) == 0

    assert "source_ids.yaml" not in capsys.readouterr().out


def test_seeding_a_workspace_that_does_not_exist_yet_creates_it(
    tmp_path, monkeypatch, capsys, shipped
):
    """`ensure_layout` before the copy."""
    workspace = tmp_path / "brand-new"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert workspace.is_dir()
    assert "seeded" in capsys.readouterr().out


def test_seeding_from_an_empty_directory_says_so(cfg, monkeypatch, capsys, tmp_path):
    """A caller pointing `--from` at a directory with nothing in it."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("sys.argv", ["kingfisher", "seed", "--from", str(empty)])

    assert main(["seed", "--from", str(empty)]) == 1

    printed = capsys.readouterr().out
    assert "nothing to seed" in printed
    for kind in ("agents", "skills", "subagents", "tools"):
        assert kind in printed


def test_an_unknown_verb_is_refused(capsys):
    """argparse's own refusal, which names the two that exist."""
    with pytest.raises(SystemExit) as exit_code:
        main(["teleport"])

    assert exit_code.value.code == 2


# -- an unknown verb, now that `help` no longer answers for one --------------


def test_an_unknown_verb_is_named_along_with_the_ones_that_exist(capsys):
    """`help` was kept for this one case, and argparse already covers it."""
    with pytest.raises(SystemExit) as exit_code:
        main(["teleport"])

    assert exit_code.value.code == 2
    printed = capsys.readouterr().err
    assert "teleport" in printed
    assert "seed" in printed and "list" in printed


def test_the_help_verb_is_gone_and_the_four_other_routes_are_not(capsys):
    """`-h`, `--help`, a bare invocation and `<verb> --help` all reach the same text; a
    fifth road to it was a verb that could go stale on its own.
    """
    from kingfisher.presentation.cli.__main__ import build_parser

    assert "help" not in verbs(build_parser())

    assert main([]) == 0
    assert "seed" in capsys.readouterr().out

    # `--help` is argparse's, so it exits rather than returning -- zero either way.
    with pytest.raises(SystemExit) as exit_code:
        main(["seed", "--help"])

    assert exit_code.value.code == 0
    assert "usage: kingfisher seed" in capsys.readouterr().out


# -- `serve`, a second door onto one server --------------------------------


def test_serve_is_offered_whether_or_not_the_extra_is_installed():
    """A command that exists and says what to install beats one that is silently absent
    -- the same choice `kingfisher-server` already made.
    """
    from kingfisher.presentation.cli.__main__ import build_parser

    # Through the public `_actions`, because `_subparsers._source_id_actions` is
    # typed as optionally absent and reaching into it needs a cast to satisfy a
    # checker -- which is a lot of ceremony for reading a list of verbs.
    verbs = {
        choice
        for action in build_parser()._actions
        for choice in getattr(action, "choices", None) or ()
    }

    # Membership, not the exact set. Naming every verb here makes this fail on
    # each one added -- it did, the moment `doctor` landed -- and the claim is
    # about `serve` being offered, not about how many siblings it has. Still
    # exact enough: renaming the verb to `srv` fails this.
    assert "serve" in verbs


def test_serve_without_the_extra_says_what_to_install(monkeypatch, capsys):
    """Not a traceback. The reader has one thing to do and the line says it."""
    import builtins

    real = builtins.__import__

    def _no_server(name, *args, **kwargs):
        if name.startswith("kingfisher_service"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_server)

    assert main(["serve"]) == 1

    printed = capsys.readouterr().err
    assert "kingfisher[service]" in printed


def test_a_missing_server_extra_does_not_take_the_other_verbs_down(
    monkeypatch, capsys, cfg, shipped
):
    """The reason the import is inside the function."""
    import builtins

    real = builtins.__import__

    def _no_server(name, *args, **kwargs):
        if name.startswith("kingfisher_service"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_server)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert "seeded" in capsys.readouterr().out


def test_serve_hands_off_rather_than_deciding_anything(monkeypatch):
    """One implementation behind two names."""
    from kingfisher_service import __main__ as server

    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append("served") or 0)

    from kingfisher.presentation.cli.__main__ import _serve

    assert _serve() == 0
    assert calls == ["served"]


def test_every_verb_the_parser_offers_has_something_to_run_it():
    """The one risk the dispatch table introduces."""
    from kingfisher.presentation.cli.__main__ import HANDLERS, build_parser

    offered = set(verbs(build_parser()))

    assert offered == set(HANDLERS), (
        f"offered but unwired: {sorted(offered - set(HANDLERS))}; "
        f"wired but not offered: {sorted(set(HANDLERS) - offered)}"
    )


# -- `./.env`, and nowhere else --------------------------------------------


def test_the_env_file_beside_you_is_read(tmp_path, monkeypatch, capsys, shipped):
    """The failure this was written for."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"KINGFISHER_WORKSPACE={tmp_path / 'ws'}\n", encoding="utf-8"
    )
    monkeypatch.delenv("KINGFISHER_WORKSPACE", raising=False)
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert (tmp_path / "ws").is_dir()


def test_a_parent_directorys_env_file_is_not_read(tmp_path, monkeypatch):
    """The objection that was right, kept.

    `load_dotenv()` with no argument walks *upward* from the calling file, which for an
    installed package starts in `site-packages`; naming the path is what takes that
    away.
    """
    (tmp_path / ".env").write_text("KINGFISHER_WORKSPACE=/should/never/be/read\n", encoding="utf-8")
    below = tmp_path / "below"
    below.mkdir()
    monkeypatch.chdir(below)
    monkeypatch.delenv("KINGFISHER_WORKSPACE", raising=False)

    assert main(["seed"]) == 2  # no workspace named anywhere it looked

    assert not Path("/should/never/be/read").exists()


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch, capsys, shipped):
    """`override=False`, and it matters."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"KINGFISHER_WORKSPACE={tmp_path / 'from-the-file'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "from-the-shell"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert (tmp_path / "from-the-shell").is_dir()
    assert not (tmp_path / "from-the-file").exists()


def test_no_env_file_is_the_ordinary_case(tmp_path, monkeypatch, capsys, shipped):
    """An installed kingfisher usually has none, so absent must be silent and must not
    stop the command reaching the environment.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    assert not (tmp_path / ".env").exists()

    assert main(["seed"]) == 0

    assert (tmp_path / "ws").is_dir()


def test_the_refusal_names_the_file_it_looked_at(tmp_path, monkeypatch, capsys):
    """A caller standing one directory from theirs is told about a variable that is set,
    just not here.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["list"]) == 2

    printed = capsys.readouterr().err
    assert str(tmp_path / ".env") in printed
    assert "not found" in printed


def test_as_parses_a_comma_separated_list():
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("A,B") == ("A", "B")
    assert _held(" A , B ") == ("A", "B")


def test_as_unscoped_is_spelled_out_rather_than_implied():
    """An empty `--as` is far more likely to be a shell variable that did not expand
    than a considered decision to run with no caller.
    """
    from kingfisher.domain.access import UNSCOPED
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("UNSCOPED") is UNSCOPED
    assert _held("") == ()


# -- `run`, the verb the command existed for years without ------------------


def _ran(monkeypatch, events, cfg):
    """Point `run` at a scripted stream, so nothing reaches a model."""
    from kingfisher.presentation.cli import __main__ as entry

    class Stub:
        def __init__(self, *a, **k) -> None:
            self.seen: list = []
            self.deleted: list = []
            # What `delete_session` answers: a failure string, as the library's
            # own does, or None for a removal that worked.
            self.failure: str | None = None

        def stream(self, request, *, source_ids=None):
            self.seen.append((request, source_ids))
            yield from events

        def delete_session(self, session_id):
            self.deleted.append(session_id)
            return self.failure

    stub = Stub()
    monkeypatch.setattr("kingfisher.Kingfisher", lambda *a, **k: stub)
    monkeypatch.setattr(entry, "config_from_env", lambda: cfg)
    return stub


def _finished(stop_reason="end_turn", artifacts=()):
    from kingfisher import RunEvent, RunResult

    return RunEvent(
        kind="finished",
        result=RunResult(
            session_id="s1", turn_id="t001", answer="42",
            virtual_dir="/runs/t001", stop_reason=stop_reason, artifacts=artifacts,
        ),
    )


def test_the_answer_goes_to_stdout_and_the_watching_to_stderr(cfg, monkeypatch, capsys):
    """What makes the verb compose."""
    from kingfisher import RunEvent

    _ran(monkeypatch, [RunEvent(kind="run_start", text="/runs/t001"),
                       RunEvent(kind="token", text="forty two"),
                       _finished()], cfg)

    assert main(["run", "do a thing", "--agent", "assistant"]) == 0

    shown = capsys.readouterr()
    assert shown.out == "forty two"
    assert "/runs/t001" in shown.err
    assert "forty two" not in shown.err


def test_a_delegates_prose_is_progress_rather_than_answer(cfg, monkeypatch, capsys):
    """A reviewer's working notes are not what you asked for."""
    from kingfisher import RunEvent

    _ran(monkeypatch, [RunEvent(kind="token", text="mine"),
                       RunEvent(kind="token", text="theirs", agent="reviewer"),
                       _finished()], cfg)

    main(["run", "t", "--agent", "assistant"])

    shown = capsys.readouterr()
    assert shown.out == "mine"
    assert "theirs" in shown.err


def test_a_turn_stopped_at_a_bound_exits_non_zero(cfg, monkeypatch, capsys):
    """`kingfisher run ..."""
    _ran(monkeypatch, [_finished(stop_reason="max_steps")], cfg)

    assert main(["run", "t", "--agent", "assistant"]) == 1
    assert "max_steps" in capsys.readouterr().err


def test_the_agent_is_required_because_there_is_no_honest_default(cfg, monkeypatch):
    """An agent decides which endpoint the prompts go to and whose credentials pay."""
    _ran(monkeypatch, [_finished()], cfg)

    with pytest.raises(SystemExit) as exit_code:
        main(["run", "do a thing"])

    assert exit_code.value.code == 2


def test_a_file_that_is_not_there_is_refused_before_the_model(cfg, monkeypatch, capsys):
    """The one mistake that would otherwise cost money to discover."""
    _ran(monkeypatch, [_finished()], cfg)

    assert main(["run", "t", "--agent", "a", "--data", "/nope/missing.csv"]) == 2
    assert "no such file" in capsys.readouterr().err


def test_who_is_calling_reaches_the_library(cfg, monkeypatch):
    """`--as` is not decoration: on a workspace that declares source ids the library refuses
    a turn that names nobody, and names this flag when it does.
    """
    stub = _ran(monkeypatch, [_finished()], cfg)

    main(["run", "t", "--agent", "a", "--as", "A,B"])

    assert stub.seen[0][1] == ("A", "B")


# -- seeding lands where the catalogue is, not where the workspace is --------
#
# These moved off the driver when `--seed` did. They were always about where
# `seeding.seed` puts things, which is the shipped verb's job -- and testing it
# through a driver that is not in the wheel meant the thing anybody installs was
# the thing nothing covered.


def test_seeding_lands_in_the_catalogue_not_the_workspace(
    cfg, tmp_path, shipped, monkeypatch, capsys
):
    """They are the same directory until a deployment moves them, and a preset written
    to `workspace/skills` is invisible to an agent reading the relocated one -- the
    bug this has caught before.
    """
    catalogue = tmp_path / "catalogue"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.setenv("KINGFISHER_SKILLS_DIR", str(catalogue / "skills"))
    monkeypatch.setenv("KINGFISHER_SUBAGENTS_DIR", str(catalogue / "subagents"))

    assert main(["seed"]) == 0

    assert LocalSkillRepository(catalogue / "skills").names
    assert not LocalSkillRepository(cfg.workspace / "skills").names
    assert "seeded" in capsys.readouterr().out


def test_seeding_still_works_when_the_catalogue_is_the_workspace(cfg, shipped, monkeypatch):
    """The default, and the case the old code got right -- worth keeping, or the fix
    above could quietly break the ordinary setup.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0
    assert LocalSkillRepository(cfg.skills_dir).names


def test_seeding_puts_tools_in_the_tool_catalogue(cfg, tmp_path, shipped, monkeypatch):
    """The third catalogue, and the third chance to seed where nothing reads."""
    from kingfisher.kinds.tools.catalogue import LocalToolRepository

    catalogue = tmp_path / "catalogue"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.setenv("KINGFISHER_TOOLS_DIR", str(catalogue / "tools"))

    assert main(["seed"]) == 0

    assert "http_fetch" in LocalToolRepository(catalogue / "tools").names
    # `ensure_layout` still makes the workspace directory, so the place to put
    # one is obvious. What must not happen is a preset landing in it.
    assert LocalToolRepository(cfg.workspace / "tools").names == ()


# -- `sessions` and `reap`, the workspace's own housekeeping -----------------


def _looking_at(monkeypatch, cfg):
    """Point the verbs at this workspace, so none of them reads the real environment."""
    from kingfisher.presentation.cli import __main__ as entry

    monkeypatch.setattr(entry, "config_from_env", lambda: cfg)


def test_the_sessions_are_listed_most_recently_used_first(cfg, monkeypatch, capsys):
    """A listing in the filesystem's order says nothing about which one to reap."""
    import os

    _looking_at(monkeypatch, cfg)
    start(cfg, "older")
    start(cfg, "newer")
    os.utime(cfg.workspace / "sessions" / "older", (1_000, 1_000))

    assert main(["sessions"]) == 0

    # Read off the rows rather than searched for in the whole block: the
    # header names the workspace, and a tmp path under `/var/folders` contains
    # the word this asserts on.
    printed = capsys.readouterr().out.splitlines()
    rows = [line.split()[0] for line in printed if line.startswith("  ")]
    assert rows == ["newer", "older"]


def test_the_listing_says_what_a_session_is_costing(cfg, monkeypatch, capsys):
    """Without the size this repeats what `ls` already says, and what a cleanup command
    is actually asked is what the thing is costing.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "fat")
    (cfg.workspace / "sessions" / "fat" / "derived" / "big.bin").write_bytes(b"x" * (1 << 20))

    assert main(["sessions"]) == 0

    assert "1.0 MB" in capsys.readouterr().out


def test_a_workspace_holding_no_sessions_says_so(cfg, monkeypatch, capsys):
    """A command that prints nothing at all reads as one that failed."""
    _looking_at(monkeypatch, cfg)

    assert main(["sessions"]) == 0
    assert "no sessions" in capsys.readouterr().out


def test_both_forms_of_the_listing_name_where_they_read(cfg, monkeypatch, capsys):
    """The rule `doctor` puts its origins in both forms for: a JSON form that omits
    where everything was read from is the disagreement between surfaces that record
    exists to end.
    """
    import json

    _looking_at(monkeypatch, cfg)
    start(cfg, "s1")

    main(["sessions"])
    block = capsys.readouterr().out
    main(["sessions", "--json"])
    document = json.loads(capsys.readouterr().out)

    assert str(cfg.workspace / "sessions") in block
    assert document["root"] == str(cfg.workspace / "sessions")
    assert [held["id"] for held in document["sessions"]] == ["s1"]


def test_a_bare_number_is_refused_rather_than_read_as_seconds(cfg, monkeypatch, capsys):
    """Somebody who means a week types `--older-than 7`, and seven *seconds* sweeps
    every session no turn is running in. The plausible misreading is the destructive
    one, so there is no reading at all.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "s1")

    with pytest.raises(SystemExit) as exit_code:
        main(["reap", "--older-than", "7"])

    assert exit_code.value.code == 2
    assert "30m, 12h, 7d" in capsys.readouterr().err
    # The half that bites. A refusal that still swept would be no refusal.
    assert (cfg.workspace / "sessions" / "s1").is_dir()


def test_zero_is_the_one_age_that_needs_no_unit(cfg, monkeypatch):
    """Zero is the same number in every unit, and "all of them" is what a person
    clearing a workspace by hand actually types.
    """
    import os

    _looking_at(monkeypatch, cfg)
    start(cfg, "s1")
    os.utime(cfg.workspace / "sessions" / "s1", (1_000, 1_000))

    assert main(["reap", "--older-than", "0"]) == 0
    assert not (cfg.workspace / "sessions" / "s1").exists()


def test_a_sweep_that_removes_nothing_names_what_decided(cfg, monkeypatch, capsys):
    """The ordinary case on a workspace in daily use is that nothing has expired. A
    command that deletes nothing and says nothing there is one whose next user deletes
    the directory by hand, which leaves the conversation, the claim and whatever a
    store kept exactly where they were.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "s1")

    assert main(["reap"]) == 0

    printed = capsys.readouterr().out
    assert "KINGFISHER_SESSION_TTL_S" in printed
    assert "--older-than" in printed
    assert (cfg.workspace / "sessions" / "s1").is_dir()


def test_an_age_you_asked_for_is_not_blamed_on_the_setting(cfg, monkeypatch, capsys):
    """The message names where the number came from, and naming an environment variable
    the caller never set sends them to edit the wrong thing.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "s1")

    assert main(["reap", "--older-than", "7d"]) == 0

    printed = capsys.readouterr().out
    assert "7d" in printed
    assert "KINGFISHER_SESSION_TTL_S" not in printed


def test_reaping_one_by_name_takes_it_whatever_its_age(cfg, monkeypatch, capsys):
    """The id you are holding is the session you want gone, and the TTL has nothing to
    say about it.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "keep")
    start(cfg, "go")

    assert main(["reap", "--session", "go"]) == 0

    assert not (cfg.workspace / "sessions" / "go").exists()
    assert (cfg.workspace / "sessions" / "keep").is_dir()


def test_an_id_that_names_nothing_is_refused_rather_than_called_done(cfg, monkeypatch, capsys):
    """`delete_session` answers `None` both for "removed it" and for "there was no such
    session", so without the check here a mistyped id reports success for work that
    nothing did.
    """
    _looking_at(monkeypatch, cfg)

    assert main(["reap", "--session", "nosuch"]) == 2
    assert "nosuch" in capsys.readouterr().err


def test_an_age_and_a_name_are_not_both_askable(cfg, monkeypatch):
    """Two different questions. A command handed both would have to decide which one of
    them it had been asked.
    """
    _looking_at(monkeypatch, cfg)

    with pytest.raises(SystemExit) as exit_code:
        main(["reap", "--older-than", "1d", "--session", "s1"])

    assert exit_code.value.code == 2


def test_a_session_that_will_not_go_exits_non_zero(cfg, monkeypatch, capsys):
    """A sweep that could not remove what it named is "did something, but not all",
    which is what 1 already means for `list` and for a turn stopped at a bound.
    """
    import os

    _looking_at(monkeypatch, cfg)
    start(cfg, "stuck")
    os.utime(cfg.workspace / "sessions" / "stuck", (1_000, 1_000))
    monkeypatch.setattr(
        "kingfisher.infrastructure.workspace.sessions.LocalSessionDirs.remove_tree",
        lambda self, path: "directory not removed (Permission denied)",
    )

    assert main(["reap", "--older-than", "1d"]) == 1
    assert "not reaped" in capsys.readouterr().err


# -- `--delete-session`, for a run that should leave nothing behind ----------


def test_a_run_told_to_delete_its_session_does(cfg, monkeypatch):
    """The whole of the flag: a one-off run that leaves no directory behind."""
    stub = _ran(monkeypatch, [_finished()], cfg)

    assert main(["run", "t", "--agent", "assistant", "--delete-session"]) == 0
    assert stub.deleted == ["s1"]


def test_a_run_not_told_to_keeps_its_session(cfg, monkeypatch):
    """The default, which has to stay right for somebody who does not know sessions
    exist yet: files gone before they knew to look for them is worse than a directory
    they can delete later.
    """
    stub = _ran(monkeypatch, [_finished()], cfg)

    assert main(["run", "t", "--agent", "assistant"]) == 0
    assert stub.deleted == []


def test_a_turn_stopped_at_a_bound_keeps_its_session_and_says_so(cfg, monkeypatch, capsys):
    """The one ending whose leftovers are worth something: the partial work is real and
    the conversation is what a retry on the same session is rebuilt from. Deleting here
    would also make the line printed just above it -- what it wrote is in /runs/t001 --
    a lie about a directory that had already gone.
    """
    stub = _ran(monkeypatch, [_finished(stop_reason="max_steps")], cfg)

    assert main(["run", "t", "--agent", "assistant", "--delete-session"]) == 1

    assert stub.deleted == []
    printed = capsys.readouterr().err
    assert "--session s1" in printed
    assert "reap --session s1" in printed


def test_what_the_session_takes_with_it_is_named_before_it_goes(cfg, monkeypatch, capsys):
    """This is the moment those files stop being recoverable, and the command prints
    `artifacts` nowhere else -- so without it a run that wrote a file and a run that
    wrote nothing end identically.
    """
    written = ("derived/report.md", "memory/AGENTS.md")
    stub = _ran(monkeypatch, [_finished(artifacts=written)], cfg)

    assert main(["run", "t", "--agent", "assistant", "--delete-session"]) == 0

    printed = capsys.readouterr().err
    assert "derived/report.md" in printed
    assert "memory/AGENTS.md" in printed
    assert stub.deleted == ["s1"]


def test_the_flag_takes_a_session_you_named_just_as_readily(cfg, monkeypatch):
    """One rule -- delete the session this run used -- rather than one that turns on
    where the id came from, which is a flag nobody can predict the effect of.
    """
    stub = _ran(monkeypatch, [_finished()], cfg)

    argv = ["run", "t", "--agent", "assistant", "--session", "s1", "--delete-session"]
    assert main(argv) == 0
    assert stub.deleted == ["s1"]


def test_a_session_that_will_not_delete_does_not_fail_the_turn(cfg, monkeypatch, capsys):
    """Those three exit codes say how the *turn* ended, and 1 already means the answer
    above was cut short -- which would be a lie told about a turn that finished.
    """
    stub = _ran(monkeypatch, [_finished()], cfg)
    stub.failure = "s1: directory not removed (Permission denied)"

    assert main(["run", "t", "--agent", "assistant", "--delete-session"]) == 0
    assert "not deleted" in capsys.readouterr().err

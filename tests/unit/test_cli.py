"""`kingfisher seed`, `run` and `serve`, and the rule they are held to."""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.kinds.skills.catalogue import LocalSkillRepository
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import verbs


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
    """A remedy is only actionable if it is about the groups you are missing."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "groups.yaml.example" not in printed, "still naming a file that is gone"
    assert "groups: [analysts, auditors, reviewers, senior-analysts]" in printed, (
        "the remedy does not carry the line to write, so the reader is told to "
        "produce a format they have not been shown"
    )


def test_the_line_is_printed_once_for_every_definition_skipped(
    cfg, monkeypatch, capsys, shipped
):
    """Two definitions are skipped for groups and they want overlapping but different
    sets.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert printed.count("the groups.yaml that unblocks") == 1
    assert len([ln for ln in printed.splitlines() if "does not check your groups.yaml" in ln]) > 1


def test_no_line_is_printed_when_nothing_wanted_a_group(cfg, monkeypatch, capsys, tmp_path):
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

    assert "groups.yaml" not in capsys.readouterr().out


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

    # Through the public `_actions`, because `_subparsers._group_actions` is
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

        def stream(self, request, *, groups=None):
            self.seen.append((request, groups))
            yield from events

    stub = Stub()
    monkeypatch.setattr("kingfisher.Kingfisher", lambda *a, **k: stub)
    monkeypatch.setattr(entry, "config_from_env", lambda: cfg)
    return stub


def _finished(stop_reason="end_turn"):
    from kingfisher import RunEvent, RunResult

    return RunEvent(
        kind="finished",
        result=RunResult(
            session_id="s1", turn_id="t001", answer="42",
            virtual_dir="/runs/t001", stop_reason=stop_reason,
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
    """`--as` is not decoration: on a workspace that declares groups the library refuses
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

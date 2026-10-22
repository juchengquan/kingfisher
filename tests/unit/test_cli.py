"""`kingfisher seed` and `run`, and the rule they are held to."""

from __future__ import annotations

import argparse
import io
import re
import tokenize
from pathlib import Path

import pytest

from kingfisher import Kingfisher, Origin
from kingfisher.cli.__main__ import build_parser, main
from kingfisher.core.definitions.skills.catalogue import reachable
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, repository_root, start, verbs


def test_bare_invocation_prints_help_and_does_nothing(capsys):
    """The safe default a shipped command needs."""
    assert main([]) == 0

    printed = capsys.readouterr().out
    assert "seed" in printed
    assert "list" in printed


def test_seeding_needs_no_model_catalogue(tmp_path, monkeypatch, capsys, shipped):
    """The point of running on the paths half of the configuration: no catalogue and
    no workspace, only the folder it writes into -- which then holds the catalogue's
    worked example.
    """
    definitions = tmp_path / "definitions"
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.delenv("KINGFISHER_WORKSPACE", raising=False)

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "seeded" in printed
    assert (definitions / "models.yaml.example").is_file()
    assert not (definitions / "models.yaml").exists()


def test_seeding_with_nothing_to_copy_still_leaves_somewhere_to_start(
    tmp_path, monkeypatch, capsys
):
    """Catches the command refusing for want of a source before it lays the definitions
    folder out, which leaves a deployment with nothing to seed yet no folder and no
    `models.yaml.example` -- a refusal with nowhere to go next.
    """
    definitions = tmp_path / "definitions"
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(definitions))
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)

    assert main(["seed"]) == 2

    assert "no definitions to seed from" in capsys.readouterr().err
    assert (definitions / "models.yaml.example").is_file()


def test_seeding_refuses_a_setting_that_named_where_it_wrote(
    monkeypatch, capsys, shipped, tmp_path
):
    """A deployment still naming its catalogue with `KINGFISHER_MODELS_FILE` is refused
    before anything is written: carrying on would seed a folder it never named, beside
    a catalogue it is not reading.
    """
    definitions = tmp_path / "definitions"
    elsewhere = tmp_path / "config"
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(elsewhere / "models.yaml"))

    assert main(["seed"]) == 2

    assert "KINGFISHER_MODELS_FILE was removed" in capsys.readouterr().err
    assert not definitions.exists()
    assert not elsewhere.exists()


def test_the_skip_line_says_which_of_the_two_reasons_it_was(cfg, monkeypatch, capsys, shipped):
    """The word that tells a reader which of the two it was. The sentence is per-kind --
    why it was held back and what to do -- and the reason alone reads as advice about
    nothing in particular until the line says what was named.
    """
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "names source_ids (" in printed
    assert "a lock switches on access control in this workspace" in printed


def test_the_skip_message_names_no_file_to_write(cfg, monkeypatch, capsys, shipped):
    """There is no vocabulary file any more, so a remedy telling a reader to write one
    sends them to produce a file nothing reads."""
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "source_ids.yaml" not in printed
    assert "seed again with --all" in printed.lower()


def test_the_reason_is_printed_on_every_definition_skipped(cfg, monkeypatch, capsys, shipped):
    """Each skipped line gives its own, so a reader skimming to one file still reads why."""
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    locked = [ln for ln in printed.splitlines() if "switches on access control" in ln]
    assert len(locked) > 1


def test_no_line_is_printed_when_nothing_wanted_a_source_id(cfg, monkeypatch, capsys, tmp_path):
    """It earns its lines or it has none."""
    plain = tmp_path / "plain" / "skills" / "only"
    plain.mkdir(parents=True)
    (plain / "SKILL.md").write_text(
        "---\nname: only\ndescription: A skill.\n---\n\nDo the thing.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(tmp_path / "plain"))

    assert main(["seed"]) == 0

    assert "access control" not in capsys.readouterr().out


def test_seeding_a_definitions_folder_that_does_not_exist_yet_creates_it(
    tmp_path, monkeypatch, capsys, shipped
):
    """`ensure_definitions` before the copy."""
    definitions = tmp_path / "brand-new"
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert definitions.is_dir()
    assert "seeded" in capsys.readouterr().out


def test_seeding_from_an_empty_directory_says_so(cfg, monkeypatch, capsys, tmp_path):
    """A caller pointing `--from` at a directory with nothing in it."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setattr("sys.argv", ["kingfisher", "seed", "--from", str(empty)])

    assert main(["seed", "--from", str(empty)]) == 1

    printed = capsys.readouterr().out
    assert "nothing to seed" in printed
    for kind in ("agents", "skills", "subagents", "tools"):
        assert kind in printed


def test_seeding_nothing_never_gives_one_definitions_reason_as_every_ones(
    cfg, monkeypatch, capsys, tmp_path
):
    """The command closed on `skipped[0].wants`: one agent held back for middleware and
    one for source ids read "every definition … names middlewares", and told a reader
    to seed middleware for the agent that carries a lock.
    """
    tree = tmp_path / "tree"
    agents = tree / "agents"
    agents.mkdir(parents=True)
    body = "description: d\nsystem_prompt: |\n  Hi.\n"
    (agents / "wired.yaml").write_text(f"name: wired\nmiddlewares: [audit]\n{body}")
    (agents / "scoped.yaml").write_text(f"name: scoped\nsource_ids: [analysts]\n{body}")
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))

    assert main(["seed", "--from", str(tree)]) == 1

    lines = capsys.readouterr().out.splitlines()
    closing = lines[-1]
    assert closing.startswith("nothing seeded"), lines
    # Each reason is on its own `skipped` line above; the closing line restates none.
    assert "middlewares" not in closing
    assert "source_ids" not in closing
    assert any(line.startswith("skipped agents/wired.yaml") for line in lines)
    assert any(line.startswith("skipped agents/scoped.yaml") for line in lines)


def test_an_unknown_verb_is_refused(capsys):
    """argparse's own refusal, which names the ones that exist."""
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
    from kingfisher.cli.__main__ import build_parser

    assert "help" not in verbs(build_parser())

    assert main([]) == 0
    assert "seed" in capsys.readouterr().out

    # `--help` is argparse's, so it exits rather than returning -- zero either way.
    with pytest.raises(SystemExit) as exit_code:
        main(["seed", "--help"])

    assert exit_code.value.code == 0
    assert "usage: kingfisher seed" in capsys.readouterr().out


def test_every_verb_the_parser_offers_has_something_to_run_it():
    """The one risk the dispatch table introduces."""
    from kingfisher.cli.__main__ import HANDLERS, build_parser

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
        f"KINGFISHER_DEFINITIONS_DIR={tmp_path / 'definitions'}\n", encoding="utf-8"
    )
    monkeypatch.delenv("KINGFISHER_DEFINITIONS_DIR", raising=False)
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert (tmp_path / "definitions").is_dir()


def test_a_parent_directorys_env_file_is_not_read(tmp_path, monkeypatch):
    """The objection that was right, kept.

    `load_dotenv()` with no argument walks *upward* from the calling file, which for an
    installed package starts in `site-packages`; naming the path is what takes that
    away.
    """
    (tmp_path / ".env").write_text(
        "KINGFISHER_DEFINITIONS_DIR=/should/never/be/read\n", encoding="utf-8"
    )
    below = tmp_path / "below"
    below.mkdir()
    monkeypatch.chdir(below)
    monkeypatch.delenv("KINGFISHER_DEFINITIONS_DIR", raising=False)

    assert main(["seed"]) == 2  # no definitions folder named anywhere it looked

    assert not Path("/should/never/be/read").exists()


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch, capsys, shipped):
    """`override=False`, and it matters."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"KINGFISHER_DEFINITIONS_DIR={tmp_path / 'from-the-file'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(tmp_path / "from-the-shell"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert (tmp_path / "from-the-shell").is_dir()
    assert not (tmp_path / "from-the-file").exists()


def test_no_env_file_is_the_ordinary_case(tmp_path, monkeypatch, capsys, shipped):
    """An installed kingfisher usually has none, so absent must be silent and must not
    stop the command reaching the environment.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(tmp_path / "definitions"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    assert not (tmp_path / ".env").exists()

    assert main(["seed"]) == 0

    assert (tmp_path / "definitions").is_dir()


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
    from kingfisher.cli.__main__ import _held

    assert _held("A,B") == ("A", "B")
    assert _held(" A , B ") == ("A", "B")


def test_as_unscoped_is_spelled_out_rather_than_implied():
    """An empty `--as` is far more likely to be a shell variable that did not expand
    than a considered decision to run with no caller.
    """
    from kingfisher.cli.__main__ import _held
    from kingfisher.core.access.source_ids import UNSCOPED

    assert _held("UNSCOPED") is UNSCOPED
    assert _held("") == ()


# -- `run`, the verb the command existed for years without ------------------


def _ran(monkeypatch, events, cfg):
    """Point `run` at a scripted stream, so nothing reaches a model."""
    from kingfisher.cli import __main__ as entry

    class Stub:
        def __init__(self, *a, **k) -> None:
            self.seen: list = []
            self.deleted: list = []
            # What deleting answers: a failure string, as the library's own does,
            # or None for a removal that worked.
            self.failure: str | None = None

        def stream(self, request, *, source_ids=None):
            self.seen.append((request, source_ids))
            yield from events

        def session(self, session_id, *, source_ids=None):
            stub = self

            class Handle:
                def delete(self):
                    stub.deleted.append(session_id)
                    return stub.failure

            return Handle()

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
            stop_reason=stop_reason, artifacts=artifacts,
        ),
    )


def test_the_answer_goes_to_stdout_and_the_watching_to_stderr(cfg, monkeypatch, capsys):
    """With a streaming model stdout got every reply the agent made, its narration before
    a tool call included, so `> answer.md` kept prose that was not the answer.

    Driven through a real two-call turn, because only a real one streams the narration.
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from kingfisher import Landing
    from kingfisher.cli import __main__ as entry
    from kingfisher.plugins import WireFormat
    from tests.unit.scripted import Streamed

    row = WireFormat(
        f"{Streamed.__module__}:Streamed",
        Landing(model="model", base_url="base_url", api_key="api_key",
                max_tokens="max_tokens", timeout="timeout"),
    )
    endpoints = {name: replace(one, wire_format=row) for name, one in cfg.models.endpoints.items()}
    streamed = replace(cfg, models=replace(cfg.models, endpoints=endpoints))
    monkeypatch.setattr(entry, "config_from_env", lambda: streamed)
    Streamed.script.clear()
    Streamed.script.extend([
        AIMessage(content="Let me write that down first.", tool_calls=[{
            "name": "write_file", "id": "c1",
            "args": {"file_path": "/outputs/note.txt", "content": "42"},
        }]),
        AIMessage(content="The answer is forty two."),
    ])

    assert main(["run", "do a thing", "--agent", an_agent(cfg)]) == 0

    shown = capsys.readouterr()
    assert shown.out == "The answer is forty two."
    assert "Let me write that down first." in shown.err, "the narration was not watched"
    assert "write_file" in shown.err


def test_an_answer_that_did_not_stream_is_printed_when_the_run_ends(cfg, monkeypatch, capsys):
    """A model that does not stream sends no `token` events, and the answer exists only
    on the result. One line of `models.yaml` -- `disable_streaming: true`, a kwarg the
    shipped example documents -- made `run` print nothing and exit 0 while `kf.run` on
    the same request answered.
    """
    from kingfisher import RunEvent

    _ran(monkeypatch, [RunEvent(kind="run_start", text="t001"), _finished()], cfg)

    assert main(["run", "do a thing", "--agent", "assistant"]) == 0

    assert capsys.readouterr().out == "42"


def test_run_refuses_an_unknown_shell_sandbox_before_it_opens_a_session(
    cfg, at_the_command_line, monkeypatch, capsys
):
    """`run` with `KINGFISHER_SHELL_SANDBOX=bogus` made a session and then died on its
    first turn with a `ValueError` traceback and exit 1, the code for a turn that ran."""
    agent = an_agent(cfg)
    monkeypatch.setenv("KINGFISHER_SHELL_SANDBOX", "bogus")

    assert main(["run", "t", "--agent", agent]) == 2

    assert "configuration error: KINGFISHER_SHELL_SANDBOX" in capsys.readouterr().err
    assert not cfg.sessions_root.exists() or not any(cfg.sessions_root.iterdir())


def _refused(how: str, folder: Path) -> list[str]:
    """`run`'s arguments for a request the library turns down after opening its session."""
    if how == "no such agent":
        return ["--agent", "nobody"]
    for name in ("a", "b"):
        (folder / name).mkdir()
        (folder / name / "same.csv").write_text(name)
    return ["--agent", "only", "--input", str(folder / "a" / "same.csv"),
            "--input", str(folder / "b" / "same.csv")]


@pytest.mark.parametrize("how", ["no such agent", "inputs under one name"])
def test_a_refused_run_leaves_no_session_behind(cfg, at_the_command_line, tmp_path, how):
    """`run` with no `--session`, refused, left an empty `default` behind: the session was
    made when it was opened, before the refusals that read it."""
    an_agent(cfg)

    assert main(["run", "t", *_refused(how, tmp_path)]) == 2

    assert not cfg.sessions_root.exists() or not any(cfg.sessions_root.iterdir())


@pytest.mark.parametrize("how", ["no such agent", "inputs under one name"])
def test_a_refused_run_keeps_a_session_that_was_there(cfg, at_the_command_line, tmp_path, how):
    """The control beside the escape: a session there before the refused request, empty
    as one just made, is not this request's to take back."""
    an_agent(cfg)
    start(cfg, "default")

    assert main(["run", "t", *_refused(how, tmp_path)]) == 2

    assert sorted(p.name for p in cfg.sessions_root.iterdir()) == ["default"]
    assert (cfg.sessions_root / "default" / "memory").is_dir()


def test_a_warning_of_kingfishers_own_is_one_plain_line(cfg, session_dir, monkeypatch, capsys):
    """The unconfined-shell warning reached `run` in Python's default shape: a path into
    the package, a line number and the `warnings.warn` source line, around the one
    sentence addressed to the reader.

    Raised by the real `LocalSessions` rather than a `warnings.warn` here, because a
    warning this file raises is attributed to this file and would test the other branch.
    """
    from kingfisher.plugins import ShellSettings, backend_at

    def turn():
        backend_at(cfg, session_dir, shell=ShellSettings(sandbox="off"))
        yield _finished()

    _ran(monkeypatch, turn(), cfg)
    main(["run", "t", "--agent", "assistant"])

    err = capsys.readouterr().err
    assert "warning: the agent's shell is unconfined" in err
    assert "UserWarning" not in err
    assert "warnings.warn" not in err


def test_a_warning_from_someone_elses_code_is_left_as_it_was(cfg, monkeypatch, capsys, recwarn):
    """The control: a dependency's warning is about code the operator did not write, and
    its file and line are the only pointer to it -- so it goes to whatever handled
    warnings before, unformatted.
    """
    import warnings

    def turn():
        warnings.warn("from a dependency", UserWarning, stacklevel=1)
        yield _finished()

    _ran(monkeypatch, turn(), cfg)
    handler = warnings.showwarning
    main(["run", "t", "--agent", "assistant"])

    assert "from a dependency" not in capsys.readouterr().err
    assert "from a dependency" in [str(w.message) for w in recwarn]
    assert warnings.showwarning is handler, "the plain handler must not outlive the run"


def test_a_verb_other_than_run_says_a_warning_plainly_too(cfg, monkeypatch, capsys):
    """Only `run` had the plain line, and `list` loads the model catalogue as well: its
    warning about a missing key -- the only report of one `list` gives -- still came out
    as a path, a line number and `UserWarning`.
    """
    (cfg.definitions / "models.yaml").write_text(
        "endpoints:\n"
        "  here:\n    api: anthropic\n    base_url: http://127.0.0.1:9/never-called\n"
        "    key_env: HERE_KEY\n"
        "  there:\n    api: anthropic\n    base_url: http://127.0.0.1:9/never-called\n"
        "    key_env: THERE_KEY\n"
        "default: here-model\n"
        "models:\n  here-model:\n    endpoint: here\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("HERE_KEY", "not-a-real-key")
    monkeypatch.delenv("THERE_KEY", raising=False)

    main(["list"])

    err = capsys.readouterr().err
    assert [line for line in err.splitlines()
            if line.startswith("warning: ") and "THERE_KEY is not set" in line]
    assert "UserWarning" not in err


def test_a_subagents_prose_is_progress_rather_than_answer(cfg, monkeypatch, capsys):
    """A reviewer's working notes are not what you asked for."""
    from kingfisher import RunEvent

    _ran(monkeypatch, [RunEvent(kind="token", text="mine"),
                       RunEvent(kind="token", text="theirs", agent="reviewer"),
                       _finished()], cfg)

    main(["run", "t", "--agent", "assistant"])

    shown = capsys.readouterr()
    assert shown.out == "42"
    assert "theirs" in shown.err


@pytest.mark.parametrize("task", ["", "   "], ids=["empty", "spaces"])
def test_an_empty_task_is_refused_before_anything_runs(task, capsys):
    """`kingfisher run ''` died on the library's `ValueError` with a traceback and exit 1,
    the code for a turn that ran and stopped."""
    assert main(["run", task, "--agent", "assistant"]) == 2

    said = capsys.readouterr().err
    assert "the task is empty" in said
    assert "Traceback" not in said


def test_the_run_names_the_files_it_left(cfg, monkeypatch, capsys):
    """`kingfisher artifact` fetches "by the name the run printed", and the run printed
    none, so the one way to learn a name was to read the session's folder."""
    _ran(monkeypatch, [_finished(artifacts=("outputs/report.html", "memory/notes.md"))], cfg)

    assert main(["run", "t", "--agent", "assistant"]) == 0

    said = capsys.readouterr().err
    assert "  outputs/report.html\n" in said
    assert "  memory/notes.md\n" in said


def test_a_turn_stopped_at_a_bound_exits_non_zero(cfg, monkeypatch, capsys):
    """`kingfisher run ... > report.md && publish report.md` must not publish a report
    that stopped halfway."""
    _ran(monkeypatch, [_finished(stop_reason="max_steps")], cfg)

    assert main(["run", "t", "--agent", "assistant"]) == 1
    assert "max_steps" in capsys.readouterr().err


def test_the_agent_is_required_because_there_is_no_honest_default(cfg, monkeypatch):
    """An agent decides which endpoint the prompts go to and whose credentials pay."""
    _ran(monkeypatch, [_finished()], cfg)

    with pytest.raises(SystemExit) as exit_code:
        main(["run", "do a thing"])

    assert exit_code.value.code == 2


def test_every_file_that_is_not_there_is_named_at_once(cfg, at_the_command_line, capsys):
    """The library refuses a missing input too, but names only the first, so a caller
    with two typos would find the second on the next run. Against the real library: a
    stub refuses nothing, so a run without the check would fail here for another reason."""
    agent = an_agent(cfg)

    code = main([
        "run", "t", "--agent", agent, "--input", "/nope/one.csv", "--input", "/nope/two.csv"
    ])

    assert code == 2
    assert "no such file: /nope/one.csv, /nope/two.csv" in capsys.readouterr().err


def test_two_inputs_under_one_name_are_refused_through_the_command(
    cfg, at_the_command_line, tmp_path, capsys
):
    """`InputsError` was in no `except`, so `--input a/same.csv --input b/same.csv` gave a
    traceback and exit 1, the code the help gives a turn that ran and stopped at a bound."""
    agent = an_agent(cfg)
    for folder in ("a", "b"):
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "same.csv").write_text(folder)

    code = main([
        "run", "hi", "--agent", agent,
        "--input", str(tmp_path / "a" / "same.csv"),
        "--input", str(tmp_path / "b" / "same.csv"),
    ])

    said = capsys.readouterr().err
    assert code == 2
    assert "InputsError: same.csv: supplied twice" in said
    assert "Traceback" not in said, "the refusal reached the terminal as a traceback"


def test_who_is_calling_reaches_the_library(cfg, monkeypatch):
    """`--as` is not decoration: where a definition carries a lock the library refuses a
    turn that names nobody, and names this flag when it does.
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


def test_seeding_lands_in_the_definitions_folder_not_the_workspace(
    cfg, shipped, monkeypatch, capsys
):
    """A preset written to `workspace/skills` is invisible to an agent reading the
    definitions folder -- the bug this has caught before, with a workspace named
    beside the folder seeding was meant for.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(cfg.definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert reachable(cfg.skills_dir)
    assert not (cfg.workspace / "skills").exists()
    assert "seeded" in capsys.readouterr().out


def test_seeding_refuses_a_definitions_folder_inside_the_workspace(
    cfg, shipped, monkeypatch, capsys
):
    """Seeding needs no workspace, and still refuses one that would hold what it writes:
    definitions there are in the one place every session's shell may read.
    """
    inside = cfg.workspace / "definitions"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(inside))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 2

    assert "neither may be inside the other" in capsys.readouterr().err
    assert not inside.exists()


def test_seeding_never_makes_a_workspace(tmp_path, shipped, monkeypatch):
    """Not even one it is told about: seeding writes what people author, and the
    workspace is what a start lays out on the machine that serves sessions.
    """
    from kingfisher.core.definitions.tools.catalogue import LocalToolRepository

    workspace = tmp_path / "fresh"
    definitions = tmp_path / "definitions"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(workspace))
    monkeypatch.setenv("KINGFISHER_DEFINITIONS_DIR", str(definitions))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    seeded = LocalToolRepository(definitions / "tools").found
    assert "http_fetch" in {one.name for one in seeded}, "nothing seeded"
    assert not workspace.exists()


# -- `sessions` and `reap`, the workspace's own housekeeping -----------------


def _looking_at(monkeypatch, cfg):
    """Point the verbs at this workspace, so none of them reads the real environment."""
    from kingfisher.cli import __main__ as entry

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
    (cfg.workspace / "sessions" / "fat" / "outputs" / "big.bin").write_bytes(b"x" * (1 << 20))

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


def test_no_surface_names_the_workspace_directory_when_backends_keep_sessions(
    cfg, monkeypatch, capsys
):
    """The setting exists so that `sessions` and `reap` do not list and delete a local
    `sessions/` while a deployment's own backends keep them somewhere else -- and the
    path printed beside those listings said `<workspace>/sessions` whatever the backends
    were. "no sessions in ..." pointed at an empty directory the backends never touch.

    Driven over the three surfaces together, because the listing was already right and
    only the line beside it was wrong: a rule reading one of them would have passed.
    `test_both_forms_of_the_listing_name_where_they_read` is the control and runs on the
    default backend, where the derived path is the correct answer.
    """
    import json

    # A factory that really builds, because the command builds it. Named rather than
    # faked for that reason: `configured_sessions` imports the dotted path, so a
    # plausible-looking string would fail before any of this is reached.
    monkeypatch.setenv("KINGFISHER_SESSIONS", "tests.unit.test_named_backend:make_backends")
    _looking_at(monkeypatch, cfg)
    derived = str(cfg.workspace / "sessions")

    main(["sessions"])
    listing = capsys.readouterr().out
    main(["sessions", "--json"])
    document = json.loads(capsys.readouterr().out)
    main(["list"])
    catalogue = capsys.readouterr().out

    assert derived not in listing, "the human listing named a directory nothing reads"
    assert document["root"] is None, "and a script was handed it as a fact"
    assert "sessions    : <supplied>" in catalogue


def test_the_library_and_the_commands_agree_on_where_sessions_are(cfg, monkeypatch):
    """`Kingfisher.origins` asked the backends it was built with and the commands derived
    a path from the workspace, so the same deployment got two answers depending on which
    door it came through. Both ask the sessions in use, the command the ones it names.
    """
    from kingfisher.cli.configured import configured_sessions
    from kingfisher.core.inventory import inventory

    monkeypatch.setenv("KINGFISHER_SESSIONS", "tests.unit.test_named_backend:make_backends")

    class Theirs(LocalSessions):
        """A deployment's own, which is what makes `origins` answer `<supplied>`: told
        apart by identity, since a subclass may return its backend with anything changed.
        """

    through_the_library = Kingfisher(cfg, sessions=Theirs()).origins.sessions
    through_a_command = inventory(cfg, sessions=configured_sessions()).origins.sessions

    assert through_the_library == through_a_command == Origin("supplied", None)


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


@pytest.mark.parametrize(
    "ttl_said_shorter",
    [(5400, "90m", "1h"), (30, "30s", "0"), (7 * 86400, "7d", "1d")],
    ids=["90m", "30s", "a week"],
)
def test_the_age_that_spared_them_is_printed_as_it_was(
    cfg, monkeypatch, capsys, ttl_said_shorter
):
    """Floored, a 5400s TTL read "longer than 1h" and a 30s one "longer than <1m", and the
    hint was always `--older-than 1d`, longer than any TTL under a day."""
    from dataclasses import replace

    ttl, said, shorter = ttl_said_shorter
    _looking_at(monkeypatch, replace(cfg, session_ttl_s=ttl))
    start(cfg, "fresh")

    assert main(["reap"]) == 0

    printed = capsys.readouterr().out
    assert f"idle longer than {said}, which is KINGFISHER_SESSION_TTL_S" in printed
    assert f"kingfisher reap --older-than {shorter}\n" in printed


def test_more_than_one_busy_session_are_busy(cfg, monkeypatch, capsys):
    """It said "2 sessions is busy"."""
    _looking_at(monkeypatch, cfg)
    _busy_since(cfg, "held", hours=2)
    _busy_since(cfg, "also", hours=2)
    start(cfg, "fresh")

    assert main(["reap", "--older-than", "30m"]) == 0

    assert "2 sessions are busy" in capsys.readouterr().out


def _busy_since(cfg, session_id: str, *, hours: float) -> str:
    """A session a turn is holding, last touched `hours` ago."""
    import os
    import time

    from tests.unit.test_tenancy import _claim

    start(cfg, session_id)
    _claim(cfg, session_id).write_text("held", encoding="utf-8")
    old = time.time() - hours * 3600
    os.utime(cfg.workspace / "sessions" / session_id, (old, old))
    return session_id


def test_reap_names_the_turn_rather_than_the_age_that_did_not_decide(cfg, monkeypatch, capsys):
    """`expired` folds a busy session into `kept`, so a sweep that removed nothing
    blamed the age -- for a session `kingfisher sessions` prints as hours idle. Two
    commands contradicting each other on one workspace, and advice that cannot work: no
    age sweeps a session a turn is running in.
    """
    _looking_at(monkeypatch, cfg)
    _busy_since(cfg, "held", hours=2)

    assert main(["reap", "--older-than", "30m"]) == 0

    printed = capsys.readouterr().out
    assert "a turn is running in it" in printed
    assert "idle longer than" not in printed, "the age did not decide this"


def test_reap_names_both_reasons_where_both_applied(cfg, monkeypatch, capsys):
    """One held by a turn and one simply too young. Naming only the age would be wrong
    about the first, and naming only the turn wrong about the second.
    """
    _looking_at(monkeypatch, cfg)
    _busy_since(cfg, "held", hours=2)
    start(cfg, "fresh")

    assert main(["reap", "--older-than", "30m"]) == 0

    printed = capsys.readouterr().out
    assert "1 session is busy" in printed
    assert "none of the other 1 session here has been idle longer than 30m" in printed


def test_reap_still_blames_the_age_when_the_age_is_what_decided(cfg, monkeypatch, capsys):
    """The control: a sweep over sessions no turn is holding must go on naming the age,
    which is the only thing that spared them.
    """
    _looking_at(monkeypatch, cfg)
    start(cfg, "fresh")

    assert main(["reap", "--older-than", "30m"]) == 0

    printed = capsys.readouterr().out
    assert "idle longer than 30m" in printed
    assert "busy" not in printed


def test_the_idle_column_reads_straight_into_the_flag_it_claims_to(cfg, monkeypatch, capsys):
    """`sessions --help` says the column is in the units `--older-than` takes, so a
    listing reads into a sweep. Rounded, it did not: a session idle 1.6 days printed
    `2d`, and `reap --older-than 2d` left it where it was -- the two commands
    contradicting each other on one workspace, which is what the rule above exists to
    keep them out of. 2.4 days printed `2d` as well, so one figure covered a session a
    sweep would take and a session it would not.
    """
    import os
    import time

    _looking_at(monkeypatch, cfg)
    start(cfg, "s-one")
    old = time.time() - 1.6 * 86400
    os.utime(cfg.workspace / "sessions" / "s-one", (old, old))

    assert main(["sessions"]) == 0
    printed = capsys.readouterr().out
    assert "1d" in printed, f"the column rounded 1.6 days up: {printed!r}"

    assert main(["reap", "--older-than", "1d"]) == 0
    assert not (cfg.workspace / "sessions" / "s-one").exists(), (
        "the column named an age the flag then declined to act on"
    )


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
    """Deleting answers `None` both for "removed it" and for "there was no such
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
        "kingfisher.plugins.sessions.folders.LocalSessionDirs.remove_tree",
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
    would also make the line printed just above it -- what it wrote is in /outputs and
    /memory -- a lie about a directory that had already gone.
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
    written = ("outputs/report.md", "memory/AGENTS.md")
    stub = _ran(monkeypatch, [_finished(artifacts=written)], cfg)

    assert main(["run", "t", "--agent", "assistant", "--delete-session"]) == 0

    printed = capsys.readouterr().err
    assert "outputs/report.md" in printed
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


def test_a_policied_workspace_still_deletes_the_session_its_run_used(
    cfg, monkeypatch, capsys
):
    """`_discard` opens the session `UNSCOPED`: asked as nobody, a workspace declaring
    source ids refuses it, and a finished `--delete-session` run would end on an access
    error with its session still there.

    Against a real `Kingfisher` where a definition carries a lock, because the stub
    above never asks who is calling.
    """
    from kingfisher.cli import __main__ as entry
    from tests.unit.test_run import StubAgent

    # The agent's own `source_ids:` is the lock, so this workspace already refuses a
    # call that says nobody -- there is no policy to hand in beside it.
    agent = an_agent(cfg, source_ids="[A]")
    service = Kingfisher(cfg, graph=StubAgent("done"), sessions=LocalSessions())
    monkeypatch.setattr(entry, "config_from_env", lambda: cfg)
    monkeypatch.setattr("kingfisher.Kingfisher", lambda *a, **k: service)
    argv = ["run", "t", "--agent", agent, "--session", "s1", "--as", "A"]

    assert main([*argv, "--delete-session"]) == 0, capsys.readouterr().err
    assert not (cfg.workspace / "sessions" / "s1").exists()
    # The control: the same run without the flag leaves the session, so the one above
    # was deleted by the flag and not by never having been made.
    assert main(argv) == 0
    assert (cfg.workspace / "sessions" / "s1").is_dir()


# -- what the command refuses rather than raises -------------------------------


def _said_and_not_raised() -> tuple[type[Exception], ...]:
    """What the command answers with exit 2, written out rather than read off `REFUSALS`:
    parametrized over the tuple, an entry dropped from it would take its case along."""
    from kingfisher import (
        ArtifactError,
        CallerError,
        CapabilityError,
        DecisionError,
        InputsError,
        QuotaExceededError,
        SubagentError,
        UnknownSessionError,
    )
    from kingfisher.core.definitions.agents.spec import AgentError

    return (
        AgentError,
        ArtifactError,
        CallerError,
        CapabilityError,
        DecisionError,
        InputsError,
        QuotaExceededError,
        SubagentError,
        UnknownSessionError,
    )


def test_every_refusal_the_command_makes_is_one_driven_below():
    """A class added to `REFUSALS` and not here would be caught by nothing that runs it."""
    from kingfisher.cli.__main__ import REFUSALS

    assert set(REFUSALS) <= set(_said_and_not_raised())


@pytest.mark.parametrize("refusal", _said_and_not_raised(), ids=lambda cls: cls.__name__)
def test_every_refusal_reaches_the_terminal_as_one_line_and_exit_2(
    refusal, monkeypatch, capsys
):
    """Only `DecisionError` and `AgentError` were ever driven through `main`; the rest of
    `REFUSALS` -- `QuotaExceededError` and `SubagentError` among them -- could leave the
    tuple and turn back into a traceback with exit 1, a turn that stopped at a bound.
    """
    from kingfisher.cli import __main__ as entry

    def refuses(_args):
        msg = "the one line worth reading"
        raise refusal(msg)

    monkeypatch.setitem(entry.HANDLERS, "sessions", refuses)

    assert main(["sessions"]) == 2

    said = capsys.readouterr().err
    assert f"{refusal.__name__}: the one line worth reading" in said
    assert "Traceback" not in said


def test_an_error_that_is_not_a_refusal_keeps_its_traceback(monkeypatch):
    """The control beside it: a bug caught here would read as the caller's mistake."""
    from kingfisher.cli import __main__ as entry

    def broken(_args):
        msg = "limit"
        raise KeyError(msg)

    monkeypatch.setitem(entry.HANDLERS, "sessions", broken)

    with pytest.raises(KeyError):
        main(["sessions"])


# -- the flags the command names ----------------------------------------------

#: Flags the command's own words name and it does not take, each with why: another
#: program's, or one named in order to say there is none.
NOT_OURS = {
    "--dry-run": "named by `reap --help` to say there is none",
    "--memory": "docker's, in doctor's advice on a container that may swap",
    "--memory-swap": "docker's, in the same advice",
}


def _options(parser: argparse.ArgumentParser) -> set[str]:
    found: set[str] = set()
    for action in parser._actions:
        found |= set(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                found |= _options(sub)
    return found


def test_every_flag_the_command_names_is_one_it_takes():
    """The listing told a reader to grant with `--tools` and `--builtin-tools`, which
    only the test driver has -- `run` refused both as unrecognised. Read from the
    command's own source rather than its rendered output, so a message on a path no
    test drives is held too.
    """
    taken = _options(build_parser())
    named: dict[str, list[str]] = {}
    for path in sorted((repository_root() / "src/kingfisher/cli").glob("*.py")):
        source = path.read_text(encoding="utf-8")
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.STRING, tokenize.FSTRING_MIDDLE):
                for flag in re.findall(r"(?<![\w-])--[a-z][a-z-]*", token.string):
                    named.setdefault(flag, []).append(f"{path.name}:{token.start[0]}")

    unknown = {flag: where for flag, where in named.items() if flag not in taken | set(NOT_OURS)}
    assert not unknown, f"named, and taken by no kingfisher command: {unknown}"
    # A reason kept for a flag nothing names any more is one nobody can check.
    assert set(NOT_OURS) <= set(named), sorted(set(NOT_OURS) - set(named))

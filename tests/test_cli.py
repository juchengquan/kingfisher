"""`kingfisher seed` and `kingfisher list`, and the rule they are held to.

The command exists because a pip-installed kingfisher had the definitions and no
way to put them anywhere: both operations lived behind flags in `main.py`, which
is a development driver and is not in the wheel.

Held to the front door, like `kingfisher_service`. `test_architecture`
enforces that against every module here; these are about what the command does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.presentation.cli.__main__ import main
from tests.conftest import subagents_dir


def test_bare_invocation_prints_help_and_does_nothing(capsys):
    """The safe default a shipped command needs.

    `main.py` with no arguments runs the eval smoke -- a real model call against
    whatever key the deployment holds. Right for a driver you type daily, and
    the wrong first contact for someone who just installed this. Nothing is read
    and nothing is written: help does not need a workspace.
    """
    assert main([]) == 0

    printed = capsys.readouterr().out
    assert "seed" in printed
    assert "list" in printed


def test_seeding_needs_no_model_catalogue(cfg, monkeypatch, capsys):
    """The point of running on the paths half of the configuration.

    `models.yaml` lives *inside* the workspace, so a first run has none --
    requiring one would make this unusable exactly when it is wanted. Asserted
    against a workspace with no catalogue at all.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)
    assert not (cfg.workspace / "models.yaml").exists()

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert "seeded" in printed
    assert (cfg.workspace / "models.yaml.example").is_file()


def test_seeding_a_workspace_that_does_not_exist_yet_creates_it(tmp_path, monkeypatch, capsys):
    """`ensure_layout` before the copy. A destination has to exist before
    anything lands in it, and this is the command someone runs first."""
    workspace = tmp_path / "brand-new"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(workspace))

    assert main(["seed"]) == 0

    assert workspace.is_dir()
    assert "seeded" in capsys.readouterr().out


def test_seeding_from_an_empty_directory_says_so(cfg, monkeypatch, capsys, tmp_path):
    """A caller pointing `--from` at a directory with nothing in it. Silence
    would read as success."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("sys.argv", ["kingfisher", "seed", "--from", str(empty)])

    assert main(["seed", "--from", str(empty)]) == 0

    assert "nothing to seed" in capsys.readouterr().out


def test_listing_reports_a_workspace_that_will_not_load(cfg, monkeypatch, capsys):
    """Non-zero, because a listing gets read by scripts.

    Printed and returned apart, a caller could report a broken catalogue and
    exit 0 -- and the thing reading it carries on.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")  # or the endpoint is dropped
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list"]) == 1

    assert "cannot load" in capsys.readouterr().out


def test_a_missing_catalogue_is_reported_rather_than_raised(tmp_path, monkeypatch, capsys):
    """The one error a caller causes and can fix. It also says `.env` is not
    read, because the other driver reads one and the difference is invisible."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["list"]) == 2

    printed = capsys.readouterr().err
    assert "configuration error" in printed
    # Where the answer would have come from. It said `.env` is never read, which
    # was true and was the reason this failed where `main.py` worked; now the
    # useful thing is *which* file was read, since a caller one directory from
    # theirs is told about a variable that is set, just not here.
    assert "the environment and" in printed
    assert ".env" in printed


def test_an_unknown_verb_is_refused(capsys):
    """argparse's own refusal, which names the two that exist."""
    with pytest.raises(SystemExit) as exit_code:
        main(["teleport"])

    assert exit_code.value.code == 2


def _catalogue(cfg) -> object:
    """A minimal `models.yaml` beside the workspace, so `list` gets past config."""
    path = cfg.workspace / "models.yaml"
    path.write_text(
        "endpoints:\n  fake:\n    api: anthropic\n"
        "    base_url: http://127.0.0.1:9/never-called\n    key_env: FAKE_KEY\n"
        "default: fake-model\n"
        "models:\n  fake-model:\n    endpoint: fake\n",
        encoding="utf-8",
    )
    return path


def test_both_drivers_render_through_the_same_code(cfg, capsys):
    """Two doors printing one block, and now by construction.

    `show_inventory` calls `listing.render`; there is no second formatter to
    keep in step. It was a copy for one step, and the copy was already wrong:
    written from the version of `show_inventory` I had in hand rather than the
    one on disk, it missed a change that had landed days earlier -- two folders
    may each define a `surveyor`, and then the listing must not print the file
    twice. This test passed anyway, because its workspace had no such pair.

    So the fixture has one now. A comparison is only worth the cases it covers,
    and the case it did not cover is the one that broke.
    """
    import main as driver
    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import render

    _seed_something(cfg)

    assert driver.show_inventory(cfg, cfg.workspace) == 0
    printed = capsys.readouterr().out

    expected = "\n".join(render(inventory(cfg), workspace=cfg.workspace))
    assert printed.strip() == expected.strip()


def _seed_something(cfg) -> None:
    """One skill and one subagent, so the comparison has something to disagree
    about. Against an empty workspace both sides print the same three headings
    whatever the renderer does."""
    skill = cfg.skills_dir / "probe-skill"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: probe-skill\ndescription: Something to list.\n---\nDo it.\n",
        encoding="utf-8",
    )
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "probe-agent.yaml").write_text(
        "name: probe-agent\ndescription: Something to list.\n"
        "system_prompt: |\n  Answer briefly.\n",
        encoding="utf-8",
    )
    # And a name two folders both claim, which is printed as a reference rather
    # than as a name plus the file it came from. The case the copy got wrong.
    for folder in ("team", "vendor"):
        (subagents_dir(cfg) / folder).mkdir(parents=True, exist_ok=True)
        (subagents_dir(cfg) / folder / "surveyor.yaml").write_text(
            f"name: surveyor\ndescription: Surveys, the {folder} way.\n"
            "system_prompt: |\n  Survey it.\n",
            encoding="utf-8",
        )


# -- `help`, which repeats what four other routes already say ---------------


def test_help_lists_the_verbs(capsys):
    """The same text `--help` prints, reached by typing the word.

    It was argued against and added anyway: `-h`, `--help`, bare `kingfisher`
    and `<verb> --help` all reach this already. What it buys is that a reader
    looking for help finds the word listed beside the verbs it describes.
    """
    assert main(["help"]) == 0

    printed = capsys.readouterr().out
    assert "seed" in printed
    assert "list" in printed


def test_help_explains_one_verb(capsys):
    """`kingfisher help seed`, which is `kingfisher seed --help` by another road."""
    assert main(["help", "seed"]) == 0

    printed = capsys.readouterr().out
    assert "usage: kingfisher seed" in printed
    assert "Overwrites" in printed  # its own description, not the top-level one


def test_help_reads_the_verbs_from_the_parser(capsys):
    """Not from a list beside it.

    A second list of names goes stale the first time somebody adds a verb and
    does not think about `help` -- and `help` is precisely the thing nobody
    thinks about. Asserted by comparing against the parser rather than against
    words in a docstring.
    """
    from kingfisher.presentation.cli.__main__ import _verbs, build_parser

    parser = build_parser()
    for verb in _verbs(parser):
        assert main(["help", verb]) == 0
        assert f"usage: kingfisher {verb}" in capsys.readouterr().out


def test_an_unknown_verb_is_named_along_with_the_ones_that_exist(capsys):
    """The one thing this does better than `--help`.

    argparse refuses an unknown subcommand with a usage line. Here the reader
    mistyped a word and the useful answer is which words there are.
    """
    assert main(["help", "teleport"]) == 2

    printed = capsys.readouterr().err
    assert "teleport" in printed
    assert "seed" in printed and "list" in printed
# -- `serve`, a second door onto one server --------------------------------


def test_serve_is_offered_whether_or_not_the_extra_is_installed():
    """A command that exists and says what to install beats one that is silently
    absent -- the same choice `kingfisher-server` already made.

    The design argued the other way once: that a subcommand "would be missing on
    a plain install". It would not, and the existing script had already shown
    why.

    Asserted against the parser's own choices, not against a substring of the
    help text. The first version looked for "serve" in what `main([])` printed,
    and renaming the verb to `srv` left it green -- the word survives elsewhere
    on the page.
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


def test_a_missing_server_extra_does_not_take_the_other_verbs_down(monkeypatch, capsys, cfg):
    """The reason the import is inside the function.

    `kingfisher_service` reaches fastapi as it loads. Imported at module
    scope, a verb nobody asked for would break the two they did -- on exactly
    the installs that chose not to have the extra.
    """
    import builtins

    real = builtins.__import__

    def _no_server(name, *args, **kwargs):
        if name.startswith("kingfisher_service"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _no_server)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))

    assert main(["seed"]) == 0

    assert "seeded" in capsys.readouterr().out


def test_serve_hands_off_rather_than_deciding_anything(monkeypatch):
    """One implementation behind two names. If this assembled its own settings
    or set up its own logging, `kingfisher serve` and `kingfisher-server` would
    be two servers that merely look alike."""
    from kingfisher_service import __main__ as server

    calls = []
    monkeypatch.setattr(server, "main", lambda: calls.append("served") or 0)

    from kingfisher.presentation.cli.__main__ import _serve

    assert _serve() == 0
    assert calls == ["served"]
# -- `list --json`, for a script rather than a person ----------------------


def test_the_json_document_carries_every_field_the_record_has(cfg):
    """"Field for field" is a claim, and this is the mechanism.

    A key here that `Inventory` does not have would be inventing an answer; a
    field it has that is missing here would be hiding one. Either happens by a
    field being added to the record and nobody thinking about the serialiser,
    which is exactly the kind of drift nobody notices in a format only scripts
    read.
    """
    from dataclasses import fields

    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import as_json

    document = as_json(inventory(cfg))

    assert set(document) == {field.name for field in fields(inventory(cfg))}


def test_the_json_document_survives_a_round_trip(cfg):
    """It is only worth having if `json.dumps` accepts it.

    `Path`, `MappingProxyType` and tuples are all things the record holds and
    `json` refuses, so this is not a formality -- it is the whole reason the
    mapping exists rather than `asdict`.
    """
    import json

    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import as_json

    _seed_something(cfg)

    document = json.loads(json.dumps(as_json(inventory(cfg))))

    assert isinstance(document["workspace"], str)
    assert "probe-agent" in document["subagents"]
    assert document["tools_error"] is None


def test_json_and_the_human_form_describe_the_same_workspace(cfg, monkeypatch, capsys):
    """Two formats, one answer. A document that disagreed with the listing would
    be worse than no document at all -- a script would act on it."""
    import json

    _seed_something(cfg)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0
    printed = capsys.readouterr().out
    assert main(["list", "--json"]) == 0
    document = json.loads(capsys.readouterr().out)

    # The workspace was seeded above, so both are populated -- asserted rather
    # than assumed, because two empty loops agree with each other perfectly and
    # this test's whole claim is that the two formats say the same thing.
    assert document["subagents"] and document["skills"]

    for name in document["subagents"]:
        assert name in printed
    for name in document["skills"]:
        assert name in printed


def test_a_broken_workspace_is_non_zero_in_either_format(cfg, monkeypatch, capsys):
    """The exit code does not depend on the format, and the reason is in the
    document too -- so a script can find out either way round."""
    import json

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["subagents_error"]


def test_json_is_asked_for_rather_than_assumed(cfg, monkeypatch, capsys):
    """A listing whose default output is JSON is a listing nobody reads."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0

    assert not capsys.readouterr().out.lstrip().startswith("{")


def test_every_verb_the_parser_offers_has_something_to_run_it():
    """The one risk the dispatch table introduces.

    A chain of `if`s ended in a fallthrough, so a verb nobody wired reached
    `list` -- wrong, but quiet. A table raises `KeyError` instead, which is
    louder and still only at runtime, in front of whoever typed the verb. This
    is what makes it neither.
    """
    from kingfisher.presentation.cli.__main__ import HANDLERS, _verbs, build_parser

    offered = set(_verbs(build_parser()))

    assert offered == set(HANDLERS), (
        f"offered but unwired: {sorted(offered - set(HANDLERS))}; "
        f"wired but not offered: {sorted(set(HANDLERS) - offered)}"
    )


# -- a delegate the workspace built itself ----------------------------------


COMPILED_MODULE = """from langchain_core.runnables import RunnableLambda


def _build(model, tools):
    return RunnableLambda(lambda state: state)


SUBAGENTS = [
    {
        "name": "researcher",
        "description": "Researches a topic.",
        "build": _build,
    }
]
"""

PROMPTED_DEFINITION = """name: reviewer
description: Checks figures.
system_prompt: |
  You check figures.
"""


def _subagent_catalogue(cfg):
    directory = cfg.workspace / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "researcher.py").write_text(COMPILED_MODULE, encoding="utf-8")
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")


def test_the_listing_marks_a_compiled_delegate(cfg):
    """Marked because the rest of the listing means something different for it,
    and nothing else in the output would say so."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    lines = list(render(inventory(cfg), workspace=cfg.workspace))
    named = {name: [one for one in lines if one.strip().startswith(name)]
             for name in ("researcher", "reviewer")}

    assert "[compiled]" in named["researcher"][0]
    assert "[compiled]" not in named["reviewer"][0]


def test_the_listing_says_what_a_compiled_delegate_costs(cfg):
    """The assumption a reader would otherwise make. deepagents runs the graph
    as given and never applies our allowlist to it, so a tool grant is a
    suggestion there rather than a limit."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    printed = "\n".join(render(inventory(cfg), workspace=cfg.workspace))

    assert "--tools" in printed
    assert "do not restrict what it can call" in printed


def test_a_workspace_with_no_compiled_delegate_says_nothing_about_them(cfg):
    """The note is about a minority, so it stays absent for everyone else --
    a caveat printed to every reader is one none of them reads."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    directory = cfg.workspace / "subagents"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "reviewer.yaml").write_text(PROMPTED_DEFINITION, encoding="utf-8")

    printed = "\n".join(render(inventory(cfg), workspace=cfg.workspace))

    assert "compiled" not in printed


def test_a_compiled_delegate_is_not_annotated_with_the_file_you_can_already_see(cfg):
    """`_from` stays silent when the name already tells you the file. There are
    two spellings now, so the obvious filename depends on the kind -- comparing
    a `.py` definition against `<name>.yaml` would annotate every one of them."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    (line,) = [
        one for one in render(inventory(cfg), workspace=cfg.workspace)
        if one.strip().startswith("researcher")
    ]

    assert "(researcher.py)" not in line


def test_the_json_listing_carries_it_too(cfg):
    """`--json` is what a script reads, and a script deciding whether a grant
    means anything needs the same fact the text gives a person."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import as_json

    _subagent_catalogue(cfg)

    assert as_json(inventory(cfg))["compiled_subagents"] == ["researcher"]


def test_a_skill_offered_under_another_name_is_named_in_the_listing(cfg, monkeypatch, capsys):
    """`--list` is where somebody goes *because* a grant was refused for a skill
    they can see in the tree. deepagents files it by its header and warns to a
    log nobody reads, so this line is the only place the two names meet."""
    directory = cfg.skills_dir / "company-lookup"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        "---\nname: find-company\ndescription: Looks a company up.\n---\nBody.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")  # or the endpoint is dropped
    monkeypatch.setenv("KINGFISHER_SKILLS", "1")

    assert main(["list"]) == 0, "a misfiled skill loads, so this is not a failure"

    printed = capsys.readouterr().out
    assert "company-lookup/ is offered as find-company" in printed
    assert "rename the directory to match" in printed


# -- `./.env`, and nowhere else --------------------------------------------


def test_the_env_file_beside_you_is_read(tmp_path, monkeypatch, capsys):
    """The failure this was written for.

    A checkout keeps its keys in `.env`, and reading the environment alone left
    `kingfisher list` failing on a deployment where `main.py --list` worked --
    with the key three lines away in a file.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"KINGFISHER_WORKSPACE={tmp_path / 'ws'}\n", encoding="utf-8"
    )
    monkeypatch.delenv("KINGFISHER_WORKSPACE", raising=False)

    assert main(["seed"]) == 0

    assert (tmp_path / "ws").is_dir()


def test_a_parent_directorys_env_file_is_not_read(tmp_path, monkeypatch):
    """The objection that was right, kept.

    `load_dotenv()` with no argument walks *upward* from the calling file, which
    for an installed package starts in `site-packages`. Naming the path is what
    takes that away, so a file one directory up must stay invisible -- somebody
    standing in a subdirectory should not silently inherit it.
    """
    (tmp_path / ".env").write_text("KINGFISHER_WORKSPACE=/should/never/be/read\n", encoding="utf-8")
    below = tmp_path / "below"
    below.mkdir()
    monkeypatch.chdir(below)
    monkeypatch.delenv("KINGFISHER_WORKSPACE", raising=False)

    assert main(["seed"]) == 2  # no workspace named anywhere it looked

    assert not Path("/should/never/be/read").exists()


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch, capsys):
    """`override=False`, and it matters.

    Somebody writing `KINGFISHER_WORKSPACE=... kingfisher seed` has said exactly
    where they mean. A file they may not have known was in the directory must
    not quietly replace it.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text(
        f"KINGFISHER_WORKSPACE={tmp_path / 'from-the-file'}\n", encoding="utf-8"
    )
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "from-the-shell"))

    assert main(["seed"]) == 0

    assert (tmp_path / "from-the-shell").is_dir()
    assert not (tmp_path / "from-the-file").exists()


def test_no_env_file_is_the_ordinary_case(tmp_path, monkeypatch, capsys):
    """An installed kingfisher usually has none, so absent must be silent and
    must not stop the command reaching the environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    assert not (tmp_path / ".env").exists()

    assert main(["seed"]) == 0

    assert (tmp_path / "ws").is_dir()


def test_the_refusal_names_the_file_it_looked_at(tmp_path, monkeypatch, capsys):
    """A caller standing one directory from theirs is told about a variable that
    is set, just not here. Naming the path is the difference between that and a
    hunt."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("KINGFISHER_MODELS_FILE", raising=False)

    assert main(["list"]) == 2

    printed = capsys.readouterr().err
    assert str(tmp_path / ".env") in printed
    assert "not found" in printed


def test_an_unloadable_agent_catalogue_is_non_zero_too(cfg, monkeypatch, capsys):
    """The kind that arrived last, and the one `failed` did not name.

    The field was added, the section printed "cannot load", and the predicate
    still listed the two kinds that existed when it was written -- so a
    workspace whose agents will not load reported the failure and exited 0.
    Which is the exact sentence in that function's own docstring: "a caller
    could report a broken catalogue and exit 0 -- which is how a listing gets
    read by a script that then carries on".
    """
    import json

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "broken.yaml").write_text("name: broken\ndescription: d\nnope: 1\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["agents_error"]


def test_an_unloadable_tool_still_leaves_the_rest_of_the_listing(cfg, monkeypatch, capsys):
    """One unloadable catalogue must not take the others down with it, which is
    this record's own rule and was not true of tools.

    A single unparseable `.py` returned early and hid the skills and subagents
    sections entirely -- from the person who by definition is looking at a
    broken workspace, which is the worst moment to be shown less of it.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: A delegate.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )
    tools = cfg.catalogue_roots["tools"]
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "broken.py").write_text("this is not python(\n", encoding="utf-8")

    assert main(["list"]) == 1
    printed = capsys.readouterr().out

    assert "cannot load" in printed
    assert "\nskills" in printed, "the skills section went with the tools"
    assert "helper" in printed, "so did the subagents"

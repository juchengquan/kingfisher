"""`kingfisher seed` and `kingfisher list`, and the rule they are held to.

The command exists because a pip-installed kingfisher had the definitions and no
way to put them anywhere: both operations lived behind flags in `the driver`, which
is a development driver and is not in the wheel.

Held to the front door, like `kingfisher_service`. `test_architecture`
enforces that against every module here; these are about what the command does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.presentation.cli.__main__ import main
from tests.conftest import subagents_dir, verbs


def test_bare_invocation_prints_help_and_does_nothing(capsys):
    """The safe default a shipped command needs.

    `the driver` with no arguments runs the eval smoke -- a real model call against
    whatever key the deployment holds. Right for a driver you type daily, and
    the wrong first contact for someone who just installed this. Nothing is read
    and nothing is written: help does not need a workspace.
    """
    assert main([]) == 0

    printed = capsys.readouterr().out
    assert "seed" in printed
    assert "list" in printed


def test_seeding_needs_no_model_catalogue(cfg, monkeypatch, capsys, shipped):
    """The point of running on the paths half of the configuration.

    `models.yaml` lives *inside* the workspace, so a first run has none --
    requiring one would make this unusable exactly when it is wanted. Asserted
    against a workspace with no catalogue at all.
    """
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
    """`compose.yaml` sets `KINGFISHER_MODELS_FILE`, so this is the shipped case.

    Seeding wrote `models.yaml.example` into the workspace and the container
    read `/config/models.yaml`, so the one file a deployment cannot start
    without had its worked example in a directory nobody looks at.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    elsewhere = tmp_path / "config"
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(elsewhere / "models.yaml"))

    assert main(["seed"]) == 0

    assert (elsewhere / "models.yaml.example").is_file()


def test_the_skip_message_carries_the_line_to_write(cfg, monkeypatch, capsys, shipped):
    """A remedy is only actionable if it is about the groups you are missing.

    This named `groups.yaml.example` for two days, and `ensure_layout` placed
    it. Both halves were true and the pair still did not help: an example ships
    one vocabulary and a workspace needs whichever names its own definitions ask
    for, so seeding this repository's own set said "declare analysts, auditors,
    senior-analysts" and put a file beside it declaring readers, writers, staff,
    senior and senior-writers. Five names, none of them the three.

    So the shape travels in the message, where it can be built from the names
    actually missing. Asserted as a paste rather than as prose: what makes it
    actionable is that the line works unedited.
    """
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
    """Two definitions are skipped for groups and they want overlapping but
    different sets. One line naming the union unblocks both; a copy each would
    print two partial lists and leave whoever pasted the first skipped again on
    the second."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    printed = capsys.readouterr().out
    assert printed.count("the groups.yaml that unblocks") == 1
    assert len([ln for ln in printed.splitlines() if "does not declare" in ln]) > 1


def test_no_line_is_printed_when_nothing_wanted_a_group(cfg, monkeypatch, capsys, tmp_path):
    """It earns its lines or it has none. A set with no `groups:` anywhere gets
    no advice about a file it has no reason to write."""
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
    """`ensure_layout` before the copy. A destination has to exist before
    anything lands in it, and this is the command someone runs first."""
    workspace = tmp_path / "brand-new"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert workspace.is_dir()
    assert "seeded" in capsys.readouterr().out


def test_seeding_from_an_empty_directory_says_so(cfg, monkeypatch, capsys, tmp_path):
    """A caller pointing `--from` at a directory with nothing in it.

    Non-zero, which it was not. This was nearly unreachable while a set shipped
    -- it always held all four kinds -- and is now among the likelier mistakes,
    since `--from ./examples/skills` names a directory that exists, is readable
    and holds none of them. Exiting 0 after copying nothing is indistinguishable
    from success to the script that ran it.

    The message names the four kinds, because the mistake is almost always one
    directory level in the wrong direction.
    """
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr("sys.argv", ["kingfisher", "seed", "--from", str(empty)])

    assert main(["seed", "--from", str(empty)]) == 1

    printed = capsys.readouterr().out
    assert "nothing to seed" in printed
    for kind in ("agents", "skills", "subagents", "tools"):
        assert kind in printed


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
    # was true and was the reason this failed where `the driver` worked; now the
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
    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import render
    from tests.integration import driver

    _seed_something(cfg)

    assert driver.show_inventory(cfg, cfg.workspace) == 0
    printed = capsys.readouterr().out

    expected = "\n".join(render(inventory(cfg)))
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


# -- an unknown verb, now that `help` no longer answers for one --------------


def test_an_unknown_verb_is_named_along_with_the_ones_that_exist(capsys):
    """`help` was kept for this one case, and argparse already covers it.

    `kingfisher help teleport` answered "no such command: teleport. kingfisher
    knows doctor, help, list, seed, serve", which is the only thing that verb
    did better than `--help`. argparse refuses an unknown subcommand with the
    valid choices listed and the same exit code, so what was lost is the
    wording, not the answer.

    It *raises* `SystemExit(2)` where the verb *returned* 2. From a shell the
    two are indistinguishable -- the exit code is the same -- and in process
    they are not, which is the whole of what removing the verb changed for a
    caller of `main`.
    """
    with pytest.raises(SystemExit) as exit_code:
        main(["teleport"])

    assert exit_code.value.code == 2
    printed = capsys.readouterr().err
    assert "teleport" in printed
    assert "seed" in printed and "list" in printed


def test_the_help_verb_is_gone_and_the_four_other_routes_are_not(capsys):
    """`-h`, `--help`, a bare invocation and `<verb> --help` all reach the same
    text; a fifth road to it was a verb that could go stale on its own."""
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


def test_a_missing_server_extra_does_not_take_the_other_verbs_down(
    monkeypatch, capsys, cfg, shipped
):
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
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

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

    # Under `origins` now, with every other place beside it. `Path` is what
    # made this test worth having and the record still holds them, one layer in.
    assert isinstance(document["origins"]["workspace"], str)
    assert document["origins"]["skills"] == {
        "kind": "default",
        "path": str(cfg.skills_dir),
    }
    assert "probe-agent" in document["subagents"]
    assert document["tools_error"] is None


def test_the_header_names_every_catalogue_including_tools(cfg):
    """The regression this record was built to make impossible.

    The header was four hand-written lines naming the workspace, agents, skills
    and subagents. `tools` was in neither the header nor the record behind it --
    not a decision, just a fourth line nobody added -- so the one question a
    reader most often has about a relocated catalogue had no answer.
    """
    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import render

    header = list(render(inventory(cfg)))[:11]

    assert [line.split(" :")[0].strip() for line in header] == [
        "workspace", "agents", "skills", "subagents", "tools",
        "models", "groups", "seed", "state", "scratch", "sessions",
    ]


def test_the_json_carries_the_kind_and_a_path_a_script_can_open(cfg, tmp_path):
    """Two things the header deliberately does not do.

    The header spells anything under the workspace as `./name`, which is a
    reading aid -- it leaves the entries that moved as the only absolute paths
    on the page. A script wants the path it can open, and it wants the kind,
    because "nothing is configured" and "you handed me a store" are two
    situations it must not have to tell apart by matching on prose.
    """
    import json
    from dataclasses import replace

    from kingfisher import inventory
    from kingfisher.presentation.cli.listing import as_json

    document = json.loads(json.dumps(as_json(inventory(replace(cfg, assets=tmp_path)))))
    origins = document["origins"]

    assert origins["skills"]["path"].startswith("/"), "not the ./name the header prints"
    assert origins["seed"] == {"kind": "relocated", "path": str(tmp_path)}
    assert origins["groups"]["kind"] == "unset"


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
    from kingfisher.presentation.cli.__main__ import HANDLERS, build_parser

    offered = set(verbs(build_parser()))

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
    lines = list(render(inventory(cfg)))
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
    printed = "\n".join(render(inventory(cfg)))

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

    printed = "\n".join(render(inventory(cfg)))

    assert "compiled" not in printed


def test_a_compiled_delegate_is_not_annotated_with_the_file_you_can_already_see(cfg):
    """`_from` stays silent when the name already tells you the file. There are
    two spellings now, so the obvious filename depends on the kind -- comparing
    a `.py` definition against `<name>.yaml` would annotate every one of them."""
    from kingfisher.application.inventory import inventory
    from kingfisher.presentation.cli.listing import render

    _subagent_catalogue(cfg)
    (line,) = [
        one for one in render(inventory(cfg))
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


def test_the_env_file_beside_you_is_read(tmp_path, monkeypatch, capsys, shipped):
    """The failure this was written for.

    A checkout keeps its keys in `.env`, and reading the environment alone left
    `kingfisher list` failing on a deployment where `tests/integration/driver.py --list` worked --
    with the key three lines away in a file.
    """
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


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch, capsys, shipped):
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
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0

    assert (tmp_path / "from-the-shell").is_dir()
    assert not (tmp_path / "from-the-file").exists()


def test_no_env_file_is_the_ordinary_case(tmp_path, monkeypatch, capsys, shipped):
    """An installed kingfisher usually has none, so absent must be silent and
    must not stop the command reaching the environment."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
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


# -- listing under a group vocabulary ---------------------------------------

TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

NARROW = """name: narrow
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [A]
system_prompt: |
  You do the task.
"""

WIDE = """name: wide
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [A, B]
system_prompt: |
  You do the task.
"""


def _workspace(cfg, monkeypatch, *agents: str, vocabulary: str = "groups: [A, B]\n"):
    """A workspace with one tool and whichever agents the test names."""
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(TOOL, encoding="utf-8")
    directory = cfg.catalogue_roots["agents"]
    directory.mkdir(parents=True, exist_ok=True)
    for document in agents:
        name = document.split("name: ", 1)[1].split("\n", 1)[0]
        (directory / f"{name}.yaml").write_text(document, encoding="utf-8")
    (cfg.workspace / "groups.yaml").write_text(vocabulary, encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")
    return cfg


@pytest.fixture
def policied(cfg, monkeypatch):
    return _workspace(cfg, monkeypatch, NARROW)


def test_the_operator_sees_audiences_per_definition(policied, capsys):
    """The unscoped listing is the operator's audit view. It is exempt from the
    refusal that covers a *turn* -- listing is read-only, and whoever runs it is
    on the host with the definitions already in front of them."""
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" in shown
    assert "agent narrow  [A, B]" in shown
    assert "tool line_count  [A]" in shown


def test_the_operator_sees_a_roll_up_by_asset(policied, capsys):
    """The question the files can no longer answer on their own."""
    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "by tool" in shown
    assert "line_count" in shown.split("by tool", 1)[1]


def test_the_roll_up_shows_one_tool_at_two_audiences(cfg, monkeypatch, capsys):
    """The case it exists for: a call site quietly wider than its neighbour."""
    _workspace(cfg, monkeypatch, NARROW, WIDE)

    assert main(["list"]) == 0

    section = capsys.readouterr().out.split("by tool", 1)[1]
    assert "narrow  [A]" in section
    assert "wide  [A, B]" in section


BOTH = """name: both
description: An agent.
groups: [{all_of: [A, B]}]
tools:
  - name: line_count
    groups: [A, B]
system_prompt: |
  You do the task.
"""

NAMED = """name: named
description: An agent.
groups: [ab]
system_prompt: |
  You do the task.
"""


def test_an_inline_conjunction_reads_as_a_plus_b(cfg, monkeypatch, capsys):
    """`+` for "and", so the audience column stays a column."""
    _workspace(cfg, monkeypatch, BOTH)

    assert main(["list"]) == 0

    assert "agent both  [A+B]" in capsys.readouterr().out


def test_a_conjunction_is_spelled_the_same_way_wherever_it_appears(cfg, monkeypatch, capsys):
    """The by-definition view and the roll-up print the same audience, and a
    reader comparing the two should not have to translate."""
    _workspace(cfg, monkeypatch, BOTH)

    shown = capsys.readouterr().out if main(["list"]) == 0 else ""
    before, after = shown.split("by tool", 1)
    assert "[A+B]" in before
    assert "both  [A, B]" in after


def test_a_named_compound_says_what_it_requires(cfg, monkeypatch, capsys):
    """A name tells a reader nothing on the line it appears on, and every line
    it appears on needs it -- so it is said once, above."""
    _workspace(
        cfg, monkeypatch, NAMED, vocabulary="groups:\n  A: {}\n  B: {}\n  ab: {all_of: [A, B]}\n"
    )

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "groups that require others" in shown
    assert "ab = A+B" in shown
    assert "agent named  [ab]" in shown


def test_a_vocabulary_with_no_compounds_gets_no_such_section(policied, capsys):
    """It exists to make audiences readable, so it earns its lines or it has
    none."""
    assert main(["list"]) == 0

    assert "groups that require others" not in capsys.readouterr().out


def test_a_conjunction_survives_the_json_round_trip(cfg, monkeypatch):
    """`json` holds neither a set nor a tuple, so this is not a formality: an
    audience carrying a conjunction used to be unencodable outright."""
    import json

    from kingfisher import config_from_env, inventory
    from kingfisher.presentation.cli.listing import as_json

    _workspace(
        cfg, monkeypatch, BOTH, NAMED,
        vocabulary="groups:\n  A: {}\n  B: {}\n  ab: {all_of: [A, B]}\n",
    )

    # From the environment, not the fixture: the vocabulary is a file the
    # helper just wrote, and the fixture config predates it.
    document = json.loads(json.dumps(as_json(inventory(config_from_env()))))

    # Nested, not "A+B": a script should not have to parse a separator out of a
    # name, and a group name may legally contain one.
    assert document["audiences"]["agents"]["both"]["groups"] == [["A", "B"]]
    assert document["access"]["requires"]["ab"] == ["A", "B"]
    assert document["access"]["names"]["A"] == ["A"]


NARROWED = """name: narrowed
description: An agent.
groups: [A, B]
tools:
  - name: line_count
    groups: [C]
system_prompt: |
  You do the task.
"""


def test_an_entry_narrowing_past_its_definition_is_reported(cfg, monkeypatch, capsys):
    """It used to be refused. Now it runs and says so, because the same line is
    what somebody trying to widen would have written by accident."""
    _workspace(cfg, monkeypatch, NARROWED, vocabulary="groups: [A, B, C]\n")

    assert main(["list"]) == 0

    shown = capsys.readouterr().out
    assert "narrows past this definition's own audience" in shown
    assert "agent narrowed: tool line_count  [C]" in shown


def test_a_narrowed_entry_reaches_a_caller_holding_both(cfg, monkeypatch):
    """The report is not the point -- this is. A caller in A and C opens the
    agent and gets the tool; a caller in A alone opens it and does not.

    Asserted on the grant rather than on `list`, because the `--tools` section
    describes what the *workspace* offers and would answer a different
    question."""
    from kingfisher import config_from_env
    from kingfisher.domain.access import reaches
    from kingfisher.infrastructure.catalogue.agents import LocalAgentRepository

    _workspace(cfg, monkeypatch, NARROWED, vocabulary="groups: [A, B, C]\n")
    reach = config_from_env().access
    assert reach is not None
    spec = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs["narrowed"]

    assert spec.declares(reach.expand(["A", "C"])).tools == ("line_count",)
    assert spec.declares(reach.expand(["A"])).tools == ()
    # And the definition's own line still gates the agent itself: C alone opens
    # nothing, so there is no way to reach the tool by holding only C.
    assert not reaches(spec.groups, reach.expand(["C"]))


def test_a_callers_view_carries_no_audiences(policied, capsys):
    """Who else reaches a thing is the operator's question, not a caller's."""
    assert main(["list", "--as", "A"]) == 0

    shown = capsys.readouterr().out
    assert "by definition" not in shown
    assert "by tool" not in shown


def test_a_callers_view_drops_an_agent_they_cannot_open(cfg, monkeypatch, capsys):
    _workspace(cfg, monkeypatch, NARROW, vocabulary="groups: [A, B, C]\n")

    assert main(["list", "--as", "C"]) == 0

    assert "narrow" not in capsys.readouterr().out


def test_the_operator_still_sees_it(cfg, monkeypatch, capsys):
    """So the assertion above is not passing because the agent vanished."""
    _workspace(cfg, monkeypatch, NARROW, vocabulary="groups: [A, B, C]\n")

    assert main(["list"]) == 0

    assert "narrow" in capsys.readouterr().out


def test_listing_names_a_definition_that_restricts_nobody(cfg, monkeypatch, capsys):
    """Default-open, said where somebody will see it."""
    from tests.conftest import an_agent

    _workspace(cfg, monkeypatch)
    an_agent(cfg, "open_to_all")

    assert main(["list"]) == 0

    printed = capsys.readouterr().out
    assert "reachable by everyone" in printed
    assert "open_to_all" in printed


def test_naming_a_group_that_does_not_exist_is_refused(policied, capsys):
    assert main(["list", "--as", "Q"]) != 0

    assert "unknown group" in capsys.readouterr().err


def test_no_vocabulary_means_no_access_section(cfg, monkeypatch, capsys):
    from tests.conftest import an_agent

    an_agent(cfg, "plain")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0
    assert "access —" not in capsys.readouterr().out


def test_as_parses_a_comma_separated_list():
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("A,B") == ("A", "B")
    assert _held(" A , B ") == ("A", "B")


def test_as_unscoped_is_spelled_out_rather_than_implied():
    """An empty `--as` is far more likely to be a shell variable that did not
    expand than a considered decision to run with no caller."""
    from kingfisher.domain.access import UNSCOPED
    from kingfisher.presentation.cli.__main__ import _held

    assert _held("UNSCOPED") is UNSCOPED
    assert _held("") == ()


def test_the_listing_reports_a_definition_naming_an_undeclared_group(cfg, monkeypatch, capsys):
    """The listing is where somebody diagnosing this looks, and it goes through
    `inventory` rather than `Kingfisher` -- so the check has to be in both or
    the one place a reader would check shows a broken definition as ordinary.

    Reported rather than raised, which is this listing's rule: it is where you
    go *because* something is broken.
    """
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", groups="[analists]")
    (cfg.workspace / "groups.yaml").write_text("groups: [analysts]\n", encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 1, "a workspace that will not build is a non-zero listing"

    printed = capsys.readouterr().out
    assert "analists" in printed
    assert "analysts" in printed, "and the spelling that would have worked"


def test_the_listing_is_clean_when_every_group_is_declared(cfg, monkeypatch, capsys):
    """So the rule above is not passing because every listing says that."""
    from tests.conftest import an_agent

    an_agent(cfg, "analyst", groups="[analysts]")
    (cfg.workspace / "groups.yaml").write_text("groups: [analysts]\n", encoding="utf-8")
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0
    assert "cannot load" not in capsys.readouterr().out


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
    """What makes the verb compose. `> answer.md` has to hold the answer and
    nothing else, and `2>/dev/null` has to give silence."""
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
    """A reviewer's working notes are not what you asked for. On one stream the
    speaker tag is what keeps them apart; on two, the streams are."""
    from kingfisher import RunEvent

    _ran(monkeypatch, [RunEvent(kind="token", text="mine"),
                       RunEvent(kind="token", text="theirs", agent="reviewer"),
                       _finished()], cfg)

    main(["run", "t", "--agent", "assistant"])

    shown = capsys.readouterr()
    assert shown.out == "mine"
    assert "theirs" in shown.err


def test_a_turn_stopped_at_a_bound_exits_non_zero(cfg, monkeypatch, capsys):
    """`kingfisher run ... > report.md && publish report.md` must not publish a
    report that stopped halfway. stdout is prose, so the exit code is the only
    thing a script has to read."""
    _ran(monkeypatch, [_finished(stop_reason="max_steps")], cfg)

    assert main(["run", "t", "--agent", "assistant"]) == 1
    assert "max_steps" in capsys.readouterr().err


def test_the_agent_is_required_because_there_is_no_honest_default(cfg, monkeypatch):
    """An agent decides which endpoint the prompts go to and whose credentials
    pay. A default would put that choice where the command line never says it."""
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
    """`--as` is not decoration: on a workspace that declares groups the library
    refuses a turn that names nobody, and names this flag when it does."""
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
    """They are the same directory until a deployment moves them, and a preset
    written to `workspace/skills` is invisible to an agent reading the relocated
    one -- the bug this has caught before."""
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
    """The default, and the case the old code got right -- worth keeping, or the
    fix above could quietly break the ordinary setup."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))

    assert main(["seed"]) == 0
    assert LocalSkillRepository(cfg.skills_dir).names


def test_seeding_puts_tools_in_the_tool_catalogue(cfg, tmp_path, shipped, monkeypatch):
    """The third catalogue, and the third chance to seed where nothing reads.

    `KINGFISHER_TOOLS_DIR` relocates it the way the other two do, so a tool
    written to `workspace/tools` would be invisible to the agent for exactly the
    reason that was fixed for skills.
    """
    from kingfisher.infrastructure.catalogue.tools import LocalToolRepository

    catalogue = tmp_path / "catalogue"
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_ASSETS", str(shipped))
    monkeypatch.setenv("KINGFISHER_TOOLS_DIR", str(catalogue / "tools"))

    assert main(["seed"]) == 0

    assert "http_fetch" in LocalToolRepository(catalogue / "tools").names
    # `ensure_layout` still makes the workspace directory, so the place to put
    # one is obvious. What must not happen is a preset landing in it.
    assert LocalToolRepository(cfg.workspace / "tools").names == ()

"""`kingfisher seed` and `kingfisher list`, and the rule they are held to.

The command exists because a pip-installed kingfisher had the definitions and no
way to put them anywhere: both operations lived behind flags in `main.py`, which
is a development driver and is not in the wheel.

Held to the front door, like `kingfisher.presentation`. `test_architecture`
enforces that against every module here; these are about what the command does.
"""

from __future__ import annotations

import pytest

from kingfisher.cli.__main__ import main


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


def test_seeding_with_no_pack_installed_says_so(cfg, monkeypatch, capsys):
    """Kingfisher ships no definitions, so this is what an install without a
    pack does. Silence would read as success."""
    from kingfisher.infrastructure import seeding

    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setattr(seeding, "installed_packs", tuple)
    monkeypatch.setattr(seeding, "EXAMPLE", "nothing-here.example")

    assert main(["seed"]) == 0

    assert "nothing to seed" in capsys.readouterr().out


def test_listing_reports_a_workspace_that_will_not_load(cfg, monkeypatch, capsys):
    """Non-zero, because a listing gets read by scripts.

    Printed and returned apart, a caller could report a broken catalogue and
    exit 0 -- and the thing reading it carries on.
    """
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")  # or the endpoint is dropped
    cfg.subagents_dir.mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

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
    assert "`.env` is not loaded" in printed


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
    from kingfisher.cli.listing import render

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
    cfg.subagents_dir.mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "probe-agent.yaml").write_text(
        "name: probe-agent\ndescription: Something to list.\n"
        "system_prompt: |\n  Answer briefly.\n",
        encoding="utf-8",
    )
    # And a name two folders both claim, which is printed as a reference rather
    # than as a name plus the file it came from. The case the copy got wrong.
    for folder in ("team", "vendor"):
        (cfg.subagents_dir / folder).mkdir(parents=True, exist_ok=True)
        (cfg.subagents_dir / folder / "surveyor.yaml").write_text(
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
    from kingfisher.cli.__main__ import _verbs, build_parser

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
    from kingfisher.cli.listing import as_json

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
    from kingfisher.cli.listing import as_json

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
    cfg.subagents_dir.mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "broken.yaml").write_text("name: broken\n", encoding="utf-8")

    assert main(["list", "--json"]) == 1

    assert json.loads(capsys.readouterr().out)["subagents_error"]


def test_json_is_asked_for_rather_than_assumed(cfg, monkeypatch, capsys):
    """A listing whose default output is JSON is a listing nobody reads."""
    monkeypatch.setenv("KINGFISHER_WORKSPACE", str(cfg.workspace))
    monkeypatch.setenv("KINGFISHER_MODELS_FILE", str(_catalogue(cfg)))
    monkeypatch.setenv("FAKE_KEY", "not-a-real-key")

    assert main(["list"]) == 0

    assert not capsys.readouterr().out.lstrip().startswith("{")

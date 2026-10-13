"""The seeding flow a deployment writes, driven exactly as the README writes it."""

from __future__ import annotations

import pytest

from kingfisher import (
    WorkspacePaths,
    definitions_source,
    ensure_definitions,
    seed,
)
from kingfisher.config import ConfigError
from tests.conftest import repository_root
from tests.integration.seed_example import main, seed_definitions


@pytest.fixture
def assets_examples():
    """The tree this repository actually ships, not a planted one.

    A fixture would pass while `assets_examples/` held a definition no deployment could
    seed.
    """
    return repository_root() / "assets_examples"


def test_the_readme_flow_seeds_a_definitions_folder(tmp_path, assets_examples):
    """The four calls from the README, in the order it gives them."""
    paths = WorkspacePaths(tmp_path / "definitions")

    ensure_definitions(paths.definitions)
    source = definitions_source(paths, assets_examples)
    done = seed(paths, source)

    assert done.written, "the documented flow seeded nothing"
    assert not done.overwritten, "a fresh folder has nothing to overwrite"
    # What landed is loadable, which is the half `test_seeding.py` cannot see:
    # it asserts on what `seed` reported, and this asserts on what is on disk.
    # `general` rather than `assistant`, which names middleware and so arrives
    # only with `everything=True`.
    assert (paths.definitions / "agents" / "general.yaml").is_file()
    assert (paths.definitions / "skills").is_dir()


def test_laying_out_alone_writes_the_example_catalogue(tmp_path):
    """`models.yaml.example` arrives with the layout, before anything is seeded."""
    paths = WorkspacePaths(tmp_path / "definitions")

    ensure_definitions(paths.definitions)

    assert (paths.definitions / "models.yaml.example").is_file(), (
        "laying out the definitions folder no longer writes the example catalogue, so "
        "the README's ordering has stopped meaning anything"
    )


def test_the_flow_writes_nothing_into_a_workspace(tmp_path, assets_examples):
    """Seeding laid a workspace out and put `models.yaml.example` in it, where nothing
    reads it: the catalogue and every definition are read from the definitions folder.
    """
    workspace = tmp_path / "ws"
    paths = WorkspacePaths(tmp_path / "definitions", workspace=workspace)

    ensure_definitions(paths.definitions)
    seed(paths, definitions_source(paths, assets_examples))

    assert (paths.definitions / "models.yaml.example").is_file(), "the control"
    assert not workspace.exists(), "seeding made or wrote into the workspace"


def test_the_flow_reports_what_it_left_behind(tmp_path, assets_examples):
    """The field the README tells you to read, and why it tells you."""
    paths = WorkspacePaths(tmp_path / "definitions")
    ensure_definitions(paths.definitions)

    done = seed(paths, definitions_source(paths, assets_examples))

    assert done.skipped, (
        "the shipped assets_examples no longer demonstrate a definition seeding leaves "
        "behind, so the README's `skipped` paragraph has nothing behind it"
    )
    for left in done.skipped:
        assert left.label, "a skip with no label says nothing to a caller"
        assert left.names, "a skip has to name what it would have needed"
        assert not (paths.definitions / left.label).exists(), (
            f"{left.label} was reported skipped and copied anyway"
        )


def test_everything_takes_what_the_default_leaves(tmp_path, assets_examples):
    """The other half of the README paragraph, for a deployment that registered the
    names.
    """
    paths = WorkspacePaths(tmp_path / "definitions")
    ensure_definitions(paths.definitions)

    default = seed(paths, definitions_source(paths, assets_examples))
    complete = seed(paths, definitions_source(paths, assets_examples), everything=True)

    assert not complete.skipped
    assert set(default.written) < set(complete.written), (
        "`everything=True` seeded no more than the default, so the flag the "
        "README documents does nothing"
    )
    for left in default.skipped:
        assert (paths.definitions / left.label).is_file(), (
            f"{left.label} was left behind by the default and not taken by --all"
        )


def test_a_workspace_paths_is_destination_enough(tmp_path, assets_examples):
    """`seed` takes a destination, not a whole `Config`.

    Driven rather than asserted about the annotation, because what matters is that it
    works with no catalogue on disk and no workspace named -- the state this asserts by
    never writing either.
    """
    paths = WorkspacePaths(tmp_path / "definitions")
    assert paths.workspace is None, "no workspace, deliberately"
    assert not (paths.definitions / "models.yaml").exists(), "no catalogue yet, deliberately"

    assert seed(paths, definitions_source(paths, assets_examples)).written


def test_no_source_configured_says_how_to_name_one(tmp_path, monkeypatch):
    """Nothing ships, so there is no set to fall back on."""
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    paths = WorkspacePaths(tmp_path / "definitions")

    with pytest.raises(ConfigError, match="KINGFISHER_ASSETS"):
        definitions_source(paths)


# -- the script on the live shelf ----------------------------------------


def test_the_example_script_seeds_a_definitions_folder(tmp_path, assets_examples):
    """`tests/integration/seed_example.py` is driven, not just readable."""
    done = seed_definitions(tmp_path / "definitions", assets_examples)

    assert done.written
    assert (tmp_path / "definitions" / "agents" / "general.yaml").is_file()
    assert (tmp_path / "definitions" / "models.yaml.example").is_file(), (
        "the example stopped laying the folder out before seeding it"
    )


def test_the_example_script_takes_everything_too(tmp_path, assets_examples):
    """Both branches, because the flag is half of what the example teaches."""
    default = seed_definitions(tmp_path / "a", assets_examples)
    complete = seed_definitions(tmp_path / "b", assets_examples, everything=True)

    assert default.skipped
    assert not complete.skipped
    assert len(complete.written) > len(default.written)


def test_the_example_script_reports_what_it_left(tmp_path, assets_examples, capsys):
    """Its output is its point, so the output is what this asserts."""
    code = main(["--definitions", str(tmp_path / "definitions"), "--from", str(assets_examples)])
    printed = capsys.readouterr().out

    assert code == 0
    assert "seeded agents/general.yaml" in printed
    assert "skipped " in printed, "the example stopped reporting what it left behind"
    assert "seed again with --all" in printed, "a skip with no remedy is half a message"
    # The remedy this script used to get wrong, and the reason it prints the
    # record's own lines now: these names are declared in a file, and it said to
    # register them.
    assert "Declare the source ids in source_ids.yaml" in printed
    assert "register those" not in printed


def test_the_example_script_refuses_with_no_source_configured(tmp_path, monkeypatch, capsys):
    """The error path is part of the example -- it is what makes it pasteable rather
    than a snippet.
    """
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    definitions = tmp_path / "definitions"

    code = main(["--definitions", str(definitions)])

    assert code == 2, "a missing source is a configuration error, not an empty run"
    assert "KINGFISHER_ASSETS" in capsys.readouterr().err
    assert (definitions / "models.yaml.example").is_file(), (
        "the refusal left no laid-out folder, so the explicit `ensure_definitions` in "
        "the example buys nothing and the README's reason for it is wrong"
    )

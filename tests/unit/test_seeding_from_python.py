"""The seeding flow a deployment writes, driven exactly as the README writes it."""

from __future__ import annotations

import pytest

from kingfisher import (
    WorkspacePaths,
    definitions_source,
    ensure_layout,
    seed,
)
from kingfisher.config import ConfigError
from tests.conftest import repository_root
from tests.integration.seed_example import main, seed_workspace


@pytest.fixture
def assets_examples():
    """The tree this repository actually ships, not a planted one."""
    return repository_root() / "assets_examples"


def test_the_readme_flow_seeds_a_workspace(tmp_path, assets_examples):
    """The four calls from the README, in the order it gives them."""
    paths = WorkspacePaths(tmp_path / "ws")

    ensure_layout(paths.workspace, authored=paths.authored_files)
    source = definitions_source(paths, assets_examples)
    done = seed(paths, source)

    assert done.written, "the documented flow seeded nothing"
    assert not done.overwritten, "a fresh workspace has nothing to overwrite"
    # What landed is loadable, which is the half `test_seeding.py` cannot see:
    # it asserts on what `seed` reported, and this asserts on what is on disk.
    assert (paths.workspace / "agents" / "assistant.yaml").is_file()
    assert (paths.workspace / "skills").is_dir()


def test_laying_out_alone_writes_the_example_catalogue(tmp_path, assets_examples):
    """`models.yaml.example` arrives with the layout, before anything is seeded."""
    paths = WorkspacePaths(tmp_path / "ws")

    ensure_layout(paths.workspace, authored=paths.authored_files)

    assert (paths.workspace / "models.yaml.example").is_file(), (
        "laying out the workspace no longer writes the example catalogue, so the "
        "README's ordering has stopped meaning anything"
    )


def test_the_flow_follows_a_relocated_catalogue(tmp_path, assets_examples):
    """The README's first two calls, for a deployment that moved `models.yaml`."""
    shared = tmp_path / "shared"
    paths = WorkspacePaths(tmp_path / "ws", models_file=shared / "models.yaml")

    seed(paths, definitions_source(paths, assets_examples))

    assert (shared / "models.yaml.example").is_file(), (
        "seeding wrote the example where the catalogue is not read from"
    )


def test_the_flow_reports_what_it_left_behind(tmp_path, assets_examples):
    """The field the README tells you to read, and why it tells you."""
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)

    done = seed(paths, definitions_source(paths, assets_examples))

    assert done.skipped, (
        "the shipped assets_examples no longer demonstrate a definition seeding leaves "
        "behind, so the README's `skipped` paragraph has nothing behind it"
    )
    for left in done.skipped:
        assert left.label, "a skip with no label says nothing to a caller"
        assert left.names, "a skip has to name what it would have needed"
        assert not (paths.workspace / left.label).exists(), (
            f"{left.label} was reported skipped and copied anyway"
        )


def test_everything_takes_what_the_default_leaves(tmp_path, assets_examples):
    """The other half of the README paragraph, for a deployment that registered the
    names.
    """
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)

    default = seed(paths, definitions_source(paths, assets_examples))
    complete = seed(paths, definitions_source(paths, assets_examples), everything=True)

    assert not complete.skipped
    assert set(default.written) < set(complete.written), (
        "`everything=True` seeded no more than the default, so the flag the "
        "README documents does nothing"
    )
    for left in default.skipped:
        assert (paths.workspace / left.label).is_file(), (
            f"{left.label} was left behind by the default and not taken by --all"
        )


def test_a_workspace_paths_is_destination_enough(tmp_path, assets_examples):
    """`seed` takes a destination, not a whole `Config`."""
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)
    assert not (paths.workspace / "models.yaml").exists(), "no catalogue yet, deliberately"

    assert seed(paths, definitions_source(paths, assets_examples)).written


def test_no_source_configured_says_how_to_name_one(tmp_path, monkeypatch):
    """Nothing ships, so there is no set to fall back on."""
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    paths = WorkspacePaths(tmp_path / "ws")

    with pytest.raises(ConfigError, match="KINGFISHER_ASSETS"):
        definitions_source(paths)


# -- the script on the live shelf ----------------------------------------


def test_the_example_script_seeds_a_workspace(tmp_path, assets_examples):
    """`tests/integration/seed_example.py` is driven, not just readable."""
    done = seed_workspace(tmp_path / "ws", assets_examples)

    assert done.written
    assert (tmp_path / "ws" / "agents" / "assistant.yaml").is_file()
    assert (tmp_path / "ws" / "models.yaml.example").is_file(), (
        "the example stopped laying the workspace out before seeding it"
    )


def test_the_example_script_takes_everything_too(tmp_path, assets_examples):
    """Both branches, because the flag is half of what the example teaches."""
    default = seed_workspace(tmp_path / "a", assets_examples)
    complete = seed_workspace(tmp_path / "b", assets_examples, everything=True)

    assert default.skipped
    assert not complete.skipped
    assert len(complete.written) > len(default.written)


def test_the_example_script_reports_what_it_left(tmp_path, assets_examples, capsys):
    """Its output is its point, so the output is what this asserts."""
    code = main(["--workspace", str(tmp_path / "ws"), "--from", str(assets_examples)])
    printed = capsys.readouterr().out

    assert code == 0
    assert "seeded agents/assistant.yaml" in printed
    assert "skipped " in printed, "the example stopped reporting what it left behind"
    assert "run again with --all" in printed, "a skip with no remedy is half a message"


def test_the_example_script_refuses_with_no_source_configured(tmp_path, monkeypatch, capsys):
    """The error path is part of the example -- it is what makes it pasteable rather
    than a snippet.
    """
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    workspace = tmp_path / "ws"

    code = main(["--workspace", str(workspace)])

    assert code == 2, "a missing source is a configuration error, not an empty run"
    assert "KINGFISHER_ASSETS" in capsys.readouterr().err
    assert (workspace / "models.yaml.example").is_file(), (
        "the refusal left an unlaid-out workspace, so the explicit `ensure_layout` "
        "in the example buys nothing and the README's reason for it is wrong"
    )

"""The seeding flow a deployment writes, driven exactly as the README writes it."""

from __future__ import annotations

import pytest

from kingfisher import (
    WorkspacePaths,
    definitions_source,
    ensure_definitions,
    seed,
)
from kingfisher.core.config import ConfigError
from tests.conftest import repository_root


@pytest.fixture
def assets():
    """The tree this repository actually ships, not a planted one.

    A fixture would pass while `assets/` held a definition no deployment could
    seed.
    """
    return repository_root() / "assets"


def test_the_readme_flow_seeds_a_definitions_folder(tmp_path, assets):
    """The four calls from the README, in the order it gives them."""
    paths = WorkspacePaths(tmp_path / "definitions")

    ensure_definitions(paths.definitions)
    source = definitions_source(paths.assets, assets)
    done = seed(paths.definitions, source)

    assert done.written, "the documented flow seeded nothing"
    assert not done.overwritten, "a fresh folder has nothing to overwrite"
    # What landed is loadable, which is the half `test_seeding.py` cannot see:
    # it asserts on what `seed` reported, and this asserts on what is on disk.
    # `operator` rather than `general`, which names middleware and so arrives
    # only with `everything=True`.
    assert (paths.definitions / "agents" / "operator.yaml").is_file()
    assert (paths.definitions / "skills").is_dir()


def test_the_flow_writes_nothing_into_a_workspace(tmp_path, assets):
    """Seeding laid a workspace out and put `models.yaml.example` in it, where nothing
    reads it: the catalogue and every definition are read from the definitions folder.
    """
    workspace = tmp_path / "ws"
    paths = WorkspacePaths(tmp_path / "definitions", workspace=workspace)

    ensure_definitions(paths.definitions)
    seed(paths.definitions, definitions_source(paths.assets, assets))

    assert (paths.definitions / "models.yaml.example").is_file(), "the control"
    assert not workspace.exists(), "seeding made or wrote into the workspace"


def test_the_flow_reports_what_it_left_behind(tmp_path, assets):
    """The field the README tells you to read, and why it tells you."""
    paths = WorkspacePaths(tmp_path / "definitions")
    ensure_definitions(paths.definitions)

    done = seed(paths.definitions, definitions_source(paths.assets, assets))

    assert done.skipped, (
        "the shipped assets no longer demonstrate a definition seeding leaves "
        "behind, so the README's `skipped` paragraph has nothing behind it"
    )
    for left in done.skipped:
        assert left.label, "a skip with no label says nothing to a caller"
        assert left.names, "a skip has to name what it would have needed"
        assert not (paths.definitions / left.label).exists(), (
            f"{left.label} was reported skipped and copied anyway"
        )


def test_everything_takes_what_the_default_leaves(tmp_path, assets):
    """The other half of the README paragraph, for a deployment that registered the
    names.
    """
    paths = WorkspacePaths(tmp_path / "definitions")
    ensure_definitions(paths.definitions)

    default = seed(paths.definitions, definitions_source(paths.assets, assets))
    complete = seed(paths.definitions, definitions_source(paths.assets, assets), everything=True)

    assert not complete.skipped
    assert set(default.written) < set(complete.written), (
        "`everything=True` seeded no more than the default, so the flag the "
        "README documents does nothing"
    )
    for left in default.skipped:
        assert (paths.definitions / left.label).is_file(), (
            f"{left.label} was left behind by the default and not taken by --all"
        )


def test_no_source_configured_says_how_to_name_one(tmp_path, monkeypatch):
    """Nothing ships, so there is no set to fall back on."""
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    paths = WorkspacePaths(tmp_path / "definitions")

    with pytest.raises(ConfigError, match="KINGFISHER_ASSETS"):
        definitions_source(paths.assets)

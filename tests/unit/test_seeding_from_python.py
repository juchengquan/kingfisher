"""The seeding flow a deployment writes, driven exactly as the README writes it.

`test_seeding.py` beside this file tests `seed` itself -- what it copies, what
it leaves, what it reports. This tests the *four calls together*, in the order
the README gives them, against the real `examples/` tree rather than a planted
one.

Two different things can rot, which is why this is not folded in there. A change
to `seed` breaks the tests next door; a change to the shape a *caller* has to
write -- an argument that moves, a return field that is renamed, an ordering
that starts to matter -- breaks nothing until somebody follows the README and
finds it wrong. `Seeding` was renamed to `Seeded` while the README example still
parsed, because the example never touched the name.

Offline, so it lives on this shelf. The split under `tests/` is by what a test
*costs to run* rather than by what it is about: `tests/integration/` is the
shelf that reaches a real model and spends real money, and
`test_nothing_on_the_live_shelf_is_collected` refuses a `test_*.py` there for
exactly that reason. Seeding copies files and calls no model, so a seeding test
belongs here however end-to-end it is. The worked example on *that* shelf is
`driver.py`, which calls these same four in the same order.
"""

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


@pytest.fixture
def examples():
    """The tree this repository actually ships, not a planted one.

    The point of this file is that the documented flow works against the real
    thing -- a fixture would pass while `examples/` held a definition no
    deployment could seed.
    """
    return repository_root() / "examples"


def test_the_readme_flow_seeds_a_workspace(tmp_path, examples):
    """The four calls from the README, in the order it gives them."""
    paths = WorkspacePaths(tmp_path / "ws")

    ensure_layout(paths.workspace)
    source = definitions_source(paths, examples)
    done = seed(paths, source)

    assert done.written, "the documented flow seeded nothing"
    assert not done.overwritten, "a fresh workspace has nothing to overwrite"
    # What landed is loadable, which is the half `test_seeding.py` cannot see:
    # it asserts on what `seed` reported, and this asserts on what is on disk.
    assert (paths.workspace / "agents" / "assistant.yaml").is_file()
    assert (paths.workspace / "skills").is_dir()


def test_ensure_layout_comes_first_and_is_why(tmp_path, examples):
    """`models.yaml.example` has to arrive whether or not anything is seeded.

    The ordering in the README is load-bearing rather than stylistic: a
    deployment told to write `models.yaml` and given no example of one is the
    dead end `ensure_layout` running first exists to avoid. Pinned here because
    an example is exactly where an ordering silently stops mattering.
    """
    paths = WorkspacePaths(tmp_path / "ws")

    ensure_layout(paths.workspace)

    assert (paths.workspace / "models.yaml.example").is_file(), (
        "laying out the workspace no longer writes the example catalogue, so the "
        "README's ordering has stopped meaning anything"
    )


def test_the_flow_reports_what_it_left_behind(tmp_path, examples):
    """The field the README tells you to read, and why it tells you.

    A definition naming middleware or groups this deployment has not registered
    is refused when it is built, so `seed` leaves it and names what it needed. A
    caller that printed only `written` would hand somebody a workspace missing
    agents they can see in the source directory.

    Asserted as a property rather than a list of filenames: which definitions
    the shipped tree happens to have is `test_shipped_assets.py`'s business, and
    naming them here would make this fail every time one is added.
    """
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)

    done = seed(paths, definitions_source(paths, examples))

    assert done.skipped, (
        "the shipped examples no longer demonstrate a definition seeding leaves "
        "behind, so the README's `skipped` paragraph has nothing behind it"
    )
    for left in done.skipped:
        assert left.label, "a skip with no label says nothing to a caller"
        assert left.names, "a skip has to name what it would have needed"
        assert not (paths.workspace / left.label).exists(), (
            f"{left.label} was reported skipped and copied anyway"
        )


def test_everything_takes_what_the_default_leaves(tmp_path, examples):
    """The other half of the README paragraph, for a deployment that registered
    the names."""
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)

    default = seed(paths, definitions_source(paths, examples))
    complete = seed(paths, definitions_source(paths, examples), everything=True)

    assert not complete.skipped
    assert set(default.written) < set(complete.written), (
        "`everything=True` seeded no more than the default, so the flag the "
        "README documents does nothing"
    )
    for left in default.skipped:
        assert (paths.workspace / left.label).is_file(), (
            f"{left.label} was left behind by the default and not taken by --all"
        )


def test_a_workspace_paths_is_destination_enough(tmp_path, examples):
    """`seed` takes a destination, not a whole `Config`.

    That is what lets it run on a fresh workspace: the model catalogue is a file
    *inside* the workspace, so `config_from_env` raises before the directory
    exists. A signature that wanted a `Config` would be unusable exactly when
    seeding is needed.

    Driven rather than asserted about the annotation, because what matters is
    that it works with no catalogue on disk -- which is the state this asserts
    by never writing one.
    """
    paths = WorkspacePaths(tmp_path / "ws")
    ensure_layout(paths.workspace)
    assert not (paths.workspace / "models.yaml").exists(), "no catalogue yet, deliberately"

    assert seed(paths, definitions_source(paths, examples)).written


def test_no_source_configured_says_how_to_name_one(tmp_path, monkeypatch):
    """Nothing ships, so there is no set to fall back on.

    `definitions_source` refuses rather than guessing, and the refusal is the
    documentation: a caller who has set neither is told both ways to say where.
    """
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    paths = WorkspacePaths(tmp_path / "ws")

    with pytest.raises(ConfigError, match="KINGFISHER_ASSETS"):
        definitions_source(paths)

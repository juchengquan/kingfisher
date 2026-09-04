"""The seeding flow a deployment writes, driven exactly as the README writes it.

`test_seeding.py` beside this file tests `seed` itself -- what it copies, what
it leaves, what it reports. This tests the *four calls together*, in the order
the README gives them, against the real `assets_examples/` tree rather than a planted
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
from tests.integration.seed_example import main, seed_workspace


@pytest.fixture
def assets_examples():
    """The tree this repository actually ships, not a planted one.

    The point of this file is that the documented flow works against the real
    thing -- a fixture would pass while `assets_examples/` held a definition no
    deployment could seed.
    """
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
    """`models.yaml.example` arrives with the layout, before anything is seeded.

    This used to be called `test_ensure_layout_comes_first_and_is_why` and said
    the README's ordering was load-bearing -- "pinned here because an example is
    exactly where an ordering silently stops mattering". It then stopped
    mattering: `seed` lays the workspace out itself now, so the explicit call is
    belt-and-braces for the path `seed` never reaches, and the docstring went on
    claiming otherwise. The assertion was right the whole time; only the reason
    was stale.

    What it pins is still worth pinning: laying out is what produces the example
    catalogue, and a deployment told to write `models.yaml` and given no example
    of one is a dead end. That `seed` alone also gets there is
    `test_seeding_alone_leaves_a_workspace_that_can_start`, next door.
    """
    paths = WorkspacePaths(tmp_path / "ws")

    ensure_layout(paths.workspace, authored=paths.authored_files)

    assert (paths.workspace / "models.yaml.example").is_file(), (
        "laying out the workspace no longer writes the example catalogue, so the "
        "README's ordering has stopped meaning anything"
    )


def test_the_flow_follows_a_relocated_catalogue(tmp_path, assets_examples):
    """The README's first two calls, for a deployment that moved `models.yaml`.

    `seed` lays the workspace out itself, so the library route reaches
    `ensure_layout` through the record rather than through the caller -- which
    is where the relocation would be dropped if only the CLI passed it on.
    """
    shared = tmp_path / "shared"
    paths = WorkspacePaths(tmp_path / "ws", models_file=shared / "models.yaml")

    seed(paths, definitions_source(paths, assets_examples))

    assert (shared / "models.yaml.example").is_file(), (
        "seeding wrote the example where the catalogue is not read from"
    )


def test_the_flow_reports_what_it_left_behind(tmp_path, assets_examples):
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
    """The other half of the README paragraph, for a deployment that registered
    the names."""
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

    assert seed(paths, definitions_source(paths, assets_examples)).written


def test_no_source_configured_says_how_to_name_one(tmp_path, monkeypatch):
    """Nothing ships, so there is no set to fall back on.

    `definitions_source` refuses rather than guessing, and the refusal is the
    documentation: a caller who has set neither is told both ways to say where.
    """
    monkeypatch.delenv("KINGFISHER_ASSETS", raising=False)
    paths = WorkspacePaths(tmp_path / "ws")

    with pytest.raises(ConfigError, match="KINGFISHER_ASSETS"):
        definitions_source(paths)


# -- the script on the live shelf ----------------------------------------


def test_the_example_script_seeds_a_workspace(tmp_path, assets_examples):
    """`tests/integration/seed_example.py` is driven, not just readable.

    Nothing collects that file -- it is deliberately not named `test_*.py`,
    because a bare `pytest` must not run what lives on that shelf -- so without
    this it would be a worked example with no run to fail on, which is exactly
    the condition an example rots under. `call_cap.py` is driven by a scripted
    model for the same reason.

    Imported rather than shelled out to, so a signature that changes fails here
    with a `TypeError` naming it rather than a non-zero exit code.
    """
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
    """Its output is its point, so the output is what this asserts.

    A caller reading only `written` is the mistake the script exists to
    demonstrate against; if `main` stopped printing the skips it would still
    seed correctly and still teach the wrong thing.
    """
    code = main(["--workspace", str(tmp_path / "ws"), "--from", str(assets_examples)])
    printed = capsys.readouterr().out

    assert code == 0
    assert "seeded agents/assistant.yaml" in printed
    assert "skipped " in printed, "the example stopped reporting what it left behind"
    assert "run again with --all" in printed, "a skip with no remedy is half a message"


def test_the_example_script_refuses_with_no_source_configured(tmp_path, monkeypatch, capsys):
    """The error path is part of the example -- it is what makes it pasteable
    rather than a snippet.

    And it is the whole reason the example still calls `ensure_layout` itself.
    `seed` lays a workspace out, but `definitions_source` refuses *before*
    seeding starts, so this is the one path `seed` never reaches: without the
    explicit call the deployment gets an empty directory and a traceback, and
    with it a laid-out workspace and an error that explains itself.

    Asserted rather than left to the prose, because the prose here has already
    gone stale once -- see `test_laying_out_alone_writes_the_example_catalogue`.
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

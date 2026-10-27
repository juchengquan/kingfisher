"""One list of the kinds, and everything by-kind checked against it.

The vocabulary is shared and the shapes are not, so what these tests ask is only that a
kind is either handled or written down as deliberately absent.

Adding a ninth axis to `Capabilities` passed the whole suite, ruff and ty without a
word, which is how a feature comes to silently not exist.
"""

from __future__ import annotations

from dataclasses import fields

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.subagents.catalogue import ASSET_DIRECTORIES
from tests.integration import driver

#: The vocabulary, derived here rather than imported. It was a constant on
#: `core.access.capabilities`, and nothing in the package ever read it -- only
#: these tests and the wire test in `service`, one of which re-derived it
#: anyway. A name published from the domain for its own tests to import is
#: API surface pointing the wrong way; `fields(Capabilities)` is the same
#: sentence and cannot drift from the type it asks.
AXES: tuple[str, ...] = tuple(f.name for f in fields(Capabilities))

#: What the workspace layout creates, and what each one is instead of a definition
#: kind. A directory in neither this table nor `DEFINITION_KINDS` is one a layout
#: makes and nothing reads.
NOT_A_KIND_DIRECTORY = {
    "sessions": "the unit of isolation; one backend root per session",
    ".kingfisher": "the harness's own, which the agent may read through but not write",
}


def test_the_definitions_layout_names_every_kind_a_catalogue_reads(tmp_path):
    """The layout named three of the five, so `agents` and `middlewares` -- each
    arriving after the tuple did -- were made by hand in tests, and the same call
    spread to the three that already existed, 69 times across 28 files.

    Driven rather than read off a tuple: the kinds come from `definition_roots_for`,
    and a check on a constant passes while the layout makes none of them.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS
    from kingfisher.core.workspace import ensure_definitions

    made = {p.name for p in ensure_definitions(tmp_path / "definitions").iterdir() if p.is_dir()}

    assert made == set(DEFINITION_KINDS), (
        "the definitions folder is laid out with a directory that is not a kind a "
        "catalogue reads, or without one that is"
    )


def test_the_workspace_layout_makes_no_kind(tmp_path):
    """A kind folder in the workspace is a place to write a definition that is never
    read, because every kind is read from the definitions folder.
    """
    from kingfisher.core.layout import LAYOUT_DIRS
    from kingfisher.core.workspace import ensure_layout

    made = {p.name for p in ensure_layout(tmp_path / "ws").iterdir() if p.is_dir()}

    assert made == set(NOT_A_KIND_DIRECTORY), (
        "the workspace layout makes a directory that is not written down here"
    )
    assert set(LAYOUT_DIRS) == set(NOT_A_KIND_DIRECTORY)


def test_the_first_read_makes_no_folder(cfg, tmp_path):
    """A first read that made the kinds it did not find would write into a folder
    several deployments read, and hide a mount that failed behind a catalogue that is
    merely empty. A kind with no folder is a kind with nothing in it.
    """
    from dataclasses import replace

    from kingfisher.core.definitions.catalogue import resolve_definitions

    bare = tmp_path / "bare"
    bare.mkdir()

    found = resolve_definitions(replace(cfg, definitions=bare))

    assert not list(bare.iterdir()), "the first read made a folder in the definitions folder"
    assert not found.agents.documents, "a kind with no folder read as holding something"


#: Why a kind is not read from the catalogue directories.
NOT_ON_DISK = {
    "builtin_tools": "deepagents brings them",
    "endpoints": "settings, not assets",
    "models": "settings, not assets",
    "memory": "a switch, not names",
}

#: Why a kind is not something a subagent may keep privately in its own folder.
#: A bundle is `subagents/<name>/tools/` and `subagents/<name>/skills/`, and the
#: two names it may use are reserved wherever they appear under `subagents/` --
#: so what a bundle can hold is a closed list, and this is the other half of it.
NOT_BUNDLED = {
    "builtin_tools": "deepagents brings them; there is no folder to put one in",
    "subagents": "a bundle is one subagent's own, so nesting makes 'whose' unanswerable",
    "middlewares": "granted by the deployment, so a subagent cannot bring its own",
    "endpoints": "settings, not assets",
    "models": "settings, not assets",
    "memory": "a switch, not names",
}


def test_what_a_subagent_may_bring_privately_is_accounted_for():
    """A kind a bundle neither holds nor is written off for is one nobody decided a
    subagent may or may not keep privately."""
    covered = set(ASSET_DIRECTORIES)

    assert covered | set(NOT_BUNDLED) == set(AXES)
    assert not covered & set(NOT_BUNDLED), "a kind cannot be both bundled and refused"


def test_a_bundle_holds_only_kinds_the_catalogue_reads():
    """A directory name a bundle used that the catalogue does not read would be a folder
    nothing ever looks in -- the same shape as
    `test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads`, one level
    down.
    """
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    assert set(ASSET_DIRECTORIES) <= set(DEFINITION_KINDS)


#: Why something the catalogue holds is not an axis a request narrows.
NOT_AN_AXIS = {
    "agents": "a request names one rather than narrowing a set of them",
}

#: Why a kind has no `--without-<kind>` flag. Not a CLI concern only: these are
#: the axes a *library* caller narrows differently too.
NOT_SUBTRACTABLE = {
    "middlewares": "selects code the deployment registered",
    "endpoints": "decides which credentials are used",
    "models": "an assignment, not a permission",
    "memory": "a switch, not names -- `--no-memory` says it",
}


def test_what_the_catalogue_loads_is_accounted_for():
    """One directory per field of `Definitions`."""
    covered = {f.name for f in fields(Definitions)}

    assert covered | set(NOT_ON_DISK) == set(AXES) | set(NOT_AN_AXIS)
    assert not covered & set(NOT_ON_DISK)
    assert set(NOT_AN_AXIS) <= covered, "a kind that is not an axis still has to be loaded"
    assert not set(NOT_AN_AXIS) & set(AXES)


def test_what_a_caller_can_subtract_is_accounted_for():
    """The driver exposes four."""
    assert set(driver.GRANTS) | set(NOT_SUBTRACTABLE) == set(AXES)
    assert not set(driver.GRANTS) & set(NOT_SUBTRACTABLE)


def test_a_ninth_axis_cannot_be_added_in_silence():
    """The point of all of the above."""
    unaccounted = set(AXES) - (set(driver.GRANTS) | set(NOT_SUBTRACTABLE))
    assert not unaccounted, f"{sorted(unaccounted)} is a kind nothing has decided about"


#: Directories under `assets/` that are not a catalogue kind, and why each is
#: there in spite of that.
NOT_A_KIND: dict[str, str] = {
    "plugins": (
        "Python that code embedding kingfisher imports, not a definition: the "
        "turn hook kept as an example, to copy rather than seed"
    ),
}


def test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads(shipped):
    """A kind the catalogue does not read would be copied where nothing looks."""
    from kingfisher.core.definitions.catalogue import DEFINITION_KINDS

    found = {p.name for p in shipped.iterdir() if p.is_dir() and not p.name.startswith("_")}

    assert found, "this repository's assets/ is empty -- this asserts nothing"
    assert found <= set(DEFINITION_KINDS) | set(NOT_A_KIND), (
        f"assets/ holds {sorted(found - set(DEFINITION_KINDS) - set(NOT_A_KIND))}, which "
        "is not a catalogue kind and would be copied where nothing looks. If it belongs "
        "there anyway, write it in NOT_A_KIND above with the reason"
    )
    assert set(NOT_A_KIND) <= found, (
        f"NOT_A_KIND names {sorted(set(NOT_A_KIND) - found)}, which assets/ does not "
        "hold -- an exception outliving the thing it excepted"
    )
    assert not set(NOT_A_KIND) & set(DEFINITION_KINDS), "an exception for a kind that is one"


def test_a_folder_that_is_not_a_kind_is_not_seeded_either(shipped, cfg):
    """The claim every `NOT_A_KIND` entry rests on, checked rather than asserted."""
    from kingfisher.core.definitions import seeding

    seeding.seed(cfg.definitions, shipped)

    for name in NOT_A_KIND:
        assert not (cfg.definitions / name).exists(), (
            f"{name}/ is written off as unseeded and seeding wrote it anyway"
        )
    # And the ordinary kinds did arrive, or the assertion above is vacuous. Their
    # contents rather than their folders, which the destination was laid out with.
    assert any((cfg.definitions / "agents").iterdir())


#: One definition per shape a skipped file can take, which `Skipped.wants` keys the
#: message table by.
SKIPPED_SHAPES = {
    "middleware.yaml": "middlewares: [audit]\n",
    "audienced.yaml": "source_ids: [A]\n",
    "datazone.yaml": "datazone: emea\n",
    "both.yaml": "source_ids: [A]\ndatazone: emea\n",
}


def test_every_reason_a_definition_is_skipped_has_a_remedy(cfg, tmp_path):
    """A reason `seed` reports and the table lacks would raise `KeyError` in the middle
    of the report; reading only `_deployment_specific`'s literal returns checked one of
    the four.

    Driven rather than parsed: the reasons are the ones `seed` actually reports, however
    each is built.
    """
    from kingfisher.core.definitions import seeding
    from kingfisher.core.definitions.seeding import HELD_BACK

    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    for name, body in SKIPPED_SHAPES.items():
        (source / "agents" / name).write_text(
            f"name: {name.removesuffix('.yaml')}\ndescription: d\n{body}", encoding="utf-8"
        )

    done = seeding.seed(cfg.definitions, source)
    reported = {left.wants for left in done.skipped}

    assert len(done.skipped) == len(SKIPPED_SHAPES), (
        f"seed copied {sorted(done.written)}, so not every shape was checked"
    )
    assert reported <= set(HELD_BACK), (
        f"{sorted(reported - set(HELD_BACK))} can be reported and HELD_BACK does not name it"
    )

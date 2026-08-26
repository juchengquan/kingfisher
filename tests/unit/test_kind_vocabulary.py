"""One list of the kinds, and everything by-kind checked against it.

`Capabilities` grew to eight axes. `main.GRANTS` names four, `Brought` has two
fields, `Definitions` has three -- and nothing kept them in step. Adding a ninth
axis passed the whole suite, ruff and ty without a word, which is how a feature
comes to silently not exist.

The vocabulary is shared; the shapes are not. Each type has its own field types
and its own defaults -- several of which carry a measurement -- so what these
tests ask is only that a kind is either handled or written down as deliberately
absent, in one place, with a reason.
"""

from __future__ import annotations

from dataclasses import fields

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.subagents import ASSET_DIRECTORIES
from kingfisher.infrastructure.uploads import Brought
from tests.integration import driver

#: The vocabulary, derived here rather than imported. It was a constant on
#: `domain.capabilities`, and nothing in the package ever read it -- only
#: these tests and the wire test in `service`, one of which re-derived it
#: anyway. A name published from the domain for its own tests to import is
#: API surface pointing the wrong way; `fields(Capabilities)` is the same
#: sentence and cannot drift from the type it asks.
AXES: tuple[str, ...] = tuple(f.name for f in fields(Capabilities))

#: Why a kind is not something a caller can upload. A definition arrives as a
#: document this package parses; these are not that.
NOT_UPLOADABLE = {
    "builtin_tools": "deepagents brings them; there is nothing to supply",
    "tools": "code, imported into this process -- never caller-supplied",
    "middleware": "selects code the deployment registered",
    "endpoints": "decides which credentials are used",
    "models": "an assignment, not a definition",
    "memory": "a switch, not names",
}

#: Why a kind is not read from the catalogue directories.
NOT_ON_DISK = {
    "builtin_tools": "deepagents brings them",
    "middleware": "registered in the process, not staged as files",
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
    "middleware": "registered in the process, not staged as files",
    "endpoints": "settings, not assets",
    "models": "settings, not assets",
    "memory": "a switch, not names",
}


def test_what_a_subagent_may_bring_privately_is_accounted_for():
    """`ASSET_DIRECTORIES` is two names. The other six are refusals.

    Here rather than beside the loader because that is what this file is for: a
    kind is either handled or written down as deliberately absent, in one place,
    with a reason. A seventh directory name added to a bundle without deciding
    what it means would otherwise be a feature that silently half-exists --
    which is the failure the whole file was written after measuring.
    """
    covered = set(ASSET_DIRECTORIES)

    assert covered | set(NOT_BUNDLED) == set(AXES)
    assert not covered & set(NOT_BUNDLED), "a kind cannot be both bundled and refused"


def test_a_bundle_holds_only_kinds_the_catalogue_reads():
    """A directory name a bundle used that the catalogue does not read would be
    a folder nothing ever looks in -- the same shape as
    `test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads`, one
    level down.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    assert set(ASSET_DIRECTORIES) <= set(DEFINITION_KINDS)


#: Why something the catalogue holds is not an axis a request narrows.
#:
#: The one entry is the whole reason this set exists: every other kind is a
#: *set* a request picks from, and an agent is the thing doing the picking. A
#: request names one, and the capabilities then apply to what that agent
#: declared -- so "narrow the agents" is not a sentence with a meaning.
#:
#: It is also the one kind a caller cannot upload, and that follows from the
#: same fact rather than being a second rule: what you would be uploading is
#: not a set to choose from, it is the thing choosing.
NOT_AN_AXIS = {
    "agents": "a request names one rather than narrowing a set of them",
}

#: Why a kind has no `--without-<kind>` flag. Not a CLI concern only: these are
#: the axes a *library* caller narrows differently too.
NOT_SUBTRACTABLE = {
    "middleware": "selects code, and `including` never widens it",
    "endpoints": "decides which credentials are used",
    "models": "an assignment, not a permission",
    "memory": "a switch, not names -- `--no-memory` says it",
}


def test_what_a_request_may_upload_is_accounted_for():
    """`Brought` has two fields. The other six are refusals, and the reason a
    caller cannot upload a tool is that `Request` has no `tool_refs` -- the
    field's absence is a consequence of that, not the rule itself.
    """
    covered = {f.name for f in fields(Brought)}

    assert covered | set(NOT_UPLOADABLE) == set(AXES)
    assert not covered & set(NOT_UPLOADABLE), "a kind cannot be both carried and refused"


def test_what_the_catalogue_loads_is_accounted_for():
    """Four directories. The five absent are settings or deepagents', and none
    of them is a thing to glob.

    The two sides stopped being the same list when agents arrived, and that is
    the finding rather than a wrinkle to paper over: a catalogue kind and a
    capability axis were the same vocabulary only while every kind was a set to
    choose from.
    """
    covered = {f.name for f in fields(Definitions)}

    assert covered | set(NOT_ON_DISK) == set(AXES) | set(NOT_AN_AXIS)
    assert not covered & set(NOT_ON_DISK)
    assert set(NOT_AN_AXIS) <= covered, "a kind that is not an axis still has to be loaded"
    assert not set(NOT_AN_AXIS) & set(AXES)


def test_what_a_caller_can_subtract_is_accounted_for():
    """The driver exposes four. The four it does not are the ones whose narrowing
    is not a list of names to remove."""
    assert set(driver.GRANTS) | set(NOT_SUBTRACTABLE) == set(AXES)
    assert not set(driver.GRANTS) & set(NOT_SUBTRACTABLE)


def test_a_ninth_axis_cannot_be_added_in_silence():
    """The point of all of the above. Adding a field to `Capabilities` and
    nothing else used to pass the suite, ruff and ty -- measured by doing it.
    """
    unaccounted = set(AXES) - (
        {f.name for f in fields(Brought)} | set(NOT_UPLOADABLE)
    )
    assert not unaccounted, f"{sorted(unaccounted)} is a kind nothing has decided about"


#: Directories under `examples/` that are not a catalogue kind, and why each is
#: there in spite of that.
#:
#: The rule below is that this repository's worked set holds only what a
#: catalogue reads, so that seeding it produces a workspace where every file is
#: found. An entry here is a folder that breaks the rule on purpose, and the
#: value is the argument for it -- written next to the check rather than in the
#: commit that added the folder.
#:
#: Kept honest by `test_a_folder_that_is_not_a_kind_is_not_seeded_either`: the
#: harm the rule names is a file "copied where nothing looks", so an exception
#: has to be a folder that is not copied at all.
NOT_A_KIND = {
    "middleware": (
        "deployment code, not a definition -- a middleware name selects code "
        "the deployment wrote, and one read out of the workspace would be code "
        "the agent can edit wrapped around the agent that edited it"
    ),
}


def test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads(shipped):
    """A kind the catalogue does not read would be copied where nothing looks.

    Asserted against the definitions that ship, which is where they come from.
    It opened every *installed pack* until they stopped being packs; before that
    it opened kingfisher's own tree, and after that tree stopped holding
    definitions it compared an empty set against the catalogue's -- true for the
    reason a test must never be true. Both times the fix was to point it at
    wherever the definitions actually are.

    There is still no second name for the list: the seeder uses the catalogue's
    directly. This checks the other side of that, which the seeder cannot -- a
    tree can hold anything.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    found = {p.name for p in shipped.iterdir() if p.is_dir() and not p.name.startswith("_")}

    assert found, "this repository's worked set is empty -- this asserts nothing"
    assert found <= set(DEFINITION_KINDS) | set(NOT_A_KIND), (
        f"examples/ holds {sorted(found - set(DEFINITION_KINDS) - set(NOT_A_KIND))}, which "
        "is not a catalogue kind and would be copied where nothing looks. If it belongs "
        "there anyway, write it in NOT_A_KIND above with the reason"
    )
    assert set(NOT_A_KIND) <= found, (
        f"NOT_A_KIND names {sorted(set(NOT_A_KIND) - found)}, which examples/ does not "
        "hold -- an exception outliving the thing it excepted"
    )
    assert not set(NOT_A_KIND) & set(DEFINITION_KINDS), "an exception for a kind that is one"


def test_a_folder_that_is_not_a_kind_is_not_seeded_either(shipped, cfg):
    """The claim every `NOT_A_KIND` entry rests on, checked rather than asserted.

    The rule above names one harm -- a folder "copied where nothing looks" --
    and an exception to it is only safe while that harm does not happen. `seed`
    walks `DEFINITION_KINDS`, so a folder outside the vocabulary is skipped
    rather than miscopied, and this drives it to make sure: seed this
    repository's own worked set and look at what landed.

    Without this the exception would be a promise about `seed` written in a
    dictionary that `seed` has never read.
    """
    from kingfisher.infrastructure import seeding

    seeding.seed(cfg, shipped)

    for name in NOT_A_KIND:
        assert not (cfg.workspace / name).exists(), (
            f"{name}/ is written off as unseeded and seeding wrote it anyway"
        )
    # And the ordinary kinds did arrive, or the assertion above is vacuous.
    assert (cfg.workspace / "agents").is_dir()

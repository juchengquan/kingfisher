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

import main as driver
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.uploads import Brought

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


def test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads():
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
    from kingfisher.infrastructure import seeding
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    with seeding.opened(seeding.ASSETS) as root:
        found = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")}

    assert found, "the shipped definitions are empty -- this asserts nothing"
    assert found <= set(DEFINITION_KINDS), (
        f"kingfisher ships {sorted(found - set(DEFINITION_KINDS))}, which is not a "
        "catalogue kind and would be copied where nothing looks"
    )


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
from kingfisher.domain.capabilities import AXES, Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.uploads import Brought

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

#: Why a kind has no `--without-<kind>` flag. Not a CLI concern only: these are
#: the axes a *library* caller narrows differently too.
NOT_SUBTRACTABLE = {
    "middleware": "selects code, and `including` never widens it",
    "endpoints": "decides which credentials are used",
    "models": "an assignment, not a permission",
    "memory": "a switch, not names -- `--no-memory` says it",
}


def test_the_vocabulary_is_derived_rather_than_written_twice():
    """If it were a hand-kept tuple it would be one more thing to drift."""
    assert tuple(f.name for f in fields(Capabilities)) == AXES
    assert len(AXES) == len(set(AXES))


def test_every_kind_is_a_field_on_capabilities_or_is_not_a_kind():
    """The type that owns the vocabulary has to cover all of it, by construction."""
    assert set(AXES) == {f.name for f in fields(Capabilities)}


def test_what_a_request_may_upload_is_accounted_for():
    """`Brought` has two fields. The other six are refusals, and the reason a
    caller cannot upload a tool is that `Request` has no `tool_refs` -- the
    field's absence is a consequence of that, not the rule itself.
    """
    covered = {f.name for f in fields(Brought)}

    assert covered | set(NOT_UPLOADABLE) == set(AXES)
    assert not covered & set(NOT_UPLOADABLE), "a kind cannot be both carried and refused"


def test_what_the_catalogue_loads_is_accounted_for():
    """Three directories. The five absent are settings or deepagents', and none
    of them is a thing to glob."""
    covered = {f.name for f in fields(Definitions)}

    assert covered | set(NOT_ON_DISK) == set(AXES)
    assert not covered & set(NOT_ON_DISK)


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


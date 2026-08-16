"""One list of the kinds, and everything by-kind checked against it.

`Capabilities` grew to eight axes. `main.GRANTS` names four, `Brought` has two
fields, `Catalogue` has three -- and nothing kept them in step. Adding a ninth
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
from kingfisher.infrastructure.catalogue import Catalogue
from kingfisher.infrastructure.uploads import Brought

#: Why a kind is not something a caller can upload. A definition arrives as a
#: document this package parses; these are not that.
NOT_UPLOADABLE = {
    "builtin_tools": "deepagents brings them; there is nothing to supply",
    "tools": "code, imported into this process -- never caller-supplied",
    "middleware": "selects code the deployment registered",
    "providers": "decides which credentials are used",
    "models": "an assignment, not a definition",
    "memory": "a switch, not names",
}

#: Why a kind is not read from the catalogue directories.
NOT_ON_DISK = {
    "builtin_tools": "deepagents brings them",
    "middleware": "registered in the process, not staged as files",
    "providers": "settings, not assets",
    "models": "settings, not assets",
    "memory": "a switch, not names",
}

#: Why a kind has no `--without-<kind>` flag. Not a CLI concern only: these are
#: the axes a *library* caller narrows differently too.
NOT_SUBTRACTABLE = {
    "middleware": "selects code, and `including` never widens it",
    "providers": "decides which credentials are used",
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
    covered = {f.name for f in fields(Catalogue)}

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


def test_the_wire_form_names_every_axis():
    """`CapabilitiesBody` is the HTTP shape, and the one that matters most for a
    library: a caller narrows over the wire, not through `main.py`. It mirrors
    `Capabilities` field for field today and nothing said so, which is how the
    two come to disagree about what a caller may ask for.

    Not a default check -- `test_capabilities_on_the_wire` already owns that.
    This is only that the vocabulary is the same on both sides.
    """
    from kingfisher.server.capabilities import CapabilitiesBody

    assert set(CapabilitiesBody.model_fields) == set(AXES)


def test_presets_seed_only_kinds_the_catalogue_reads():
    """A preset kind the catalogue does not read would be copied where nothing
    looks. There is no second name for the list -- `presets` uses the
    catalogue's directly, so this asserts the seeding matches what ships.
    """
    from kingfisher.infrastructure import presets
    from kingfisher.infrastructure.catalogue import CATALOGUE_KINDS

    with presets.opened() as root:
        shipped = {p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith("_")}

    assert shipped <= set(CATALOGUE_KINDS), (
        f"{sorted(shipped - set(CATALOGUE_KINDS))} ships as a preset but is not a catalogue kind"
    )

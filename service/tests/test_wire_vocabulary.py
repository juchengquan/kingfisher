"""The HTTP shape and the library's axes, checked against each other."""

from __future__ import annotations

from dataclasses import fields

from kingfisher_service.capabilities import CapabilitiesBody

from kingfisher.domain.capabilities import Capabilities

#: Derived from the type rather than imported as a constant. There was an
#: `AXES` on `domain.capabilities` for this, read by nothing in either
#: package -- only tests, and the other wire test derived it anyway.
AXES: tuple[str, ...] = tuple(f.name for f in fields(Capabilities))


def test_the_wire_form_names_every_axis():
    """`CapabilitiesBody` is the HTTP shape, and the one that matters most for a
    library: a caller narrows over the wire, not through a driver.
    """
    assert set(CapabilitiesBody.model_fields) == set(AXES)

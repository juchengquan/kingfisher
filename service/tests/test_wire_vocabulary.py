"""The HTTP shape and the library's axes, checked against each other.

Lived in the library's `test_kind_vocabulary` until the service became its own
distribution. It has to be here now: the library cannot import this package --
its own architecture test forbids exactly that -- so the side that *can* see
both is this one.

The direction of the check is unchanged. `AXES` is the library's vocabulary and
this asserts the wire mirrors it, rather than the other way round.
"""

from __future__ import annotations

from kingfisher_service.capabilities import CapabilitiesBody

from kingfisher.domain.capabilities import AXES


def test_the_wire_form_names_every_axis():
    """`CapabilitiesBody` is the HTTP shape, and the one that matters most for a
    library: a caller narrows over the wire, not through a driver. It mirrored
    `Capabilities` field for field and nothing said so, which is how the two
    come to disagree about what a caller may ask for.

    Not a default check -- `test_capabilities_on_the_wire` already owns that.
    This is only that the vocabulary is the same on both sides.
    """
    assert set(CapabilitiesBody.model_fields) == set(AXES)

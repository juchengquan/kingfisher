"""What a caller may ask for, across a wire that has one state too many."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from kingfisher import Capabilities

#: One axis as it arrives: `"*"`, a list of names, or `null`. Absent is the
#: fourth state and has no spelling here -- that is the point of it.
Axis = Literal["*"] | list[str] | None


class CapabilitiesBody(BaseModel):
    """The eight axes, with the lattice's own defaults declared."""

    # A caller sending an unknown axis has misunderstood something, and
    # answering 200 to a request to restrict something is the worst way to find
    # out. `extra="forbid"` makes it a 422 naming the field.
    model_config = ConfigDict(extra="forbid")

    builtin_tools: Axis = "*"
    tools: Axis = "*"
    skills: Axis = "*"
    # The literal rather than the library's constant: this is the wire, and a
    # JSON caller writes "*" because there is nothing else to write. The test
    # below holds it equal to the lattice's own default.
    subagents: Axis = "*"
    middleware: Axis = "*"
    endpoints: Axis = "*"
    models: Axis = None
    memory: Axis = None

    def selected(self) -> Capabilities:
        """The axes this request actually named, and no others."""
        named: dict[str, object] = {}
        for axis in self.model_fields_set:
            value = getattr(self, axis)
            named[axis] = tuple(value) if isinstance(value, list) else value
        return Capabilities(**named)  # ty: ignore[invalid-argument-type]

"""What a caller may ask for, across a wire that has one state too many.

`Selection` has three values -- `"*"` for everything, a list for exactly those,
`null` for none. JSON has four, because a key can also be *absent*, and absent
means "this deployment's default" while `null` means "grant me nothing". They
are opposite ends of the lattice on five of the eight axes.

Pydantic collapses them: a field declared with a default receives that default
whether the client sent `null` or sent nothing. So the two are told apart by
`model_fields_set`, which records what the request actually carried, and only
those axes are passed on -- every other one is left for the dataclass default to
supply. Nothing here restates what a default *is*; `test_capabilities_on_the_wire`
holds the two in step.

The direction of that mistake is not uniform, which is why it cannot be reasoned
about axis by axis in the head. `builtin_tools`, `tools`, `skills`, `middleware`
and `endpoints` default to `"*"`, so collapsing absent into null fails closed.
`models` and `memory` default to `None`, so the same collapse hands back the
default to a caller who explicitly asked for nothing. `subagents` was in that
list until an agent could declare its own roster; it defaults to `"*"` now, like
the five above it, and `"*"` means whatever that agent declares.

This is the second time this shape has caused trouble. `Selection` used to spell
"no opinion" as `None`, and the capabilities docstring records why it stopped:
"a JSON caller cannot tell an absent key from a null one, and both had to mean
everything -- the least safe reading of a missing field".

What bounds the cost of getting it wrong is the clamp. `Kingfisher` runs
`grants.intersect(request.capabilities)`, so a caller can only ever narrow
within what the deployment allowed. A bug here gives a caller more than *they*
asked for, never more than the deployment granted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

from kingfisher import Capabilities

#: One axis as it arrives: `"*"`, a list of names, or `null`. Absent is the
#: fourth state and has no spelling here -- that is the point of it.
Axis = Literal["*"] | list[str] | None


class CapabilitiesBody(BaseModel):
    """The eight axes, with the lattice's own defaults declared.

    The defaults are here so the generated schema tells the truth about what
    omitting a field means. They are not what makes omission work -- that is
    `model_fields_set` below -- so a wrong one would be a lie in the docs rather
    than a bug in the behaviour, which is the quieter failure and the reason a
    test compares them against the dataclass.
    """

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
        """The axes this request actually named, and no others.

        Lists become tuples here rather than in `Capabilities.__post_init__`,
        which normalises them as a documented backstop -- "a caller holding a
        list should convert at its own edge". This is that edge.
        """
        named: dict[str, object] = {}
        for axis in self.model_fields_set:
            value = getattr(self, axis)
            named[axis] = tuple(value) if isinstance(value, list) else value
        return Capabilities(**named)  # ty: ignore[invalid-argument-type]

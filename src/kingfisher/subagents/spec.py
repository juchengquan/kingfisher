"""What a subagent is, once a definition has been read.

A package rather than a module, because one file had become three subjects and said
so only by being the longest in the layer. `reading` turns a document into a spec and
owns the format -- every field, and what makes one malformed. `rules` holds what has
to be true across a *set* of specs, which is a different question from whether any
one of them is well-formed: two of a name, a cycle, a model that resolves to the
thing a delegate exists not to be.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kingfisher.domain.access import Audience, reaching
from kingfisher.domain.capabilities import ALL, Capabilities, Selection


class SubagentError(ValueError):
    """Raised when a subagent definition cannot be read."""


@dataclass(frozen=True)
class RunOn:
    """Where a request wants one delegate to run, instead of what its file says.

    One field, and it used to be two. There was a `provider` beside the model, with a
    rule that an override had to be wholesale -- never the file's endpoint joined to
    your model, because a model name sent somewhere that has never heard of it is a
    404 if you are lucky and a wrong-model run if you are not. A model resolves to
    its own endpoint through the catalogue now, so that pairing cannot be expressed
    and the rule has nothing left to guard.
    """

    model: str


@dataclass(frozen=True)
class SubagentSpec:
    """One subagent, as the workspace defines it."""

    name: str
    description: str
    #: The delegate's whole instruction -- or empty, when `build` carries it
    #: instead. Exactly one of the two is set, checked below rather than
    #: promised: a spec with neither builds a delegate with no instructions, and
    #: a spec with both has said one thing twice with no rule for which wins --
    #: which is the argument `model` and `alias` already make about themselves.
    system_prompt: str = ""
    #: The two tool axes, granted apart because they are offered apart: the
    #: built-ins come with deepagents, `tools` is what this workspace wrote.
    #: One list meant a delegate could not ask for a workspace tool without
    #: giving up every built-in, and nothing in the file showed it happening --
    #: the same trade `Offering.permitted` splits for a request, resolving each
    #: axis against its own offered set.
    builtin_tools: Selection = ALL
    tools: Selection = ALL
    #: Where each `tools:` entry claimed its tool lives, by name, for the entries
    #: written `where::what`. Beside `tools` rather than inside it, because a path is a
    #: claim to be checked and a name is what everything downstream keys on -- folding
    #: them together would make every consumer learn a spelling that only the checker
    #: cares about.
    tool_sources: Mapping[str, str] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: Skills this delegate is told about. `None` means *none*, which is not
    #: what `tools` means, and the difference is deliberate: tools are what a
    #: delegate needs to act, skills are what it needs to know -- and its body
    #: already is its procedure. Inheriting the caller's index would also put
    #: it in a context whose narrowness is the reason to delegate at all.
    skills: Selection = None
    #: Middleware this delegate runs with, by name, from a registry the
    #: deployment supplies. A name here selects *code*, which is why it is the
    #: one field never widened for an uploaded definition.
    middleware: Selection = None
    #: What each `middleware:` entry wrote under `settings:`, for the entries that wrote
    #: one. Keyed by name and kept beside them, the way `tool_sources` sits beside
    #: `tools`: a name is what gets granted and narrowed, and a value passed to the code
    #: behind it is neither.
    middleware_settings: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: Delegates this one may consult, by name, from the same catalogue. Absent means
    #: none -- like `skills`, and for the same reason: a delegate that needed the whole
    #: catalogue would not have been worth defining.
    subagents: Selection = None
    #: The model this delegate runs, out of what the catalogue defines. `None` means
    #: whatever summoned it. Naming one decides where the prompt goes and whose
    #: credentials pay -- the endpoint follows from the model -- which is why it is
    #: granted rather than free.
    wanted: str | None = field(default=None, metadata={"derived": True})
    #: The caller's own keys, carried and never interpreted. Kingfisher reads
    #: nothing here and never will: the moment it did, this would be a field
    #: with rules, and the point of it is to be the one place a definition can
    #: say something this format has no opinion about.
    #:
    #: Read by whatever loads the catalogue, not by the run -- see the module
    #: docstring for why the seam into a turn was left unbuilt.
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: What assembles this delegate, when a workspace declared it in Python rather than
    #: YAML. Called with a model and the tools it was granted, and it returns a graph
    #: deepagents runs as given.
    build: Any = field(default=None, metadata={"derived": True})
    #: Who may reach this delegate, wherever it is used.
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this delegate holds, narrowed to what one caller reaches."""
        if held is None:
            return Capabilities(
                builtin_tools=self.builtin_tools,
                tools=self.tools,
                skills=self.skills,
                subagents=self.subagents,
                middleware=self.middleware,
            )
        return Capabilities(
            builtin_tools=self.builtin_tools,
            tools=reaching(
                self.tools,
                audiences=self.audiences.get("tools", {}),
                default=self.groups,
                held=held,
            ),
            skills=reaching(
                self.skills,
                audiences=self.audiences.get("skills", {}),
                default=self.groups,
                held=held,
            ),
            subagents=reaching(
                self.subagents,
                audiences=self.audiences.get("subagents", {}),
                default=self.groups,
                held=held,
            ),
            middleware=self.middleware,
        )

    def __post_init__(self) -> None:
        """Exactly one of `system_prompt` and `build`."""
        if bool(self.system_prompt) == (self.build is not None):
            written = "both a system_prompt and a build" if self.system_prompt else "neither"
            msg = f"subagent {self.name!r} has {written}; a delegate is one or the other"
            raise ValueError(msg)

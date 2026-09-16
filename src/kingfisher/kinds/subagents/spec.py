"""What a subagent is, once a definition has been read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from kingfisher.domain.access import Audience, narrowed_for
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
    #: deployment supplies. A name here selects *code* the deployment wrote,
    #: which is why a request may narrow it and never add to it.
    middlewares: Selection = None
    #: What each `middlewares:` entry wrote under `settings:`, for the entries that wrote
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
    #: What this delegate's own folder holds, written down so the definition says it.
    #: `tools` and `skills`, each present only where the file wrote that half; empty
    #: is the ordinary case, since a bundle reaches its owner whether this names it
    #: or not and nothing here decides what is granted.
    #:
    #: Nested under one key rather than two fields beside `tools:` and `skills:`,
    #: and the indent is the point: every other list in a definition grants
    #: something, so one that describes instead has to look unlike them or it will
    #: be read as a grant and then as a bug when it grants nothing.
    #:
    #: Checked and never used, which is the whole of what it is for. A definition
    #: renamed out from under its folder, or a tool added to the folder and nowhere
    #: else, changes what a delegate holds with no line in any file to show it -- so
    #: a definition that has written this is refused once the two stop matching. The
    #: match is exact: a subset would let through the addition it exists to surface.
    bundle: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: The same two halves, brought rather than described: tool objects, and the
    #: directory a package resolved for its own skills. Written under `bundle:` like
    #: the claim above and kept in a field of its own, because the two are answers to
    #: one question and not one answer -- a claim is checked against a folder, and this
    #: *is* the folder for a definition that has none.
    #:
    #: Derived, because no document writes a key called `carried`. A YAML definition
    #: could not: the objects do not survive being written down, which is the whole
    #: reason a bundle is a folder there.
    #:
    #: Empty for every definition that owns a folder, and `__post_init__` refuses a
    #: spec holding both -- a delegate whose tools came from two places would have no
    #: rule saying which wins, and `miscounted` would check the claim against the
    #: wrong half.
    carried: Mapping[str, Any] = field(default_factory=dict, metadata={"derived": True})
    #: The model this delegate runs, out of what the catalogue defines. `None` means
    #: whatever summoned it. Naming one decides where the prompt goes and whose
    #: credentials pay -- the endpoint follows from the model -- which is why it is
    #: granted rather than free.
    wanted: str | None = field(default=None, metadata={"derived": True})
    #: The caller's own keys, carried and never interpreted. Kingfisher reads
    #: nothing here and never will: the moment it did, this would be a field
    #: with rules, and the point of it is to be the one place a definition can
    #: say something this format has no opinion about.
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: What assembles this delegate, when a workspace declared it in Python rather than
    #: YAML. Called with a model and the tools it was granted, and it returns a graph
    #: deepagents runs as given.
    build: Any = field(default=None, metadata={"derived": True})
    #: Who may reach this delegate, wherever it is used.
    source_ids: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this delegate holds, narrowed to what one caller reaches."""
        reached = narrowed_for(self, held)
        return Capabilities(
            builtin_tools=self.builtin_tools,
            tools=reached["tools"],
            skills=reached["skills"],
            subagents=reached["subagents"],
            middlewares=self.middlewares,
        )

    def __post_init__(self) -> None:
        """Exactly one of `system_prompt` and `build`, and never two kinds of bundle."""
        if bool(self.system_prompt) == (self.build is not None):
            written = "both a system_prompt and a build" if self.system_prompt else "neither"
            msg = f"subagent {self.name!r} has {written}; a delegate is one or the other"
            raise ValueError(msg)
        if self.bundle and self.carried:
            msg = (
                f"subagent {self.name!r} both describes a folder and carries a bundle; "
                "a delegate owns one or the other, since a claim is checked against a "
                "folder and carried tools are the folder"
            )
            raise ValueError(msg)

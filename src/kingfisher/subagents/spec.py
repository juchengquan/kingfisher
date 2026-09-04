"""What a subagent is, once a definition has been read.

A package rather than a module, because one file had become three subjects and
said so only by being the longest in the layer. `reading` turns a document into
a spec and owns the format -- every field, and what makes one malformed.
`rules` holds what has to be true across a *set* of specs, which is a different
question from whether any one of them is well-formed: two of a name, a cycle,
a model that resolves to the thing a delegate exists not to be.

The values stay here, which is the same shape `infrastructure.catalogue` took.
They are what the rest of the codebase means by "a subagent" -- `SubagentSpec`,
`SubagentError` and `RunOn`, imported by name from every layer -- so
`kingfisher.subagents.spec` goes on answering to them and those imports never
learned this happened. Nothing here imports `reading` or `rules`; a value knows
nothing about where it came from or what is refused about it, which is why the
split has no cycle in it and needs no import placed out of order to avoid one.

The long tail moved, and deliberately was not re-exported: `parse` is asked of
`reading` and `refuse_cycles` of `rules`, because a name read at its call site
should say which of the three subjects it belongs to.
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

    One field, and it used to be two. There was a `provider` beside the model,
    with a rule that an override had to be wholesale -- never the file's
    endpoint joined to your model, because a model name sent somewhere that has
    never heard of it is a 404 if you are lucky and a wrong-model run if you are
    not. A model resolves to its own endpoint through the catalogue now, so that
    pairing cannot be expressed and the rule has nothing left to guard.

    A name, not a `"provider:model"` string: that spelling is
    `init_chat_model`'s, and resolving a model through *it* is exactly what
    `infrastructure.harness.models` exists to avoid.
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
    #: Where each `tools:` entry claimed its tool lives, by name, for the
    #: entries written `where::what`. Beside `tools` rather than inside it,
    #: because a path is a claim to be checked and a name is what everything
    #: downstream keys on -- folding them together would make every consumer
    #: learn a spelling that only the checker cares about.
    #:
    #: Empty when every entry used the short form, which stays valid: the long
    #: one buys a check, and a definition that did not ask for one is not wrong.
    #:
    #: `derived`, so it is not in `KNOWN`. A definition cannot write this: it is
    #: read out of `tools`, and a `tool_sources:` key in a YAML file is refused
    #: like any other name this format does not define.
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
    #: What each `middleware:` entry wrote under `settings:`, for the entries
    #: that wrote one. Keyed by name and kept beside them, the way
    #: `tool_sources` sits beside `tools`: a name is what gets granted and
    #: narrowed, and a value passed to the code behind it is neither.
    #:
    #: `derived`, so it is not in `KNOWN`. It is read out of `middleware`, and
    #: a `middleware_settings:` key in a definition is refused like any other
    #: name this format does not define.
    middleware_settings: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: Delegates this one may consult, by name, from the same catalogue. Absent
    #: means none -- like `skills`, and for the same reason: a delegate that
    #: needed the whole catalogue would not have been worth defining.
    #:
    #: The only one of these five fields that refuses `["*"]`, and this is the
    #: sentence it refuses on. It was accepted for a while because all five share
    #: one type and one reader, not because anyone chose it -- and everything
    #: here includes the definition asking, so it was always a loop. Named
    #: delegates, or none.
    #:
    #: Any depth: a delegate named here may name its own. `refuse_cycles` is
    #: what stops a catalogue coming back to where it started, and it runs over
    #: the whole catalogue at load, so nobody writes a `subagents:` line that is
    #: silently ignored. Each definition is built once per position it holds
    #: rather than once per route to it, which is what makes reuse affordable.
    subagents: Selection = None
    #: The model this delegate runs, out of what the catalogue defines. `None`
    #: means whatever summoned it. Naming one decides where the prompt goes and
    #: whose credentials pay -- the endpoint follows from the model -- which is
    #: why it is granted rather than free.
    #:
    #: One name rather than a list of candidates. A list meant "try these in
    #: order", and the only thing that ever passed one over was an alias this
    #: deployment had not bound -- so when `alias` went, every entry after the
    #: first became unreachable. A model this deployment cannot run refuses on
    #: the spot and always did; there is nothing for a second choice to catch.
    #:
    #: `derived`, like `tool_sources`, and for the same reason: no definition
    #: writes `wanted:`. It is read out of `model:`, and a file spelling this
    #: field's own name is refused like any other key the format does not define.
    wanted: str | None = field(default=None, metadata={"derived": True})
    #: The caller's own keys, carried and never interpreted. Kingfisher reads
    #: nothing here and never will: the moment it did, this would be a field
    #: with rules, and the point of it is to be the one place a definition can
    #: say something this format has no opinion about.
    #:
    #: Read by whatever loads the catalogue, not by the run -- see the module
    #: docstring for why the seam into a turn was left unbuilt.
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: What assembles this delegate, when a workspace declared it in Python
    #: rather than YAML. Called with a model and the tools it was granted, and
    #: it returns a graph deepagents runs as given.
    #:
    #: `Any` for the same reason `tools.spec.Found.tool` is, and it is worth
    #: being as honest about it here. What this holds is, in practice, a
    #: callable returning a LangGraph graph. The domain never calls one, never
    #: imports the type and never depends on its shape -- it carries the thing
    #: from the loader that imported it to the adapter that runs it. If that
    #: stops being true, the fix is a domain-owned description of a graph, not a
    #: wider import here.
    #:
    #: `derived`, so it is not in `KNOWN`: a YAML document cannot write it, and
    #: writing `build:` in one is refused like any other key this format does
    #: not define. The Python declaration has its own key set, `DECLARED`.
    build: Any = field(default=None, metadata={"derived": True})
    #: Who may reach this delegate, wherever it is used.
    #:
    #: Its intrinsic ceiling rather than the whole answer: an agent naming it
    #: may narrow further for its own context, and the two intersect. `ALL`
    #: when the file says nothing, for the reason `AgentSpec.groups` gives.
    #:
    #: Enforced even for a compiled delegate, and it is the one thing that is:
    #: whether a graph gets built at all is kingfisher's decision, so there is
    #: nothing here for a graph to ignore.
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists.
    #:
    #: For a compiled delegate this narrows what is *handed* to `build` and is
    #: no more of a boundary than the plain `tools` list already is there --
    #: deepagents applies no allowlist to a graph it did not build, which
    #: `delegation.compiled` says out loud and `--list` flags.
    #:
    #: `derived`, because no definition writes `audiences:` -- it is read
    #: *out of* the three selection fields, the way `tool_sources` is read out
    #: of `tools`. Writing the key in a document is refused like any other the
    #: format does not define.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this delegate holds, narrowed to what one caller reaches.

        `held` is the caller's expanded groups, or `None` where this deployment
        declares no vocabulary or the call is `UNSCOPED` -- and `None` returns
        the selections untouched, which is what keeps every deployment that has
        not adopted audiences unchanged.

        The same shape `AgentSpec.declares` has, and the narrowing rule itself
        is shared rather than written twice: `access.reaching` is the one place
        that decides what an entry with no audience of its own inherits.
        """
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
        """Exactly one of `system_prompt` and `build`.

        A `ValueError` rather than `SubagentError`, and deliberately: both
        parsers refuse this case with a message naming the file, so reaching
        here means a spec was constructed in code. That is a programming
        mistake rather than a malformed definition, and the two should not
        arrive at the caller looking alike.
        """
        if bool(self.system_prompt) == (self.build is not None):
            written = "both a system_prompt and a build" if self.system_prompt else "neither"
            msg = f"subagent {self.name!r} has {written}; a delegate is one or the other"
            raise ValueError(msg)

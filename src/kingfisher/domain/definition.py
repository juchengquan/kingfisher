"""What an agent and a delegate both are, declared once.

Thirteen fields with the same type and the same default in either kind, and the two
that read them are held to each other by `test_format_parity`. They were declared
twice until the history was asked: every one of them means the same thing in both
files, and the comments explaining them had already begun to drift -- an agent's
`wanted` described a list of models for months after `wanted_model` stopped returning
one, with nothing red, because each file's comment only ever knew its own half.

**Where the kinds genuinely part, the comment says so rather than going quiet.** That
is the whole of what this file buys over two copies: a difference stated in one place
cannot drift, and `skills`, `subagents`, `wanted` and `source_ids` each mean something
slightly different to an agent than to a delegate. Each says which is which below.

`system_prompt` is *not* here, and that is the one field a reader should expect and
not find: an agent requires one with no default, a delegate defaults it to empty
because `build` is the alternative. A base holding it would have to pick, so each kind
declares its own -- which is also why all three are `kw_only`, since a required field
cannot otherwise follow the defaulted ones inherited from here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from kingfisher.domain.access import Audience
from kingfisher.domain.capabilities import ALL, Selection


@dataclass(frozen=True, kw_only=True)
class Definition:
    """The fields an agent and a delegate declare identically."""

    name: str
    description: str
    #: The two tool axes, granted apart because they are offered apart: the
    #: built-ins come with deepagents, `tools` is what this workspace wrote. One
    #: list meant a definition could not ask for a workspace tool without giving
    #: up every built-in, and nothing in the file showed it happening -- the same
    #: trade `Offering.permitted` splits for a request, resolving each axis
    #: against its own offered set.
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
    #: Skills this definition is told about. `None` means *none*, which is not what
    #: `tools` means, and the difference is deliberate: tools are what it needs to
    #: act, skills are what it needs to know.
    #:
    #: A delegate has a second reason, and it is why the two kinds read this key
    #: differently: its body already is its procedure, and inheriting its caller's
    #: index would put it in a context whose narrowness is the reason to delegate at
    #: all. So a delegate refuses `["*"]` here and an agent takes it.
    skills: Selection = None
    #: Definitions this one may consult, by name, from the same catalogue. Absent
    #: means none, like `skills`.
    #:
    #: **An agent may write `["*"]` and a delegate may not**, which is the other place
    #: the two formats part. For a delegate that set includes itself, so it is always
    #: a loop -- and a delegate that needed the whole catalogue would not have been
    #: worth defining.
    subagents: Selection = None
    #: Middleware this definition runs with, by name, from a registry the deployment
    #: supplies. A name here selects *code* the deployment wrote, which is why a
    #: request may narrow it and never add to it.
    middlewares: Selection = None
    #: What each `middlewares:` entry wrote under `settings:`, for the entries that
    #: wrote one. Beside the names rather than folded into them, like `tool_sources`
    #: beside `tools`: granting and narrowing are operations on names, and neither has
    #: anything to say about a value passed to the code behind one.
    middleware_settings: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: The model this definition runs, out of what the catalogue defines. Naming one
    #: decides where the prompt goes and whose credentials pay -- the endpoint follows
    #: from the model -- which is why it is granted rather than free.
    #:
    #: `None` means it named none, and **the two kinds fall back differently**: an
    #: agent runs the deployment's `default:`, a delegate runs whatever summoned it.
    #: This is the field whose two comments had already drifted apart, which is the
    #: argument for the pair being stated together.
    wanted: str | None = field(default=None, metadata={"derived": True})
    #: The caller's own keys, carried and never interpreted. Kingfisher reads nothing
    #: here and never will: the moment it did, this would be a field with rules, and
    #: the point of it is to be the one place a definition can say something the
    #: format has no opinion about.
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: Who may reach this definition: open a session on an agent, or reach a delegate
    #: wherever it is used.
    source_ids: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

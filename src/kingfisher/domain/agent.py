"""Agent definitions: `/agents/<name>.yaml`.

What a request runs. Every other format on the catalogue is something an agent
selects from -- the tools it holds, the skills it may read, the delegates it may
consult, the model it runs on -- and until now the answer to "which agent?" was
assembled from four places that did not know about each other: `prompts/system.md`
with the workspace's `PROMPT.md`, three switches in the environment, the
`default:` line in `models.yaml`, and whatever a request's `Capabilities`
narrowed to. Every delegate in `subagents/` was a reviewable document; the thing
that summoned them was not.

    name: surveyor
    description: Reads and profiles data without changing anything.
    builtin_tools: [read_file, ls, glob, grep]
    tools: [csv_profile::csv_profile]
    memory: false
    system_prompt: |
      You survey files before anyone trusts them.

`name`, `description` and `system_prompt` are required and nothing else is. The
prompt is required for the reason `description` is: an agent is a file somebody
else picks from, and those are the two fields that say what it is. A definition
without one is a list of tools with nothing anywhere saying what they are for --
`system.md` describes the harness and `PROMPT.md` describes the workspace, and
neither of them has ever heard of this agent. Writing one line is the cost; a
catalogue where every agent says what it does is what it buys.

**Its own folder and its own format, sharing the readers and not the fields.**
Two fields disagree with `subagents/`, and neither disagreement is cosmetic.
`memory` is a switch a delegate has no use for. And `system_prompt` means the
opposite thing -- see below. A shared folder would
have made a field's meaning depend on the request that read it rather than on
the file, which is exactly what nobody could then check by reading.

**`system_prompt` is added, never substituted.** A delegate's *is* the whole
prompt and it gets none of `system.md`, deliberately: that document is the
harness describing itself, and a delegate already has its own procedure. An
agent's is the last of three parts --

    prompts/system.md    what the harness is: /data is read-only, /skills is
                         loadable, where memory lives
    PROMPT.md            what this workspace is about, and it reaches delegates
    system_prompt        what this agent is

-- and there is no way to say "instead of". An agent without the first is not
leaner; it is one holding tools nobody told it about, discovering its permissions
by being denied.

The field keeps the name deepagents and Anthropic both use rather than gaining a
kingfisher-only one. What it costs is that a subagent file copied into `agents/`
parses cleanly and behaves differently, so the warning lives in the
documentation and in the seeded files rather than in an error.

**Omission means the same thing it means in a subagent file**, which is one
sentence per field rather than one per format: leave a *tool* field out and you
get everything available to you -- every built-in, every tool the workspace
defines -- and leave `skills` or `subagents` out and you get none. Tools are
what an agent needs to *act* and it can do nothing without them. Skills and
delegates are what it needs to *know* and to *ask*, and most agents need neither;
the skills index alone was measured at ~464 tokens for three, growing with the
catalogue, and every delegate compiles a graph at ~4.3ms.

`subagents: ["*"]` is the one place the two formats genuinely answer differently,
and the reason is in the files. In a subagent file "everything" includes the
definition doing the asking, so it is always a loop and is refused. An agent is
not one of the subagents, so here it means every delegate the workspace offers.

`model` reads exactly as it does for a delegate: one name, and a model this
deployment cannot run refuses rather than falling back. Omitted, the agent runs
the `default:` in `models.yaml`, which is what a file travelling between
deployments should say -- a vendor's model id is portable nowhere, so the
shipped agents name none and say in a comment what to pin them to.

Model *parameters* are not here and will not be. `models.yaml` carries
`max_tokens`, `temperature` and an `extra` bag for things like reasoning effort,
it has no credentials in it so it can go through review, and it is meant to be
the one place saying where prompts go and what they cost. An agent that wants
the same model to think harder names a second entry.

**A field this format does not define is refused, not ignored**, for the reason
the subagent format gives: a key we ignore is a key the author believes took
effect. The four that another format defines and this one declines are named
individually, because the generic message reads as "not supported yet" and sends
someone looking for a workaround.

Parsing lives in the domain because this is kingfisher's format. Nothing here
knows deepagents exists and nothing here reads a disk -- finding the files is
`infrastructure.catalogue.agents`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED, Audience, reaching
from kingfisher.domain.capabilities import ALL, Capabilities, Selection

# Imported rather than restated: both formats name a model the same way, so a
# second copy of the reader would be a second thing to keep in step, agreeing
# by coincidence until it does not. The dependency runs this way round because
# an agent already names delegates -- it depends on the subagent vocabulary
# whatever happens here.
from kingfisher.domain.subagent.reading import wanted_model
from kingfisher.domain.tool import claimed_sources

DIRECTORY = "agents"


class AgentError(ValueError):
    """Raised when an agent definition cannot be read."""


#: Every field this format defines. A key outside it is refused rather than
#: ignored: a definition writing `tolls:` would otherwise get an agent holding
#: every tool the workspace defines, since a missing `tools` means all of them.
KNOWN: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "system_prompt",
        "builtin_tools",
        "tools",
        "skills",
        "subagents",
        "middleware",
        "model",
        "memory",
        "metadata",
        "groups",
    }
)

#: Fields another format defines that this one deliberately does not, each with
#: the reason. Named separately because the generic message is misleading here:
#: it reads as "kingfisher has not got round to this" when the answer is that
#: honouring it would be wrong, or that it is a different piece of work.
REFUSED: Mapping[str, str] = MappingProxyType(
    {
        "permissions": (
            "deepagents' permissions *replace* rather than narrow, so writing this "
            "here would drop the rules an agent already has -- including the ones "
            "making /data and /skills read-only"
        ),
        "interrupt_on": (
            "an agent has both a checkpointer and a caller, unlike a delegate; what "
            "is missing is anything in the service that surfaces an interrupt to "
            "that caller"
        ),
        "response_format": (
            "an agent answers a real caller who may well want a schema, and there is "
            "nowhere to ask for one yet -- it changes what a *run returns*, so the "
            "result, the service's response body and streaming all have a stake in it"
        ),
    }
)


@dataclass(frozen=True)
class AgentSpec:
    """One agent, once its definition has been read.

    The values a request runs against. Everything is a *selection by name* apart
    from the prompt and the two switches, which is what keeps a definition
    reviewable: an agent file activates what the workspace already offers and
    cannot invent a tool or write a delegate's prompt.
    """

    name: str
    description: str
    #: Added after `system.md` and `PROMPT.md`, never instead of them. Required,
    #: and with no default here: `parse` refuses a definition that omits it, and
    #: a default would leave a second way in for something the format does not
    #: allow -- a spec built in code saying what no file may say.
    system_prompt: str
    builtin_tools: Selection = ALL
    tools: Selection = ALL
    #: Where each `tools:` entry said its tool lives, for the entries that said.
    #: A claim to check, never a choice between tools.
    tool_sources: Mapping[str, str] = field(default_factory=dict)
    skills: Selection = None
    subagents: Selection = None
    middleware: Selection = None
    #: What each `middleware:` entry wrote under `settings:`, for the entries
    #: that wrote one. Keyed by name, beside the names rather than folded into
    #: them, which is `tool_sources` beside `tools` and for the same reason:
    #: granting and narrowing are operations on names, and neither has anything
    #: to say about a value passed to one.
    #:
    #: Read against the class the deployment registered, by `approved_settings`
    #: at build time. Nothing here is checked when the file is parsed, because
    #: which keys a name accepts is declared by code this layer cannot see.
    middleware_settings: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict
    )
    #: What this agent asked to run, in the order it would prefer. Empty means
    #: it named nothing, so it runs the deployment's `default:`.
    wanted: str | None = None
    #: `False` to run without the memory file on a deployment that wired one.
    #: `None` is no opinion, which is not the same: a switch narrows like every
    #: other axis, and only `False` can subtract.
    memory: bool | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)
    #: Who may open a session on this agent.
    #:
    #: `ALL` when the file says nothing, because an absent optional field means
    #: no restriction everywhere else in this format. Reading it as "nobody"
    #: would stop every unannotated definition working the moment a vocabulary
    #: file appeared, which makes adopting access control all-or-nothing rather
    #: than incremental. What keeps that from being silent is the startup report
    #: naming every definition with no line here.
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists, which is
    #: every definition that predates audiences existing.
    #:
    #: Beside the selections rather than replacing them, so `tools` stays the
    #: `Selection` every consumer already reads -- `narrowed`, `Offering`, the
    #: allowlist -- and this is consulted only where a caller's groups are known.
    #:
    #: `derived`, because no definition writes `audiences:` -- it is read
    #: *out of* the three selection fields, the way `tool_sources` is read out
    #: of `tools`. Writing the key in a document is refused like any other the
    #: format does not define.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this agent holds, said as the narrowing a request is clamped by.

        The agent file is the baseline and a request only ever subtracts from
        it, so the two meet through the lattice that already exists rather than
        through a second set of rules. `agent.declares.intersect(asked)` is the
        whole of it: `ALL` on either side is the identity, `None` on either side
        wins outright, and a request naming what the agent did not is left with
        the overlap -- which is empty.

        `endpoints` and `models` are `ALL` because an agent has no opinion about
        either. They are grants a *deployment* makes -- which credentials may be
        spent, which model a caller may name for a delegate -- and an agent that
        narrowed them here would be a definition authorising itself.

        `held` is the caller's expanded groups, or `None` where this deployment
        declares no vocabulary or the call is `UNSCOPED`. `None` returns exactly
        what this returned before audiences existed, which is what keeps every
        deployment that has not adopted them unchanged -- and is why this is one
        method rather than a policied path beside an unpolicied one.

        `builtin_tools` is never narrowed here. deepagents registers those
        itself, so they can be filtered but never left out of a graph; what
        gates them is which *agents* a group may open, since an agent declaring
        a read-only builtin set cannot yield the shell to anyone.
        """
        return Capabilities(
            builtin_tools=self.builtin_tools,
            tools=self.tools if held is None else reaching(
                self.tools, audiences=self.audiences.get("tools", {}),
                default=self.groups, held=held,
            ),
            skills=self.skills if held is None else reaching(
                self.skills, audiences=self.audiences.get("skills", {}),
                default=self.groups, held=held,
            ),
            subagents=self.subagents if held is None else reaching(
                self.subagents, audiences=self.audiences.get("subagents", {}),
                default=self.groups, held=held,
            ),
            middleware=self.middleware,
            endpoints=ALL,
            models=ALL,
            memory=self.memory,
        )


def parse(document: Mapping[str, object], source: Path) -> AgentSpec:
    """One definition, from its decoded fields.

    Raises `AgentError` on anything the format forbids. Whether the document
    decoded at all was settled before this -- reading YAML needs a library, and
    a domain module imports the standard library and `kingfisher.domain`.
    """
    read = fields.Reader(source=source.name, error=AgentError)

    # Before the required-field check, so `nmae:` is reported as the typo it is
    # rather than as a missing `name` the author plainly tried to write.
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise AgentError(msg)

    for required in ("name", "description", "system_prompt"):
        # Absent and blank are different mistakes and read differently: "missing"
        # sends someone looking for a line they can see they wrote.
        if required not in document:
            msg = f"{source.name}: missing required field {required!r}"
            raise AgentError(msg)
        if not fields.text(document[required]):
            msg = f"{source.name}: {required!r} is present but empty"
            raise AgentError(msg)

    # Read once, then split. A `tools:` entry may be written `where::what`, and
    # only `what` may reach the rest of kingfisher; where it claims to live
    # travels beside it, for whoever checks the claim.
    written_tools, tool_audiences = read.audienced(
        document.get("tools"), absent=ALL, key="tools"
    )
    written_skills, skill_audiences = read.audienced(
        document.get("skills"), absent=None, key="skills"
    )
    written_delegates, delegate_audiences = read.audienced(
        # No `refuse_all` here, and that is the divergence worth reading twice.
        # A *subagent* naming every subagent names itself, which is always a
        # loop; an agent is not one of them, so this is the ordinary "give it
        # the run of the place".
        document.get("subagents"),
        absent=None,
        key="subagents",
    )
    audiences = {
        name: entries
        for name, entries in zip(
            AUDIENCED, (tool_audiences, delegate_audiences, skill_audiences), strict=True
        )
        if entries
    }
    groups = read.groups(document.get("groups"))
    # Read together, because they are one field. The names stay a `Selection`
    # and the settings ride beside them; `Reader.selection_with_settings` has
    # why the two halves are kept apart.
    #
    # `middleware` takes no audience, and that is not an oversight: it is the
    # one field naming *code the deployment registered* rather than something
    # the workspace offers, so it is granted rather than reachable.
    written_middleware, middleware_settings = read.selection_with_settings(
        document.get("middleware"), absent=None, key="middleware"
    )

    return AgentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        builtin_tools=read.selection(
            document.get("builtin_tools"), absent=ALL, key="builtin_tools"
        ),
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        skills=written_skills,
        subagents=written_delegates,
        groups=groups,
        audiences=audiences,
        middleware=written_middleware,
        middleware_settings=middleware_settings,
        wanted=wanted_model(document, read),
        # Absent is `None` rather than `False`, which `flag` alone cannot say:
        # a switch has three states here, and "no opinion" is not "no".
        memory=(
            None
            if document.get("memory") is None
            else read.flag(document.get("memory"), key="memory")
        ),
        metadata=read.mapping(document.get("metadata"), key="metadata"),
    )

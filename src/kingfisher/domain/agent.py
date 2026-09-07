"""Agent definitions: `/agents/<name>.yaml`.

What a request runs. Every other format on the catalogue is something an agent
selects from -- the tools it holds, the skills it may read, the delegates it may
consult, the model it runs on.

**Its own folder and its own format, sharing the readers and not the fields.** Two
fields disagree with `subagents/`: `memory` is a switch a delegate has no use for,
and `system_prompt` means the opposite thing. A shared folder would have made a
field's meaning depend on the request that read it rather than on the file.

**`system_prompt` is added, never substituted.** A delegate's *is* the whole prompt
and it gets none of `system.md`. An agent's is the last of three parts --

**Omission means the same thing it means in a subagent file:** leave a *tool* field
out and you get everything available to you, and leave `skills` or `subagents` out
and you get none. Tools are what an agent needs to *act* and it can do nothing
without them; skills and delegates are what it needs to *know* and to *ask*, and most
agents need neither. The skills index alone costs ~600 tokens for three -- ~450 of
that deepagents' own preamble, before a single skill is named -- while every delegate
compiles a graph at ~6ms. Re-measured 2026-09-03; `docs/findings.md` carries what
each figure is a measurement *of*.

**A field this format does not define is refused, not ignored:** a key we ignore is a
key the author believes took effect. The ones another format defines and this one
declines are named individually in `REFUSED` below, because the generic message reads
as "not supported yet" and sends someone looking for a workaround.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED, Audience, reaching
from kingfisher.domain.capabilities import ALL, Capabilities, Selection

# A `tools:` entry may be written `where::what`, so reading this format means
# parsing a tool reference -- the format referring to itself, which is why the
# domain may name an asset kind's `spec` at all. See the exception stated and
# measured in `test_domain_imports_only_the_standard_library_and_itself`.
from kingfisher.tools.spec import claimed_sources

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
    """One agent, once its definition has been read."""

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
    #: What each `middleware:` entry wrote under `settings:`. Beside the names rather
    #: than folded into them, like `tool_sources` beside `tools`: granting and narrowing
    #: are operations on names, and neither has anything to say about a value passed to
    #: one.
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
    groups: Audience = ALL
    #: Field name -> entry name -> who reaches that entry, for the fields in
    #: `AUDIENCED`. Empty for a definition written as plain lists.
    audiences: Mapping[str, Mapping[str, Audience]] = field(
        default_factory=dict, metadata={"derived": True}
    )

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this agent holds, said as the narrowing a request is clamped by."""
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
    """One definition, from its decoded fields."""
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
        # No `refuse_all` here. A *subagent* naming every subagent names itself,
        # which is always a loop; an agent is not one of them.
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
    # Read together, because they are one field. `middleware` takes no audience,
    # and that is not an oversight: it is the one field naming *code the deployment
    # registered* rather than something the workspace offers, so it is granted
    # rather than reachable.
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
        wanted=fields.wanted_model(document, read),
        # Absent is `None` rather than `False`, which `flag` alone cannot say:
        # a switch has three states here, and "no opinion" is not "no".
        memory=(
            None
            if document.get("memory") is None
            else read.flag(document.get("memory"), key="memory")
        ),
        metadata=read.mapping(document.get("metadata"), key="metadata"),
    )

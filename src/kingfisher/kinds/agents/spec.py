"""Agent definitions: `/agents/<name>.yaml`.

**Omission means the same thing it means in a subagent file:** leave a *tool* field out
and you get everything available, leave `skills` or `subagents` out and you get none.
The skills index alone costs ~600 tokens for three, ~450 of it deepagents' preamble,
while every delegate compiles a graph at ~6ms. Re-measured 2026-09-03.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED
from kingfisher.domain.capabilities import ALL, Capabilities
from kingfisher.domain.definition import Definition

# A `tools:` entry may be written `where::what`, so reading this format means
# parsing a tool reference -- the format referring to itself, which is why the
# domain may name an asset kind's `spec` at all. See the exception stated and
# measured in `test_domain_imports_only_the_standard_library_and_itself`.
from kingfisher.kinds.tools.spec import claimed_sources

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
        "middlewares",
        "model",
        "memory",
        "metadata",
        "source_ids",
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
            "is missing is anything that surfaces an interrupt to that caller"
        ),
        "response_format": (
            "an agent answers a real caller who may well want a schema, and there is "
            "nowhere to ask for one yet -- it changes what a *run returns*, so the "
            "result and streaming both have a stake in it"
        ),
    }
)


@dataclass(frozen=True, kw_only=True)
class AgentSpec(Definition):
    """One agent, once its definition has been read.

    The thirteen fields it shares with a delegate are on `Definition`; what is here
    is what only an agent has, or declares differently.
    """

    #: Added after `system.md` and `PROMPT.md`, never instead of them. Required,
    #: and with no default here: `parse` refuses a definition that omits it, and
    #: a default would leave a second way in for something the format does not
    #: allow -- a spec built in code saying what no file may say.
    #:
    #: Declared here rather than on `Definition` because a delegate defaults it to
    #: empty, having `build` as the alternative. A base would have to pick one of
    #: the two, and picking the delegate's would be this guarantee going quiet.
    system_prompt: str
    #: `False` to run without the memory file on a deployment that wired one.
    #: `None` is no opinion, which is not the same: a switch narrows like every
    #: other axis, and only `False` can subtract.
    memory: bool | None = None

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this agent holds, said as the narrowing a request is clamped by.

        The five a delegate answers identically come from `Definition`; these three
        are an agent's alone. `models` opens to everything because an agent decides
        which model its delegates may run, and `memory` is a field only it has.

        **`middlewares` is deliberately not among them**, and the contrast is the
        reason this is worth reading: middleware is not additive in effect --
        `call-cap-generous` is a *looser* ceiling than `call-cap-strict`, so a
        delegate free to name any registered entry could pick the roomiest one a
        deployment happens to offer and leave the bound its parent runs under. So it
        stays narrowed in the base, which is what makes an agent decide which its
        delegates may choose from, and why an agent lists one it does not use itself
        when a delegate needs it.
        """
        return replace(
            super().declares(held), endpoints=ALL, models=ALL, memory=self.memory
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
    source_ids = read.source_ids(document.get("source_ids"))
    # Read together, because they are one field. `middlewares` takes no audience,
    # and that is not an oversight: it is the one field naming *code the deployment
    # registered* rather than something the workspace offers, so it is granted
    # rather than reachable.
    written_middleware, middleware_settings = read.selection_with_settings(
        document.get("middlewares"), absent=None, key="middlewares"
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
        source_ids=source_ids,
        audiences=audiences,
        middlewares=written_middleware,
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

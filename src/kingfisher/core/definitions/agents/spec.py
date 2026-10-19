"""Agent definitions: `/agents/<name>.yaml`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from types import MappingProxyType

from kingfisher.core.access.capabilities import ALL, Capabilities, bare_name
from kingfisher.core.definitions import fields
from kingfisher.core.definitions.definition import SpecBase

# Other kinds' vocabulary: a `tools:` entry may be a `where::what` tool reference.
from kingfisher.core.definitions.subagents.spec import uncallable_helpers, unopenable_skills
from kingfisher.core.definitions.tools.spec import claimed_sources

DIRECTORY = "agents"


class AgentError(ValueError):
    """Raised when an agent definition cannot be read."""


#: Every field this format defines. Any other key is refused, not ignored: `tolls:` would
#: leave `tools` absent, so the agent would hold no tool and nothing would say why.
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
        "interrupt_on",
    }
)

#: Fields other formats define that this one declines, each with the reason, which the
#: generic "unknown field" refusal would misstate as not done yet.
REFUSED: Mapping[str, str] = MappingProxyType(
    {
        "permissions": (
            "deepagents' permissions *replace* rather than narrow, so writing this "
            "here would drop the rules an agent already has -- including the ones "
            "making /inputs and /skills read-only"
        ),
        "datazone": (
            "an agent runs in whichever datazone its caller names with each call; a "
            "datazone belongs on the subagents and tools the agent holds"
        ),
        "response_format": (
            "an agent answers a real caller who may well want a schema, and there is "
            "nowhere to ask for one yet -- it changes what a *run returns*, so the "
            "result and streaming both have a stake in it"
        ),
    }
)


@dataclass(frozen=True, kw_only=True)
class AgentSpec(SpecBase):
    """One agent, once its definition has been read."""

    #: `False` to run without the memory file a deployment wired. `None` is no opinion:
    #: the switch narrows like every other axis, and only `False` subtracts.
    memory: bool | None = None

    #: Tools whose every call stops for a person before it runs. Whole tools only: a pattern
    #: over shell commands can be phrased around (`x=rm; $x -rf`), and a gate that can be
    #: is worse than none. Not on `SpecBase`: a subagent inherits its parent's.
    interrupt_on: tuple[str, ...] = ()

    def declares(self, held: frozenset[str] | None = None) -> Capabilities:
        """What this agent holds, as the narrowing a request is clamped by.

        `endpoints` and `models` open to everything, because an agent decides which model
        its subagents may run. `middlewares` stays narrowed, deliberately: a looser cap is
        not a smaller grant, so a subagent free to name any registered middleware could
        pick one that escapes the bound its parent runs under.
        """
        return replace(
            super().declares(held), endpoints=ALL, models=ALL, memory=self.memory
        )


_NO_FOLDER = (
    "an agent has no folder of its own for it to come from. Put the file in the "
    "definitions folder's catalogue and name it plainly, or hand it to a subagent, whose own "
    "folder keeps it from every agent"
)


def parse(document: Mapping[str, object], source: Path) -> AgentSpec:
    """One definition, from its decoded fields."""
    read = fields.Reader(source=source.name, error=AgentError)

    # Before the required-field check, so `nmae:` reads as a typo, not a missing `name`.
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise AgentError(msg)

    for required in ("name", "description", "system_prompt"):
        # Absent and blank told apart: "missing" sends a reader hunting for a line they wrote.
        if required not in document:
            msg = f"{source.name}: missing required field {required!r}"
            raise AgentError(msg)
        if not fields.text(document[required]):
            msg = f"{source.name}: {required!r} is present but empty"
            raise AgentError(msg)

    # `source: shared` is accepted so an entry reads the same here as in a subagent file.
    # Absent grants none on both tool axes, as everywhere: a forgotten line fails closed.
    written_tools, _, _ = read.sourced(
        document.get("tools"),
        absent=None,
        key="tools",
        same=bare_name,
        refuse_bundled=_NO_FOLDER,
        locked=fields.TOOL_LOCK,
    )
    written_skills, skill_audiences, _ = read.sourced(
        document.get("skills"), absent=None, key="skills", refuse_bundled=_NO_FOLDER
    )
    written_subagents = read.names_only(
        # No `refuse_all`: `["*"]` loops only for a subagent, which would name itself.
        document.get("subagents"),
        absent=None,
        key="subagents",
        locked=fields.SUBAGENT_LOCK,
    )
    builtin = read.selection(document.get("builtin_tools"), absent=None, key="builtin_tools")
    # At load: at the call the allowlist refuses `task`, and the model quietly answers
    # without its subagents -- or `read_file`, and it spends a call on every skill it opens.
    complaint = uncallable_helpers(builtin, written_subagents) or unopenable_skills(
        builtin, written_skills
    )
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise AgentError(msg)
    source_ids = read.source_ids(document.get("source_ids"))
    # No audience, deliberately: `middlewares` names code the deployment registered,
    # which is granted rather than reachable.
    written_middleware, middleware_settings = read.selection_with_settings(
        document.get("middlewares"), absent=None, key="middlewares"
    )

    return AgentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        builtin_tools=builtin,
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        skills=written_skills,
        subagents=written_subagents,
        source_ids=source_ids,
        skill_audiences=skill_audiences,
        middlewares=written_middleware,
        middleware_settings=middleware_settings,
        wanted=fields.wanted_model(document, read),
        # Absent is `None`, not `False`: `flag` alone cannot say "no opinion".
        memory=(
            None
            if document.get("memory") is None
            else read.flag(document.get("memory"), key="memory")
        ),
        metadata=read.mapping(document.get("metadata"), key="metadata"),
        interrupt_on=_gated_tools(document.get("interrupt_on"), read),
    )


def _gated_tools(written: object, read: fields.Reader) -> tuple[str, ...]:
    """The tools this agent stops on, as names."""
    if isinstance(written, Mapping):
        # Before `selection`, whose mapping refusal is about who reaches an entry: this is
        # deepagents' `{tool: settings}`, and kingfisher writes that from the names.
        msg = (
            f"{read.source}: interrupt_on is a mapping; this field takes a list of tool "
            f"names -- write [{', '.join(map(str, written))}]"
        )
        raise read.error(msg)
    names = read.selection(
        written,
        absent=None,
        key="interrupt_on",
        refuse_all=(
            "everywhere else in this file the star means 'no limit', and gating every "
            "tool is the opposite. Name the tools that need a person."
        ),
    )
    if not isinstance(names, tuple):
        # Absent: no tool is gated. The star never gets here; `refuse_all` raises on it.
        return ()
    # Not a `where::what` reference, which `tools:` takes and this field cannot: a pause is
    # keyed by the name the call carries, so a reference reached deepagents matching nothing
    # and the gated tool ran. Refused rather than resolved to the bare name, because the
    # bare name stops every tool answering to it and the reference asks for one of them.
    if written_by_file := tuple(one for one in names if bare_name(one) != one):
        msg = (
            f"{read.source}: interrupt_on names {', '.join(written_by_file)} by file, "
            f"and a gate is keyed by the name a call carries -- write "
            f"{', '.join(bare_name(one) for one in written_by_file)}. A call carries "
            f"the name alone, so the gate stops every tool answering to it, wherever "
            f"each one is defined."
        )
        raise read.error(msg)
    return tuple(dict.fromkeys(names))

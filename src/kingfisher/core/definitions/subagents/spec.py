"""What a subagent is, and the vocabulary a definition says it in.

Every rule turning what a definition wrote into a spec is here, not in `reading`: split the
other way, every change to what a subagent may declare had to edit both files.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.core.access.capabilities import ALL, Selection, bare_name
from kingfisher.core.definitions import fields
from kingfisher.core.definitions.definition import SpecBase
from kingfisher.core.definitions.subagents import INTERPRETER_TOOL, TASK_TOOL
from kingfisher.core.definitions.tools.spec import claimed_sources

DIRECTORY = "subagents"


class SubagentError(ValueError):
    """Raised when a subagent definition cannot be read."""


@dataclass(frozen=True)
class RunOn:
    """Where a request wants one subagent to run, instead of what its file says.

    A model alone: it names its own endpoint, so it cannot be sent to one that has never
    heard of it.
    """

    model: str


@dataclass(frozen=True, kw_only=True)
class SubagentSpec(SpecBase):
    """One subagent, as the workspace defines it."""

    #: The `tools:` and `skills:` entries written `source: bundled`, by half: what this
    #: subagent takes from its own folder. Kept out of `tools` and `skills` because no
    #: grant narrows them -- activating the subagent grants its parts.
    bundled: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict, metadata={"derived": True}
    )
    #: The same two halves, brought rather than listed by a portable entry: tool objects,
    #: and the directories it resolved for its skills. This *is* the folder for a
    #: definition that has none, so a spec holds this or `bundled`, never both. Derived:
    #: no document can write it, since the objects do not survive being written down.
    carried: Mapping[str, Any] = field(default_factory=dict, metadata={"derived": True})
    #: For a subagent declared in Python: called with a model and the tools it was
    #: granted, it returns a graph deepagents runs as given.
    build: Any = field(default=None, metadata={"derived": True})
    #: The one datazone a caller must be in to reach it, or `None` for any. Not on
    #: `SpecBase`: an agent runs in whichever datazone its caller is in.
    datazone: str | None = None

    def __post_init__(self) -> None:
        """Exactly one of `system_prompt` and `build`, and never two kinds of bundle."""
        if bool(self.system_prompt) == (self.build is not None):
            written = "both a system_prompt and a build" if self.system_prompt else "neither"
            msg = f"subagent {self.name!r} has {written}; a subagent is one or the other"
            raise ValueError(msg)
        if any(self.bundled.values()) and self.carried:
            msg = (
                f"subagent {self.name!r} both lists entries from its folder and carries "
                "its own; a subagent owns one or the other, since a list is checked "
                "against a folder and carried tools are the folder"
            )
            raise ValueError(msg)


#: Every field this format defines. Anything else is refused rather than ignored: a
#: misspelt `tolls:` would leave the subagent holding no tool, with nothing saying why.
KNOWN: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "system_prompt",
        "builtin_tools",
        "tools",
        "skills",
        "middlewares",
        "subagents",
        "model",
        "metadata",
        "source_ids",
        "datazone",
    }
)

#: Why `bundle:` is refused, worded as the spelling to use: whoever writes it meant a
#: folder's contents.
NO_BUNDLE_KEY = (
    "a subagent's own tools and skills are listed where the rest are, written "
    "{name: mask_secrets, source: bundled} in tools: or skills:. Only what is "
    "listed there reaches the subagent, so add '*' to tools: as well if it should "
    "keep every catalogue tool it had"
)

#: deepagents' `SubAgent` fields this format does not take, each with its reason: the
#: generic refusal reads as *not yet*, where honouring one would be wrong.
REFUSED: Mapping[str, str] = MappingProxyType(
    {
        "permissions": (
            "deepagents' permissions *replace* the parent's rather than narrowing "
            "them, so writing this to tighten a subagent would drop the rules it "
            "already inherits -- including the one making /inputs read-only"
        ),
        "interrupt_on": (
            "a subagent inherits its parent's gates rather than declaring its own -- "
            "the caller answering a pause is the agent's caller, and a subagent that "
            "could widen or narrow what stops for them would be deciding that on "
            "their behalf"
        ),
        # deepagents does take one (`_compile_spec`), so the reason is what reaches the
        # parent: the reply is serialised into the `ToolMessage` it reads.
        "response_format": (
            "deepagents does support one here, and nothing structured survives it "
            "-- a subagent's response is serialised into the tool result, so its "
            "parent reads text either way and there is nothing for kingfisher to "
            "carry"
        ),
        "mode": (
            "deepagents' 'fork' hands a subagent its parent's whole conversation and "
            "state, private keys included, where kingfisher builds each subagent from "
            "what it was granted -- its own skills, tools and audience. Every subagent "
            "here runs isolated, which is what leaving this out means"
        ),
        "bundle": NO_BUNDLE_KEY,
    }
)

#: What a module must define: the subagents it contributes, as a sequence. Declared,
#: never inferred: a compiled subagent is a plain `dict`, and searching for one would find
#: imported names too, offering a subagent nobody meant to expose. Here rather than in
#: `catalogue` because the format's own refusals name it.
EXPORT = "SUBAGENTS"

#: Every key a *compiled* declaration, one carrying `build`, may write. Not `KNOWN`: a
#: shared set would be the union, permitting each format the other's keys.
DECLARED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "build",
        "tools",
        "model",
        "metadata",
        "source_ids",
        "datazone",
    }
)

#: Keys a compiled declaration may not write, each with its reason rather than as
#: unknown: a reader would expect each to work, and deepagents would ignore it.
NOT_COMPILED: Mapping[str, str] = MappingProxyType(
    {
        "builtin_tools": (
            "deepagents' own tools are built inside the parent's assembly and do "
            "not exist as objects when a subagent is put together, so there is "
            "nothing to hand a graph. A compiled subagent brings its own"
        ),
        "system_prompt": (
            "a compiled subagent brings its own graph, and whatever prompt it "
            "uses is inside it. Write the prompt where the graph is built"
        ),
        "skills": (
            "deepagents mounts skills for a subagent it builds; it runs a "
            "compiled graph as given and never adds a skills middleware to it. "
            "Read what the subagent needs inside the graph instead"
        ),
        "middlewares": (
            "middleware is wrapped around a graph deepagents builds. A compiled "
            "one is already built, so naming middleware here would be a line "
            "that does nothing"
        ),
        # deepagents' `CompiledSubAgent` keys, which this format does not take.
        "mode": REFUSED["mode"],
        "runnable": (
            "a compiled subagent's graph reaches kingfisher through `build`, which is "
            "called with the model and the tools this request allows -- a `runnable` "
            "is a graph assembled before any of that was known, so there would be "
            "nothing to narrow it against"
        ),
        "subagents": (
            "delegation reaches a subagent through the `task` tool its own "
            "middleware supplies, and a compiled graph is given no middleware. "
            "Build the nesting into the graph if it needs it"
        ),
        "bundle": NO_BUNDLE_KEY,
    }
)


#: The halves a subagent's own folder holds. Must match `catalogue.ASSET_DIRECTORIES`, or
#: the walk would skip a folder no definition could list from.
BUNDLE_KEYS: tuple[str, ...] = ("tools", "skills")


#: Every key a *portable* declaration, one with no `build`, may write: each answerable
#: without seeing the deployment it will be installed into.
PORTABLE: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "system_prompt",
        # deepagents' own names everywhere, and still narrowed by the request: one
        # withholding `execute` withholds it here.
        "builtin_tools",
        # The objects themselves, never names: the definition has never seen the catalogue.
        "tools",
        "skills",
        "metadata",
        # Names what only a deployment knows without depending on it: an id the
        # vocabulary lacks stops startup, and with no vocabulary the line is inert --
        # loud or nothing, never a subagent quietly open.
        "source_ids",
        # Likewise: a datazone the vocabulary does not declare stops the deployment.
        "datazone",
    }
)

#: Keys a portable declaration may not write, each with its reason: this format's own that
#: name something only the deployment knows, and deepagents' `SubAgent` and
#: `CompiledSubAgent` keys, which a spec typed to those TypedDicts writes.
NOT_PORTABLE: Mapping[str, str] = MappingProxyType(
    {
        "bundle": (
            "a portable entry carries its own under the plain fields: the tool "
            "objects in 'tools', and the absolute directory of its skills in 'skills'"
        ),
        "subagents": (
            "a helper has to be a catalogue entry, and an imported subagent is "
            "atomic -- what it owns reaches it and nothing else reaches that. Ship "
            "the helper as a subagent of its own for an agent to grant beside this "
            "one, or write 'build' and compose the graph yourself"
        ),
        "middlewares": (
            "it selects code the deployment wrote and registered, which is neither "
            "shipped with this definition nor nameable from outside"
        ),
        "model": (
            "it names a model profile only this deployment defines, and naming one "
            "decides where the prompt goes and whose credentials pay. A request pins "
            "any subagent's model by name with 'run_on', which is where the choice "
            "belongs"
        ),
        "permissions": REFUSED["permissions"],
        "interrupt_on": REFUSED["interrupt_on"],
        "response_format": REFUSED["response_format"],
        "mode": REFUSED["mode"],
        # Not `middlewares` misspelt: in a `SubAgent` it holds the middleware objects.
        "middleware": (
            "deepagents merges a spec's middleware into the stack it builds by name, "
            "so an entry sharing one replaces what was there -- and the "
            "FilesystemMiddleware its own docs suggest here would replace the one "
            "carrying this deployment's backend and every rule on it, /inputs "
            "read-only included. Middleware reaches a subagent only by the "
            "deployment registering it; write 'build' and wrap the graph yourself "
            "if this one needs some"
        ),
        "runnable": (
            "it holds a graph built before this deployment saw it, its model and "
            "tools already fixed, so nothing a request grants or withholds could "
            "reach it. Write 'build' instead: it is given a model and the tools "
            "this subagent was granted, and returns the graph to run"
        ),
    }
)


def _require(entry: Mapping[str, object], *names: str, source: str) -> None:
    """The fields a Python declaration must carry, present and not blank.

    Absent and blank stay different messages: "missing" sends somebody hunting for a line
    they can see they wrote.
    """
    for required in names:
        if required not in entry:
            msg = f"{source}: {EXPORT} entry is missing {required!r}"
            raise SubagentError(msg)
        if not fields.text(entry[required]):
            msg = f"{source}: {EXPORT} entry has {required!r} present but empty"
            raise SubagentError(msg)


def declared(entry: Mapping[str, object], source: str) -> SubagentSpec:
    """One entry of a module's `SUBAGENTS` into a spec.

    Two shapes, told apart by `build` and never by where the entry came from: a
    re-exported entry is indistinguishable from one written in the file.
    """
    if not isinstance(entry, Mapping):
        msg = (
            f"{source}: every entry of {EXPORT} must be a mapping with a "
            f"'name' and a 'description'; got {type(entry).__name__}"
        )
        raise SubagentError(msg)
    if "build" not in entry:
        return _portable(entry, source)

    if declined := sorted(set(entry) & set(NOT_COMPILED)):
        reasons = "; ".join(f"{key!r} -- {NOT_COMPILED[key]}" for key in declined)
        msg = f"{source}: {reasons}"
        raise SubagentError(msg)

    if unknown := sorted(set(entry) - DECLARED):
        msg = (
            f"{source}: {EXPORT} entry names {unknown}, which this format does not "
            f"define; it takes {sorted(DECLARED)}"
        )
        raise SubagentError(msg)

    # Not `build`, which chose this branch, nor `system_prompt`, which is refused above.
    _require(entry, "name", "description", source=source)

    build = entry["build"]
    if not callable(build):
        msg = (
            f"{source}: 'build' is {type(build).__name__}, which cannot be called. "
            f"It is given a model and the tools this subagent was granted, and "
            f"returns the graph to run"
        )
        raise SubagentError(msg)

    where = Path(source)
    read = fields.Reader(source=where.name, error=SubagentError)
    wanted = fields.wanted_model(entry, read)
    # A list of names, as a file writes it, and absent grants none: `["*"]` asks for every
    # tool the request granted. What each tool admits is in the tool's own file.
    written_tools, _, own_tools = read.sourced(
        entry.get("tools"), absent=None, key="tools", same=bare_name, locked=fields.TOOL_LOCK
    )
    source_ids = read.source_ids(entry.get("source_ids"))
    return SubagentSpec(
        name=fields.text(entry["name"]),
        description=fields.text(entry["description"]),
        # Empty: a compiled subagent's instruction is inside the graph it brings.
        system_prompt="",
        build=build,
        # Not `ALL`: a compiled graph is handed only workspace tools, so every built-in
        # would be a ceiling nothing fills, and `--run` would report tools withheld.
        builtin_tools=None,
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        bundled={"tools": own_tools, "skills": ()},
        wanted=wanted,
        metadata=read.mapping(entry.get("metadata"), key="metadata"),
        source_ids=source_ids,
        datazone=read.datazone(entry.get("datazone")),
    )


def _portable(entry: Mapping[str, object], source: str) -> SubagentSpec:
    """One `SUBAGENTS` entry with no `build`: a definition kingfisher assembles."""
    if declined := sorted(set(entry) & set(NOT_PORTABLE)):
        reasons = "; ".join(f"{key!r} -- {NOT_PORTABLE[key]}" for key in declined)
        msg = f"{source}: {reasons}"
        raise SubagentError(msg)

    if unknown := sorted(set(entry) - PORTABLE):
        msg = (
            f"{source}: {EXPORT} entry names {unknown}, which this format does not "
            f"define; a declaration without 'build' takes {sorted(PORTABLE)}"
        )
        raise SubagentError(msg)

    _require(entry, "name", "description", "system_prompt", source=source)

    read = fields.Reader(source=source, error=SubagentError)
    # Absent grants none, so a spec typed to deepagents' `SubAgent`, which has no word for
    # this, holds no built-in until the deployment's own file adds one.
    builtin = read.selection(entry.get("builtin_tools"), absent=None, key="builtin_tools")
    _refuse_the_sandbox(builtin, source=source)
    return SubagentSpec(
        name=fields.text(entry["name"]),
        description=fields.text(entry["description"]),
        system_prompt=fields.text(entry["system_prompt"]),
        builtin_tools=builtin,
        # No catalogue names, whatever it wrote: it holds what it carried and nothing else.
        tools=None,
        carried=_carried(entry, read),
        metadata=read.mapping(entry.get("metadata"), key="metadata"),
        source_ids=read.source_ids(entry.get("source_ids")),
        datazone=read.datazone(entry.get("datazone")),
    )


#: Why a portable entry's `tools` holds no names.
NO_NAMES_HERE = (
    "a portable entry's tools are the tool objects themselves; a name would be a "
    "lookup in a catalogue this definition has never seen, finding nothing on one "
    "machine and a different tool wearing the name on the next"
)


def _carried(entry: Mapping[str, object], reader: fields.Reader) -> Mapping[str, Any]:
    """What a portable entry brought in `tools` and `skills`."""
    carried: dict[str, Any] = {}
    if (tools := entry.get("tools")) is not None:
        # A list or tuple, nothing looser: a single tool is a pydantic model, which
        # iterates, so `tools: my_tool` would pass a duck test and loop over its fields.
        if not isinstance(tools, (list, tuple)):
            msg = (
                f"{reader.source}: tools is {type(tools).__name__}; it takes a "
                f"list of the tools themselves -- write tools: [my_tool]"
            )
            raise SubagentError(msg)
        # A mapping is a document's long form, and names rather than carries, as a string does.
        if named := [one for one in tools if isinstance(one, (str, Mapping))]:
            msg = f"{reader.source}: tools names {named!r} -- {NO_NAMES_HERE}"
            raise SubagentError(msg)
        carried["tools"] = tuple(tools)
    if (skills := entry.get("skills")) is not None:
        carried["skills"] = _skills_directories(skills, reader)
    return carried


def _skills_directories(value: object, reader: fields.Reader) -> tuple[Path, ...]:
    """The directories a definition resolved for its own skills.

    One path or a list, the list being deepagents' own `SubAgent["skills"]`. Each is a
    directory on this host, never a backend path: a definition written elsewhere cannot
    know this deployment's routes.
    """
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    if not values:
        msg = f"{reader.source}: skills is empty; leave it out for a subagent with none"
        raise SubagentError(msg)
    found = tuple(_skills_directory(one, reader) for one in values)
    # Either way round, a skill would be listed twice: once from each directory.
    for i, one in enumerate(found):
        for other in found[i + 1 :]:
            a, b = one.resolve(), other.resolve()
            if a.is_relative_to(b) or b.is_relative_to(a):
                msg = (
                    f"{reader.source}: skills names {str(one)!r} and {str(other)!r}, "
                    f"and one holds the other, so its skills would be offered twice"
                )
                raise SubagentError(msg)
    return found


def _skills_directory(value: object, reader: fields.Reader) -> Path:
    """One skills directory, absolute: a relative one depends on where kingfisher started."""
    if not isinstance(value, (str, Path)):
        msg = (
            f"{reader.source}: skills holds a {type(value).__name__}; it takes the "
            f"directory the skills are in, as a path, or a list of them"
        )
        raise SubagentError(msg)
    found = Path(value)
    if not found.is_absolute():
        msg = (
            f"{reader.source}: skills is {str(found)!r}, which is relative -- "
            f"it would be resolved against whatever directory kingfisher was started "
            f"in. Write an absolute path, which for a definition beside its own "
            f"skills is Path(__file__).parent / 'skills'"
        )
        raise SubagentError(msg)
    if not found.is_dir():
        msg = (
            f"{reader.source}: skills is {str(found)!r}, which is not a directory on "
            f"this host. A portable entry names its skills where they sit on disk, "
            f"not by a path inside the agent's backend such as '/skills/...'"
        )
        raise SubagentError(msg)
    return found


def _refuse_unknown(document: Mapping[str, object], source: Path) -> None:
    """Refuse every field this format does not define, saying why for the known ones."""
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise SubagentError(msg)


def uncallable_helpers(builtin_tools: Selection, subagents: Selection) -> str | None:
    """Why a definition could never call the subagents it names, or `None` if it can.

    The agent format asks it too, so the two refuse in the same words.
    """
    if not subagents or builtin_tools == ALL or TASK_TOOL in (builtin_tools or ()):
        return None
    named = "every subagent" if subagents == ALL else ", ".join(subagents)
    # Told apart: "leaves out 'task'" sends someone who wrote no list hunting for one.
    said = (
        f"builtin_tools is left out, which grants no built-in -- not even {TASK_TOOL!r}"
        if builtin_tools is None
        else f"builtin_tools leaves out {TASK_TOOL!r}"
    )
    return (
        f"subagents names {named}, and {said}, the tool a subagent is called through. "
        f"Naming one does not grant it, so every call would be refused when it was "
        f"made. Add {TASK_TOOL} to builtin_tools, or write builtin_tools: [{ALL!r}] "
        f"for every built-in"
    )


def _refuse_the_sandbox(builtin_tools: Selection, *, source: str) -> None:
    """Refuse a subagent listing the code sandbox's tool, whether or not one is wired.

    With it wired, nothing else would: the name is one the agent offers, and the subagent
    is built without it.
    """
    if builtin_tools in (ALL, None) or INTERPRETER_TOOL not in builtin_tools:
        return
    msg = (
        f"{source}: builtin_tools lists {INTERPRETER_TOOL!r}, the code sandbox, which is "
        f"the agent's alone -- no subagent is handed it at any depth, so the entry "
        f"would grant nothing. Take it off the list, and leave the computing to the "
        f"agent that consults this one"
    )
    raise SubagentError(msg)


def parse(document: Mapping[str, object], source: Path) -> SubagentSpec:
    """One definition, from its decoded fields."""
    reader = fields.Reader(source=source.name, error=SubagentError)
    # Before the required fields, so `nmae:` is reported as a typo, not a missing `name`.
    _refuse_unknown(document, source)

    for required in ("name", "description", "system_prompt"):
        # Absent and blank apart: "missing" sends someone hunting for a line they wrote.
        if required not in document:
            msg = f"{source.name}: missing required field {required!r}"
            raise SubagentError(msg)
        if not fields.text(document[required]):
            msg = f"{source.name}: {required!r} is present but empty"
            raise SubagentError(msg)

    wanted = fields.wanted_model(document, reader)

    # Absent grants none on every field, tools included: a forgotten line fails closed.
    builtin = reader.selection(document.get("builtin_tools"), absent=None, key="builtin_tools")
    _refuse_the_sandbox(builtin, source=source.name)

    # Kept as written, `where::what` and all: two folders may each define a `fetch`, and
    # only the reference says which. `Offering.permitted` flattens it for the allowlist.
    written_tools, _, own_tools = reader.sourced(
        document.get("tools"), absent=None, key="tools", same=bare_name, locked=fields.TOOL_LOCK
    )
    # Read as the agent format reads it: settings must mean the same in either file.
    written_middleware, middleware_settings = reader.selection_with_settings(
        document.get("middlewares"), absent=None, key="middlewares"
    )
    written_skills, skill_audiences, own_skills = reader.sourced(
        document.get("skills"),
        absent=None,
        key="skills",
        refuse_all=(
            "it means whatever the request granted, and a subagent is given an "
            "index only where its skills are named -- so against the ordinary "
            "request, which grants every skill, the star reads as all of them "
            "and arrives as none. Name the procedures this one uses"
        ),
    )
    written_subagents = reader.names_only(
        document.get("subagents"),
        absent=None,
        key="subagents",
        refuse_all=(
            "it would mean every definition in the catalogue, which includes this "
            "one, so it is always a loop. Name the subagents this one consults"
        ),
        locked=fields.SUBAGENT_LOCK,
    )
    # At load: otherwise only the call shows it, as an error the model reads and works
    # around, and nobody who could fix the file sees it.
    if (complaint := uncallable_helpers(builtin, written_subagents)) is not None:
        msg = f"{source.name}: {complaint}"
        raise SubagentError(msg)
    source_ids = reader.source_ids(document.get("source_ids"))

    return SubagentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        builtin_tools=builtin,
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        skills=written_skills,
        middlewares=written_middleware,
        middleware_settings=middleware_settings,
        subagents=written_subagents,
        bundled={"tools": own_tools, "skills": own_skills},
        wanted=wanted,
        metadata=reader.mapping(document.get("metadata"), key="metadata"),
        source_ids=source_ids,
        skill_audiences=skill_audiences,
        datazone=reader.datazone(document.get("datazone")),
    )

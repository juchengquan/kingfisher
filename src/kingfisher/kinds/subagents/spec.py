"""What a subagent is, and the vocabulary a definition says it in.

**Omitting `tools` inherits the parent's; omitting `skills` grants none.** Tools are
what a delegate needs to *act*, so inheriting is the useful default; skills are what it
needs to *know*, and the body below is already its procedure. Handing the index over
also costs ~600 tokens for three skills, of which ~450 is deepagents' preamble and is
paid for the first as much as the third. Re-measured 2026-09-03.

`reading` says how a *file* becomes one of these; everything a definition may write,
and every rule turning what it wrote into a spec, is here. The two were split the
other way round until the history was asked: every commit that changed what a subagent
may declare opened both files, and none has ever opened one.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED, Audience, narrowed_for
from kingfisher.domain.capabilities import ALL, Capabilities, Selection
from kingfisher.kinds.tools.spec import claimed_sources

DIRECTORY = "subagents"


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
    #: a spec with both has said one thing twice with no rule for which wins.
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


#: Every field this format defines. A key outside it is refused rather
#: than ignored, because ignoring one is indistinguishable from honouring it: a
#: definition writing `tolls:` got a delegate holding *every* tool its parent had, since
#: a missing `tools` means inherit.
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
        "bundle",
        "model",
        "metadata",
        "source_ids",
    }
)

#: Fields deepagents' `SubAgent` understands that this format deliberately does
#: not expose, each with the reason. They are named separately because the
#: generic message is actively misleading here -- it reads as "kingfisher has
#: not got round to this", when the answer is that honouring it would be wrong.
REFUSED: Mapping[str, str] = MappingProxyType(
    {
        "permissions": (
            "deepagents' permissions *replace* the parent's rather than narrowing "
            "them, so writing this to tighten a delegate would drop the rules it "
            "already inherits -- including the one making /data read-only"
        ),
        "interrupt_on": (
            "needs a checkpointer and a human to answer the interrupt, neither of "
            "which a delegate has here"
        ),
        # Corrected 2026-09-06, and the correction is the point of the entry. This read
        # "a delegate returns prose to its caller, which is the caller's to shape --
        # there is nothing here to hand a schema to", and deepagents disproves both
        # halves: `_compile_spec` takes a `response_format`, and
        # `middleware/subagents.py` serialises what comes back -- `model_dump_json`, or
        # `json.dumps` for anything else -- into the `ToolMessage` the parent reads.
        # There is somewhere to hand it, and it is handed there.
        "response_format": (
            "deepagents does support one here, and nothing structured survives it "
            "-- a delegate's response is serialised into the tool result, so its "
            "parent reads text either way and there is nothing for kingfisher to "
            "carry"
        ),
    }
)

#: What a module must define: the subagents it contributes, as a sequence.
#: Declared, never inferred -- the same rule `TOOLS` makes, and here it is not
#: even a preference. A compiled subagent is a plain `dict` at runtime, so there
#: is no type to search for, and searching for "a mapping with a `build` key"
#: would find imported names too: `from .base import RESEARCHER`, written to
#: compose one delegate into another, would offer `RESEARCHER` as a delegate
#: nobody meant to expose.
EXPORT = "SUBAGENTS"
#:
#: Beside the format rather than in `catalogue`, which is where `tools` and
#: `middlewares` keep theirs. For them the name is only the catalogue walk's business.
#: Here it is the format's: both Python declarations refuse by naming it -- "SUBAGENTS
#: entry is missing 'name'" -- so a copy in the catalogue would leave the messages
#: spelling it a second time.

#: Every key a *compiled* Python declaration may write -- one carrying `build`.
#: Deliberately not `KNOWN`: the two formats describe the same delegate and do not
#: describe it with the same words, and a shared set would have to be the union,
#: which permits each format the other's keys.
DECLARED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "build",
        "tools",
        #: Written here as well as in a document, because a compiled delegate owns
        #: a folder like any other and its tools reach the graph. This table is for
        #: keys that would do nothing, and this one checks the folder -- which is
        #: all it ever does, for either kind of delegate.
        "bundle",
        "model",
        "metadata",
        "source_ids",
    }
)

#: Keys the YAML format defines that a Python declaration may not, each with the
#: reason. Named individually rather than folded into "unknown key", because
#: every one of them is a thing a reader would reasonably expect to work -- and
#: the answer is not "not yet", it is that deepagents would ignore it.
NOT_COMPILED: Mapping[str, str] = MappingProxyType(
    {
        "builtin_tools": (
            "deepagents' own tools are built inside the parent's assembly and do "
            "not exist as objects when a delegate is put together, so there is "
            "nothing to hand a graph. A compiled subagent brings its own"
        ),
        "system_prompt": (
            "a compiled subagent brings its own graph, and whatever prompt it "
            "uses is inside it. Write the prompt where the graph is built"
        ),
        "skills": (
            "deepagents mounts skills for a delegate it builds; it runs a "
            "compiled graph as given and never adds a skills middleware to it. "
            "Read what the delegate needs inside the graph instead"
        ),
        "middlewares": (
            "middleware is wrapped around a graph deepagents builds. A compiled "
            "one is already built, so naming middleware here would be a line "
            "that does nothing"
        ),
        "subagents": (
            "delegation reaches a delegate through the `task` tool its own "
            "middleware supplies, and a compiled graph is given no middleware. "
            "Build the nesting into the graph if it needs it"
        ),
    }
)


#: What `bundle:` may say: one half per directory a bundle can hold, which is the
#: same two names `catalogue.ASSET_DIRECTORIES` keeps the walk out of.
#: `test_the_bundle_key_covers_every_directory_a_bundle_holds` is what stops the two
#: drifting -- a third asset kind added to the walk and not here would be a folder a
#: definition could never describe, and nothing else would say so.
BUNDLE_KEYS: tuple[str, ...] = ("tools", "skills")


#: Every key a *portable* declaration may write -- one with no `build`, assembled by
#: kingfisher from what it says. Each is something a definition can answer without
#: seeing the deployment it will be installed into, which is the whole of the rule.
PORTABLE: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "system_prompt",
        # deepagents' own, and the same names wherever kingfisher runs. Still
        # narrowed by the request: they are the host's tools rather than this
        # definition's, so a request that withheld `execute` withholds it here.
        "builtin_tools",
        # Carried rather than described, for a definition that has no folder.
        "bundle",
        "metadata",
    }
)

#: Keys this format defines that a portable declaration may not write, each with the
#: reason. Named one at a time for the reason `NOT_COMPILED` is, and every reason here
#: is one sentence of the same rule: the key names something only the deployment
#: knows, so a definition written elsewhere cannot mean anything by it.
NOT_PORTABLE: Mapping[str, str] = MappingProxyType(
    {
        "tools": (
            "a name here is a lookup in the deployment's own catalogue, which a "
            "definition written somewhere else has never seen -- on one machine it "
            "finds nothing and on the next a different tool wearing the name. Carry "
            "the tools themselves under 'bundle'"
        ),
        "skills": (
            "the same lookup in the same catalogue, with the same two ways to be "
            "wrong. Carry them under 'bundle' instead, as the directory they are in"
        ),
        "subagents": (
            "a helper has to be a catalogue entry, and an imported delegate is "
            "atomic -- what it owns reaches it and nothing else reaches that. Ship "
            "the helper as a delegate of its own for an agent to grant beside this "
            "one, or write 'build' and compose the graph yourself"
        ),
        "middlewares": (
            "it selects code the deployment wrote and registered, which is neither "
            "shipped with this definition nor nameable from outside"
        ),
        "model": (
            "it names a model profile only this deployment defines, and naming one "
            "decides where the prompt goes and whose credentials pay. A request pins "
            "any delegate's model by name with 'run_on', which is where the choice "
            "belongs"
        ),
        "source_ids": (
            "it names ids from the deployment's source_ids.yaml. The agent that "
            "grants this delegate carries the audience deciding who reaches it"
        ),
    }
)


def declared(entry: Mapping[str, object], source: str) -> SubagentSpec:
    """One entry of a module's `SUBAGENTS` into the spec kingfisher works with.

    Two shapes, told apart by `build` and never by where the entry came from. A
    re-export -- `from acme_agents import SUBAGENTS` -- hands over dictionaries
    indistinguishable from ones written in the file itself, so nothing here can ask
    whether an entry was imported. The shape it wrote is the only honest subject for
    a rule, which is why the portable vocabulary is a property of the format.
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

    # `build` is not among them: whether it is there is what chose this branch.
    for required in ("name", "description"):
        if required not in entry:
            msg = f"{source}: {EXPORT} entry is missing {required!r}"
            raise SubagentError(msg)

    build = entry["build"]
    if not callable(build):
        msg = (
            f"{source}: 'build' is {type(build).__name__}, which cannot be called. "
            f"It is given a model and the tools this delegate was granted, and "
            f"returns the graph to run"
        )
        raise SubagentError(msg)

    where = Path(source)
    read = fields.Reader(source=where.name, error=SubagentError)
    wanted = fields.wanted_model(entry, read)
    written_tools, tool_audiences = read.audienced(entry.get("tools"), absent=ALL, key="tools")
    # Only `tools` here: `skills` and `subagents` are refused for a compiled
    # delegate by `NOT_COMPILED`, so there is nothing else to carry an audience.
    audiences = {"tools": tool_audiences} if tool_audiences else {}
    source_ids = read.source_ids(entry.get("source_ids"))
    return SubagentSpec(
        name=fields.text(entry["name"]),
        description=fields.text(entry["description"]),
        build=build,
        # Not `ALL`, which is what a document that stays quiet means. A
        # compiled graph is handed the workspace tools it was granted and
        # nothing else, so claiming every built-in would be a ceiling nothing
        # can fill -- and `--run` would report a delegate withholding tools it
        # was never able to have.
        builtin_tools=None,
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        bundle=_bundle(entry.get("bundle"), read),
        wanted=wanted,
        metadata=read.mapping(entry.get("metadata"), key="metadata"),
        source_ids=source_ids,
        audiences=audiences,
    )


def _bundle(value: object, reader: fields.Reader) -> Mapping[str, tuple[str, ...]]:
    """What `bundle:` claims its folder holds, by half, for the halves it wrote."""
    if value is None:
        return {}
    # Not `reader.mapping`, whose refusal says "a mapping of your own keys" -- true
    # of `metadata:` and the opposite of true here, where the keys are the two the
    # format names. Someone reaching for `bundle: [mask_secrets]` needs to be told
    # which half they meant, not that they may write whatever they like.
    if not isinstance(value, Mapping):
        msg = (
            f"{reader.source}: bundle is {type(value).__name__}; it takes "
            f"{' and/or '.join(BUNDLE_KEYS)}, each naming what that folder holds:\n"
            f"    bundle:\n      tools: [mask_secrets]"
        )
        raise SubagentError(msg)
    written = dict(value)
    if unknown := sorted(set(written) - set(BUNDLE_KEYS)):
        msg = (
            f"{reader.source}: bundle names {unknown}, and a bundle holds "
            f"{list(BUNDLE_KEYS)} -- those are the folders under "
            f"subagents/<name>/ that reach the delegate"
        )
        raise SubagentError(msg)
    if not written:
        # An empty mapping describes nothing, so it cannot be wrong, so it checks
        # nothing -- which is the one thing this key must never be.
        msg = (
            f"{reader.source}: bundle is empty; it takes "
            f"{' and/or '.join(BUNDLE_KEYS)}, naming what the folder holds. "
            f"Leave the key out to say nothing"
        )
        raise SubagentError(msg)
    claimed: dict[str, tuple[str, ...]] = {}
    for half in BUNDLE_KEYS:
        if half not in written:
            continue  # said nothing about this half, which is not the same as none
        # `*` would mean "whatever the folder holds", a claim that cannot be wrong,
        # in the one key whose whole job is to be wrong when the folder changes.
        names = reader.selection(
            written[half],
            absent=(),
            key=f"bundle {half}",
            refuse_all=(
                f"it would say only that this delegate gets the {half} in its own "
                f"folder, which is true of every bundle. Name them, or leave the "
                f"line out"
            ),
        )
        claimed[half] = tuple(names or ())
    return claimed


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

    for required in ("name", "description", "system_prompt"):
        if required not in entry:
            msg = f"{source}: {EXPORT} entry is missing {required!r}"
            raise SubagentError(msg)
        if not fields.text(entry[required]):
            msg = f"{source}: {EXPORT} entry has {required!r} present but empty"
            raise SubagentError(msg)

    read = fields.Reader(source=source, error=SubagentError)
    return SubagentSpec(
        name=fields.text(entry["name"]),
        description=fields.text(entry["description"]),
        system_prompt=fields.text(entry["system_prompt"]),
        builtin_tools=read.selection(
            entry.get("builtin_tools"), absent=ALL, key="builtin_tools"
        ),
        # Explicit, and the one line here that would be a hole if it were left out.
        # The field defaults to `ALL`, which means *inherit whatever the request
        # granted* -- so a definition saying nothing about workspace tools, which
        # every portable one does since it has no `tools:` key to say it with, would
        # be handed the deployment's entire catalogue. An imported delegate holds
        # what it carried and nothing else.
        tools=None,
        carried=_carried(entry.get("bundle"), read),
        metadata=read.mapping(entry.get("metadata"), key="metadata"),
    )


def _carried(value: object, reader: fields.Reader) -> Mapping[str, Any]:
    """What `bundle:` brought, for a definition with no folder to describe.

    The other reading of the same key. A document names what its folder holds so the
    two can be checked against each other; a declaration with no folder hands over the
    things themselves, and there is nothing left to check -- which is why a spec
    carrying these makes no claim, and `miscounted` has nothing to say about it.
    """
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        msg = (
            f"{reader.source}: bundle is {type(value).__name__}; it takes "
            f"{' and/or '.join(BUNDLE_KEYS)} -- the tools themselves, and the "
            f"directory holding the skills"
        )
        raise SubagentError(msg)
    written = dict(value)
    if unknown := sorted(set(written) - set(BUNDLE_KEYS)):
        msg = (
            f"{reader.source}: bundle names {unknown}, and a bundle holds "
            f"{list(BUNDLE_KEYS)}"
        )
        raise SubagentError(msg)
    if not written:
        msg = (
            f"{reader.source}: bundle is empty; it takes "
            f"{' and/or '.join(BUNDLE_KEYS)}. Leave the key out to carry nothing"
        )
        raise SubagentError(msg)

    carried: dict[str, Any] = {}
    if "tools" in written:
        tools = written["tools"]
        # A list or a tuple, and nothing looser -- the rule `TOOLS` and `SUBAGENTS`
        # both make, for the reason they both give: a single tool is a pydantic
        # model, and a pydantic model is iterable, so `tools: my_tool` would pass a
        # duck test and then loop over the tool's own fields.
        if not isinstance(tools, (list, tuple)):
            msg = (
                f"{reader.source}: bundle tools is {type(tools).__name__}; it takes a "
                f"list of the tools themselves -- write tools: [my_tool]"
            )
            raise SubagentError(msg)
        carried["tools"] = tuple(tools)
    if "skills" in written:
        carried["skills"] = _skills_directory(written["skills"], reader)
    return carried


def _skills_directory(value: object, reader: fields.Reader) -> Path:
    """The directory a definition resolved for its own skills, checked here.

    Absolute, because a relative one resolves against the working directory: the
    package would find its skills when kingfisher happened to be started from the
    right place and silently offer none otherwise. Whoever ships the definition knows
    where its files are -- `Path(__file__).parent` -- and kingfisher never guesses.
    """
    if not isinstance(value, (str, Path)):
        msg = (
            f"{reader.source}: bundle skills is {type(value).__name__}; it takes the "
            f"directory the skills are in, as a path"
        )
        raise SubagentError(msg)
    found = Path(value)
    if not found.is_absolute():
        msg = (
            f"{reader.source}: bundle skills is {str(found)!r}, which is relative -- "
            f"it would be resolved against whatever directory kingfisher was started "
            f"in. Write an absolute path, which for a definition beside its own "
            f"skills is Path(__file__).parent / 'skills'"
        )
        raise SubagentError(msg)
    if not found.is_dir():
        msg = (
            f"{reader.source}: bundle skills is {str(found)!r}, which is not a "
            f"directory. A carried bundle is checked here because there is nowhere "
            f"else it could be: nothing walks a catalogue to find it"
        )
        raise SubagentError(msg)
    return found


def _refuse_unknown(document: Mapping[str, object], source: Path) -> None:
    """Refuse every field this format does not define, saying why for the ones we know
    about.
    """
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise SubagentError(msg)

def parse(document: Mapping[str, object], source: Path) -> SubagentSpec:
    """One definition, from its decoded fields."""
    reader = fields.Reader(source=source.name, error=SubagentError)
    # Before the required-field check, so `nmae:` is reported as the typo it is
    # rather than as a missing `name` the author plainly tried to write.
    _refuse_unknown(document, source)

    for required in ("name", "description", "system_prompt"):
        # Absent and blank are different mistakes and read differently in a
        # traceback: "missing" sends someone looking for a line they can see
        # they wrote, which is the wrong hunt.
        if required not in document:
            msg = f"{source.name}: missing required field {required!r}"
            raise SubagentError(msg)
        if not fields.text(document[required]):
            msg = f"{source.name}: {required!r} is present but empty"
            raise SubagentError(msg)

    wanted = fields.wanted_model(
        document, fields.Reader(source=source.name, error=SubagentError)
    )

    # Read once, then split. A `tools:` entry may be written `where::what`, and
    # only `what` may reach the rest of kingfisher -- a grant, an allowlist and
    # the dictionary the agent dispatches through all key on the plain name.
    # Where it claims to live travels beside it, for whoever checks the claim.
    written_tools, tool_audiences = reader.audienced(
        document.get("tools"), absent=ALL, key="tools"
    )
    # Same two-in-one read as the agent format, and the same field: a
    # definition writing settings has to mean the same thing in either file.
    written_middleware, middleware_settings = reader.selection_with_settings(
        document.get("middlewares"), absent=None, key="middlewares"
    )
    written_skills, skill_audiences = reader.audienced(
        document.get("skills"),
        absent=None,
        key="skills",
        refuse_all=(
            "it means whatever the request granted, and a delegate is given an "
            "index only where its skills are named -- so against the ordinary "
            "request, which grants every skill, the star reads as all of them "
            "and arrives as none. Name the procedures this one uses"
        ),
    )
    written_delegates, delegate_audiences = reader.audienced(
        document.get("subagents"),
        absent=None,
        key="subagents",
        refuse_all=(
            "it would mean every definition in the catalogue, which includes this "
            "one, so it is always a loop. Name the delegates this one consults"
        ),
    )
    audiences = {
        name: entries
        for name, entries in zip(
            AUDIENCED, (tool_audiences, delegate_audiences, skill_audiences), strict=True
        )
        if entries
    }
    source_ids = reader.source_ids(document.get("source_ids"))

    return SubagentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        # `[read_file, grep]` and a block list are the same thing to YAML, so
        # both reach here already parsed.
        # Absent means inherit for tools and none for skills -- the
        # asymmetry the format has always had, now said in the values
        # rather than in a reader that special-cases one of them.
        builtin_tools=reader.selection(
            document.get("builtin_tools"), absent=ALL, key="builtin_tools"
        ),
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        skills=written_skills,
        middlewares=written_middleware,
        middleware_settings=middleware_settings,
        subagents=written_delegates,
        bundle=_bundle(document.get("bundle"), reader),
        wanted=wanted,
        metadata=reader.mapping(document.get("metadata"), key="metadata"),
        source_ids=source_ids,
        audiences=audiences,
    )


# `tools:` used to be stripped to bare names here, on the reasoning that a name is the
# only thing a grant, an allowlist or the agent's dispatch dictionary keys on. That held
# while a name could only mean one tool. Two folders may now each define a `fetch`, and
# the reference is the only thing that says which -- so a definition keeps what it
# wrote, and the flattening happens at the two places that genuinely need a bare name:
# `ToolAllowlist`, and `permitted`.

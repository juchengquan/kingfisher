"""Subagent definitions: `/subagents/<name>.yaml`.

A YAML document. `name`, `description` and `system_prompt` are required. The rest are
optional, and `builtin_tools`, `tools`, `skills`, `subagents`, `middleware` and
`model` all select by name from what the deployment already offers — how each
selection is enforced is the adapter's problem, not this format's. `groups` and
`metadata` are optional too and select nothing. `KNOWN` below is the whole set, and
is the one place that stays right when it grows.

**Omitting `tools` inherits the parent's; omitting `skills` grants none.** The
asymmetry is deliberate. Tools are what a delegate needs to *act* and it can do
nothing without them, so inheriting is the useful default. Skills are what it needs
to *know*, and the body below is already its procedure — a delegate that needed the
whole index would not have been worth defining. Handing it over also costs: the
listing is injected into the delegate's prompt at ~600 tokens for three skills, of
which ~450 is deepagents' preamble and is paid for the first skill as much as the
third. Re-measured 2026-09-03.

`subagents` names delegates this one may consult mid-job, from the same catalogue.
Absent means none, like `skills`. It was refused until it was measured: the refusal
said "deepagents gives it no `task` tool, so nesting is not something this format can
express", and the first half is true -- `create_sub_agent` calls `create_agent` with
the spec's tools and no `task`. The second half was not. A spec carries `middleware`,
and `SubAgentMiddleware` is exactly what supplies `task`, so the format could always
express it through a field it already had.

**One name, and no list.** There was an `alias:` beside `model:` -- a general name
the deployment bound under `aliases:` in `models.yaml` -- so that a definition could
know what *kind* of model it needed without knowing its name, which is what a file
shipped inside a wheel is in. It is gone: two spellings for one idea, and the shipped
definitions name nothing at all now and say in a comment what to pin them to.

There was a `provider:` beside `model:`, naming an endpoint by style, and a rule that
the two moved together -- a model name sent to an endpoint that has never heard of it
is a 404 if you are lucky and a wrong-model run if you are not. Both are gone. A
model resolves to its own endpoint through the catalogue, so the half-pair is not a
thing that can be written, and the rule refusing it has nothing left to refuse.

**A field this format does not define is refused, not ignored.** Ignoring one is
indistinguishable from honouring it, and the difference matters most where it is
least visible: `tolls:` produced a delegate holding *every* tool its parent had,
because a missing `tools` means inherit. `permissions:` was worse -- it is written to
*restrict* a delegate, did nothing at all, and so a definition read tighter than the
agent it produced. Fields deepagents knows and this format deliberately declines are
named individually with the reason, since a generic "unknown field" reads as an
omission worth working around.

**Nothing in a run reads it, and that is deliberate.** It is for whatever loads the
catalogue -- a deployment script deciding which definitions to install, an ownership
report, a linter -- all of which read a `SubagentRepository` and read `spec.metadata`
without kingfisher's help. Wiring it into the run would mean choosing a consumer, and
the obvious candidate (handing it to a middleware factory) changes a published
constructor argument for a use nobody has yet.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED
from kingfisher.domain.capabilities import ALL
from kingfisher.infrastructure import documents
from kingfisher.subagents.spec import SubagentError, SubagentSpec
from kingfisher.tools.spec import claimed_sources

DIRECTORY = "subagents"
SUFFIX = ".yaml"

#: The spelling people reach for, and the one that used to vanish. `.yml` is
#: valid YAML everywhere else, so a file named that way is a definition someone
#: wrote and kingfisher silently did not read.
#:
#: Named rather than "any extension we do not recognise", which was the first
#: draft. A folder here may now be a Python package, and a package is entitled
#: to hold whatever it needs beside its `__init__.py` -- a JSON fixture, a CSV,
#: a prompt in a text file. Refusing every unfamiliar suffix would break that
#: for the sake of one confusion, so the one confusion is named.
NEAR_MISS = ".yml"
#:
#: Here rather than in `catalogue`, which is where it was: it is a fact
#: about what the format's files are called, like `SUFFIX` directly above,
#: and the agent repository needs both. Reaching a catalogue for the second
#: one closed an import loop through this package.
#: Every field this format defines. A key outside it is refused rather than
#: ignored, because ignoring one is indistinguishable from honouring it: a
#: definition writing `tolls:` got a delegate holding *every* tool its parent
#: had, since a missing `tools` means inherit.
#:
#: This is a rule for subagents and not for skills. Kingfisher owns this format;
#: deepagents owns the skill format and decides what a skill may say, so
#: refusing keys there would reject fields valid in a format we do not define.
KNOWN: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "system_prompt",
        "builtin_tools",
        "tools",
        "skills",
        "middleware",
        "subagents",
        "model",
        "metadata",
        "groups",
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
        # Corrected 2026-09-06, and the correction is the point of the entry.
        # This read "a delegate returns prose to its caller, which is the
        # caller's to shape -- there is nothing here to hand a schema to", and
        # deepagents disproves both halves: `_compile_spec` takes a
        # `response_format`, and `middleware/subagents.py` serialises what comes
        # back -- `model_dump_json`, or `json.dumps` for anything else -- into
        # the `ToolMessage` the parent reads. There is somewhere to hand it, and
        # it is handed there.
        #
        # What survives the check is the refusal, not the reason. A schema
        # shapes what the delegate produces and then that shape is flattened to
        # text at the boundary, so the parent is reading prose-or-JSON either
        # way and kingfisher is handed nothing it could carry as structure.
        #
        # Checked against deepagents 0.7.6. A reason upstream contradicts is
        # worse than the generic message this table exists to replace: whoever
        # checks one and finds it false has no reason to trust the other two.
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

#: Every key a Python declaration may write. Deliberately not `KNOWN`: the two
#: formats describe the same delegate and do not describe it with the same
#: words, and a shared set would have to be the union, which permits each format
#: the other's keys.
DECLARED: frozenset[str] = frozenset(
    {
        "name",
        "description",
        "build",
        "tools",
        "model",
        "metadata",
        "groups",
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
        "middleware": (
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


def declared(entry: Mapping[str, object], source: str) -> SubagentSpec:
    """One entry of a module's `SUBAGENTS` into the spec kingfisher works with."""
    if not isinstance(entry, Mapping):
        msg = (
            f"{source}: every entry of {EXPORT} must be a mapping with a "
            f"'name', a 'description' and a 'build'; got {type(entry).__name__}"
        )
        raise SubagentError(msg)

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

    for required in ("name", "description", "build"):
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
    groups = read.groups(entry.get("groups"))
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
        wanted=wanted,
        metadata=read.mapping(entry.get("metadata"), key="metadata"),
        groups=groups,
        audiences=audiences,
    )


def _refuse_unknown(document: Mapping[str, object], source: Path) -> None:
    """Refuse every field this format does not define, saying why for the ones we know
    about.
    """
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise SubagentError(msg)


def read(text: str, source: Path) -> SubagentSpec:
    """One definition, from its document. Raises `SubagentError` on anything malformed."""
    document = documents.decode(text)
    if isinstance(document, str):
        msg = f"{source.name}: cannot read definition ({document})"
        raise SubagentError(msg)
    documents.require_literal_prompt(text, source, SubagentError)

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
        document.get("middleware"), absent=None, key="middleware"
    )
    written_skills, skill_audiences = reader.audienced(
        document.get("skills"), absent=None, key="skills"
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
    groups = reader.groups(document.get("groups"))

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
        middleware=written_middleware,
        middleware_settings=middleware_settings,
        subagents=written_delegates,
        wanted=wanted,
        metadata=reader.mapping(document.get("metadata"), key="metadata"),
        groups=groups,
        audiences=audiences,
    )



# `tools:` used to be stripped to bare names here, on the reasoning that a name
# is the only thing a grant, an allowlist or the agent's dispatch dictionary
# keys on. That held while a name could only mean one tool. Two folders may now
# each define a `fetch`, and the reference is the only thing that says which --
# so a definition keeps what it wrote, and the flattening happens at the two
# places that genuinely need a bare name: `ToolAllowlist`, and `permitted`.
#
# `claimed_sources` still reads the same entries to check the claim is true.
# One reference, doing two jobs now rather than one.




"""Subagent definitions: `/subagents/<name>.yaml`.

A YAML document. `name`, `description` and `system_prompt` are required;
`tools`, `skills`, `middleware` and `model` are optional and all
select by name from what the deployment already offers — how each selection is
enforced is the adapter's problem, not this format's.

    name: reviewer
    description: Checks an analysis for arithmetic errors and unsupported claims.
    tools: [read_file, glob, grep]
    skills: [tabular-qa]
    middleware: [audit]
    model: gpt-5
    system_prompt: |
      You review analyses...

It was markdown with a YAML header until the header had grown into everything
but the prompt. A skill still has that shape, because deepagents owns that
format and kingfisher owns this one — which is also why they disagree about
what an unrecognised field means.

**Omitting `tools` inherits the parent's; omitting `skills` grants none.** The
asymmetry is deliberate. Tools are what a delegate needs to *act* and it can do
nothing without them, so inheriting is the useful default. Skills are what it
needs to *know*, and the body below is already its procedure — a delegate that
needed the whole index would not have been worth defining. Handing it over also
costs: the listing is injected into the delegate's prompt, measured at ~464
tokens for three skills and growing with the catalogue.

`middleware` names entries from a registry the *deployment* supplies, so it is
the one field that selects code rather than content. It is empty until someone
wires one, and a name must be both registered and granted — including for a
definition a caller uploaded, which gets none of the leeway an uploaded skill
does. An uploaded skill is the caller's own text; a middleware name is not.

`subagents` names delegates this one may consult mid-job, from the same
catalogue. Absent means none, like `skills`. It was refused until it was
measured: the refusal said "deepagents gives it no `task` tool, so nesting is
not something this format can express", and the first half is true --
`create_sub_agent` calls `create_agent` with the spec's tools and no `task`.
The second half was not. A spec carries `middleware`, and `SubAgentMiddleware`
is exactly what supplies `task`, so the format could always express it through
a field it already had.

Any depth. A helper may name helpers of its own, so a cycle *can* form, and
`refuse_cycles` is the only thing stopping one -- checked over the whole
catalogue when it loads rather than per request, because a set of definitions is
either coherent or it is not. This was bounded at one level until delegation
learned to nest, and the bound was structural: the call that would build the
second level was simply never made.

`model` names what this delegate runs, out of the models `models.yaml`
defines. Omitted, it runs whatever the deployment runs. It is granted like
`middleware` and for a stronger reason: the model decides which endpoint
receives this delegate's prompts and whose credentials pay for them.

**One name, and no list.** There was an `alias:` beside `model:` -- a general
name the deployment bound under `aliases:` in `models.yaml` -- so that a
definition could know what *kind* of model it needed without knowing its name,
which is what a file shipped inside a wheel is in. It is gone: two spellings for
one idea, and the shipped definitions name nothing at all now and say in a
comment what to pin them to.

The list went with it, and that is worth understanding rather than noticing. A
list meant "try these in order", and the only thing that ever passed a candidate
over was an alias this deployment had not bound. Nothing passes over a *model*:
one this deployment cannot run refuses on the spot, and always did. So every
entry after the first was already unreachable, and keeping the shape would have
been keeping a promise nothing could honour.

There was a `distinct: true` too, saying that running beside the main agent
defeated the delegate, and turning `indistinct`'s report into a refusal. It went
with `second-opinion`, its only user. `indistinct` still reports -- it fires for
any definition that named a model and did not end up anywhere different -- so
the disappointment is still named, just not refused.

There was a `provider:` beside `model:`, naming an endpoint by style, and a rule
that the two moved together -- a model name sent to an endpoint that has never
heard of it is a 404 if you are lucky and a wrong-model run if you are not. Both
are gone. A model resolves to its own endpoint through the catalogue, so the
half-pair is not a thing that can be written, and the rule refusing it has
nothing left to refuse.

The definition is the only place either is said. There was an environment
override too, and it is gone: `KINGFISHER_MODEL_SUBAGENT` could only say "every
delegate", which is a sentence about cost that nobody wants to be true of a
delegate chosen for being a *different* model. Per-delegate is what this file
already is, so this is where cost routing lands -- reading-heavy delegation on
a cheap model, the second opinion somewhere else entirely.

**A field this format does not define is refused, not ignored.** Ignoring one
is indistinguishable from honouring it, and the difference matters most where
it is least visible: `tolls:` produced a delegate holding *every* tool its
parent had, because a missing `tools` means inherit. `permissions:` was worse
-- it is written to *restrict* a delegate, did nothing at all, and so a
definition read tighter than the agent it produced. Fields deepagents knows and
this format deliberately declines are named individually with the reason, since
a generic "unknown field" reads as an omission worth working around.

`metadata` is the one field this format has no opinion about: a mapping of the
caller's own keys, carried and never interpreted.

**Nothing in a run reads it, and that is deliberate.** It is for whatever loads
the catalogue -- a deployment script deciding which definitions to install, an
ownership report, a linter -- all of which read a `SubagentRepository` and
read `spec.metadata` without kingfisher's help. Wiring it into the run would
mean choosing a consumer, and the obvious candidate (handing it to a middleware
factory) changes a published constructor argument for a use nobody has yet.

So the field exists and the seam does not. That way round is recoverable: a
consumer can be added later without changing what a definition may say.

Parsing lives in the domain because this is kingfisher's format, not a library's
— nothing here knows deepagents exists, and nothing here reads a disk. Finding
the files is `infrastructure.catalogue.subagents`; translating a spec into
`SubAgent` is `infrastructure.harness.agent`.

`declared` is the same format arriving another way -- a Python module stating
`SUBAGENTS` rather than a document on disk -- and the two share every field
reader below them, which is why they are one module and not two.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.access import AUDIENCED
from kingfisher.domain.capabilities import ALL
from kingfisher.domain.subagent import SubagentError, SubagentSpec
from kingfisher.domain.tool import claimed_sources

DIRECTORY = "subagents"
SUFFIX = ".yaml"


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
        "response_format": (
            "a delegate returns prose to its caller, which is the caller's to "
            "shape -- there is nothing here to hand a schema to"
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
    """One entry of a module's `SUBAGENTS` into the spec kingfisher works with.

    The Python sibling of `parse`, and it reads the same fields by the same
    rules: `tools` narrows the same way, `model` resolves the same way, `metadata`
    refuses the same way. What differs is one key -- `build` where a document
    writes `system_prompt` -- and four the other format has that this one
    cannot honour.

    Those four are refused rather than ignored for the reason the whole
    `REFUSED` table exists: a definition writing a line that does nothing reads
    tighter than the delegate it produces, and nothing in the output says so.

    `source` is a string rather than a `Path` because a declaration is one entry
    of a list in a file, not a file -- `researcher.py` names it as precisely as
    anything can, and the name inside it does the rest.
    """
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
    wanted = wanted_model(entry, read)
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
    """Refuse every field this format does not define, saying why for the ones
    we know about.

    A key we ignore is a key the author believes took effect. That is merely
    annoying for `tolls:`, and worse than annoying for `permissions:`, which
    someone writes *to restrict a delegate* and which currently does nothing at
    all -- the definition reads tighter than the agent it produces.

    The wording is `fields.unrecognised` now, shared with `models.yaml`, which
    had a plainer version of the same rule. What stays here is the raising: this
    format's mistakes are `SubagentError`, and the source is named the way this
    format names one.
    """
    complaint = fields.unrecognised(document, known=KNOWN, declined=REFUSED)
    if complaint is not None:
        msg = f"{source.name}: {complaint}"
        raise SubagentError(msg)


def parse(document: Mapping[str, object], source: Path) -> SubagentSpec:
    """One definition, from its decoded fields.

    Raises `SubagentError` on anything the format forbids. Whether the document
    decoded at all was settled before this — reading YAML needs a library, so
    `infrastructure.catalogue.documents` does that half.
    """
    read = fields.Reader(source=source.name, error=SubagentError)
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

    wanted = wanted_model(document, fields.Reader(source=source.name, error=SubagentError))

    # Read once, then split. A `tools:` entry may be written `where::what`, and
    # only `what` may reach the rest of kingfisher -- a grant, an allowlist and
    # the dictionary the agent dispatches through all key on the plain name.
    # Where it claims to live travels beside it, for whoever checks the claim.
    written_tools, tool_audiences = read.audienced(
        document.get("tools"), absent=ALL, key="tools"
    )
    # Same two-in-one read as the agent format, and the same field: a
    # definition writing settings has to mean the same thing in either file.
    written_middleware, middleware_settings = read.selection_with_settings(
        document.get("middleware"), absent=None, key="middleware"
    )
    written_skills, skill_audiences = read.audienced(
        document.get("skills"), absent=None, key="skills"
    )
    written_delegates, delegate_audiences = read.audienced(
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
    groups = read.groups(document.get("groups"))

    return SubagentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        # `[read_file, grep]` and a block list are the same thing to YAML, so
        # both reach here already parsed.
        # Absent means inherit for tools and none for skills -- the
        # asymmetry the format has always had, now said in the values
        # rather than in a reader that special-cases one of them.
        builtin_tools=read.selection(
            document.get("builtin_tools"), absent=ALL, key="builtin_tools"
        ),
        tools=written_tools,
        tool_sources=claimed_sources(written_tools),
        skills=written_skills,
        middleware=written_middleware,
        middleware_settings=middleware_settings,
        subagents=written_delegates,
        wanted=wanted,
        metadata=read.mapping(document.get("metadata"), key="metadata"),
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
# `_claimed_sources` still reads the same entries to check the claim is true.
# One reference, doing two jobs now rather than one.


def wanted_model(document: Mapping[str, object], read: fields.Reader) -> str | None:
    """The model a definition names, or `None` for whatever summoned it.

    One name. `model:` took a list while `alias:` existed, because an alias this
    deployment had not bound was passed over and the next candidate tried -- so
    a list was a definition naming the deployments it could still be useful in.
    Nothing passes over a *model*: one this deployment cannot run refuses on the
    spot, and always did. With `alias` gone every entry after the first was
    unreachable, so a list here would be a shape that cannot mean anything.

    Read through `Reader.one_name`, which refuses that list where the file can
    still be named. It said `fields.text` for a while, and `fields.text` is the
    `str()` that produced the shape rather than the check that stops it: a
    definition writing `model: [gpt-5, claude-4]` was read as a model called
    `"['gpt-5', 'claude-4']"`.

    Takes the `Reader` both formats already build, rather than a bare error
    type, because that is the pair -- the file's name and the format's
    exception -- and it is bound once at each call site.
    """
    return read.one_name(document.get("model"), key="model")


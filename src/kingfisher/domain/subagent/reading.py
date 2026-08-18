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

`alias` says the same thing generally: a name the *deployment* binds to a model
of its own, under `aliases:` in `models.yaml`. It exists because a definition
can know what *kind* of model it needs and not know its name -- which is true of
every definition shipped inside the wheel, since a vendor's model id is portable
nowhere. `extractor` wants a cheap model; `second-opinion` wants one unlike the
main agent's. Neither can spell that as `MiniMax-M2.5` without refusing to start
for everyone else.

An unbound alias stops the build. Falling back to the deployment's own model
would hand `second-opinion` the very model it exists not to be, and that failure
is silent -- the delegate builds, answers, and the answer is worth nothing.

Name one or the other, never both: an alias *is* a model name once bound, so a
file saying both has said one thing twice with no rule for which wins.

**Either may name several, tried in order.** A candidate is passed over for two
reasons and no others -- an alias this deployment never bound, and, when
`distinct` is set, a model that turns out to be the one this delegate exists not
to be. Both are the deployment's doing, so a list is not the definition hedging;
it is the definition naming the deployments it can still be useful in. If every
candidate is passed over there is nothing left to run, and that refuses, naming
each one and why.

`distinct: true` says that running beside the main agent defeats this delegate.
`indistinct` has always been able to see the two crude cases -- the same model as
the default, or a different id on the same host -- and has only ever reported
them, because nothing in a file could say whether being elsewhere was the point:
`reviewer` deliberately runs on the same model and is right to. This is how a
definition says it, and it is what turns that report into a refusal.

That refusal is less new than it looks. `second-opinion` already depends on one
for the other half of the same problem: an *unbound* alias stops the build, for
exactly the reason two paragraphs up. A bound alias that resolves to the default
is the identical sentence with the identical ending, and until now it fell
through. `distinct: true` with nothing named is refused at the file, since a
delegate running the deployment's own model is precisely what it rules out.

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
from kingfisher.domain.capabilities import ALL, Selection
from kingfisher.domain.subagent import SubagentError, SubagentSpec, Wanted
from kingfisher.domain.tool import split_reference

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
        "alias",
        "distinct",
        "metadata",
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
        "alias",
        "distinct",
        "metadata",
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
    rules: `tools` narrows the same way, `alias` binds the same way, `distinct`
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

    if entry.get("model") and entry.get("alias"):
        msg = (
            f"{source}: names both a model and an alias; an alias *is* a model "
            f"name once this deployment binds it, so name one or the other"
        )
        raise SubagentError(msg)

    where = Path(source)
    wanted = _wanted(entry)
    distinct = _flag(entry.get("distinct"), key="distinct", source=where)
    if distinct and not wanted:
        msg = (
            f"{source}: 'distinct: True' with no model or alias -- with nothing named, "
            f"this delegate runs the deployment's own model, which is exactly what "
            f"'distinct' refuses; name what it may run instead"
        )
        raise SubagentError(msg)

    written_tools = _selected(entry.get("tools"), absent=ALL, key="tools", source=where)
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
        tool_sources=_claimed_sources(written_tools),
        wanted=wanted,
        distinct=distinct,
        metadata=_metadata(entry, where),
    )


def _metadata(document: Mapping[str, object], source: Path) -> Mapping[str, object]:
    """The caller's own keys, if it brought any.

    A mapping or nothing. `metadata: gold` is refused rather than wrapped,
    because a bag with no shape cannot be looked up by key and looking up a key
    is the only thing anyone will do with it.

    Absent and empty both become `{}`, which saves every reader a `None` check
    for a field whose whole meaning is "nothing extra".
    """
    raw = document.get("metadata")
    if raw is None:
        return {}
    if not isinstance(raw, Mapping):
        msg = (
            f"{source.name}: metadata must be a mapping of your own keys, "
            f"got {type(raw).__name__}"
        )
        raise SubagentError(msg)
    return dict(raw)


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

    # Two ways to say what a delegate runs, and a file saying both has said one
    # thing twice with no rule for which wins. Refused rather than ranked: a
    # precedence order here would be invisible in the file that relies on it,
    # and whichever way round it went, half the readers would guess the other.
    if document.get("model") and document.get("alias"):
        msg = (
            f"{source.name}: names both a model ({fields.text(document['model'])!r}) and "
            f"an alias ({fields.text(document['alias'])!r}); an alias *is* a model name "
            f"once this deployment binds it, so name one or the other"
        )
        raise SubagentError(msg)

    wanted = _wanted(document)
    distinct = _flag(document.get("distinct"), key="distinct", source=source)
    # A definition that must differ and named nothing to differ *with* runs the
    # deployment's own model, which is the one thing `distinct` exists to
    # refuse -- so it could never start, and would say so per activation rather
    # than once, at the file. Refused here, where a reader can see both halves
    # of the contradiction on the same screen.
    if distinct and not wanted:
        msg = (
            f"{source.name}: 'distinct: true' with no model or alias -- with nothing "
            f"named, this delegate runs the deployment's own model, which is exactly "
            f"what 'distinct' refuses; name what it may run instead"
        )
        raise SubagentError(msg)

    # Read once, then split. A `tools:` entry may be written `where::what`, and
    # only `what` may reach the rest of kingfisher -- a grant, an allowlist and
    # the dictionary the agent dispatches through all key on the plain name.
    # Where it claims to live travels beside it, for whoever checks the claim.
    written_tools = _selected(
        document.get("tools"), absent=ALL, key="tools", source=source
    )

    return SubagentSpec(
        name=fields.text(document["name"]),
        description=fields.text(document["description"]),
        system_prompt=fields.text(document["system_prompt"]),
        # `[read_file, grep]` and a block list are the same thing to YAML, so
        # both reach here already parsed.
        # Absent means inherit for tools and none for skills -- the
        # asymmetry the format has always had, now said in the values
        # rather than in a reader that special-cases one of them.
        builtin_tools=_selected(
            document.get("builtin_tools"), absent=ALL, key="builtin_tools", source=source
        ),
        tools=written_tools,
        tool_sources=_claimed_sources(written_tools),
        skills=_selected(document.get("skills"), absent=None, key="skills", source=source),
        middleware=_selected(
            document.get("middleware"), absent=None, key="middleware", source=source
        ),
        subagents=_selected(
            document.get("subagents"),
            absent=None,
            key="subagents",
            source=source,
            refuse_all=(
                "it would mean every definition in the catalogue, which includes this "
                "one, so it is always a loop. Name the delegates this one consults"
            ),
        ),
        wanted=wanted,
        distinct=distinct,
        metadata=_metadata(document, source),
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


def _claimed_sources(written: Selection) -> Mapping[str, str]:
    """Where each entry said its tool lives, for the entries that said.

    Keyed by name rather than kept as a list, because that is how it is asked:
    the checker holds the real sources by name and wants to know what this
    definition claimed for that one. Entries written the short way are absent,
    which is how "made no claim" is told from "claimed and was right".
    """
    if written in (ALL, None):
        return MappingProxyType({})
    claimed = {}
    for entry in written:
        where, name = split_reference(entry)
        if where is not None:
            claimed[name] = where
    return MappingProxyType(claimed)


def _wanted(document: Mapping[str, object]) -> tuple[Wanted, ...]:
    """What a definition would run, in the order it would prefer.

    `model:` and `alias:` are mutually exclusive and checked as such above, so
    at most one of these loops runs. Each accepts a scalar or a list through
    `fields.names`, which is the same reader every other name-list field uses --
    a single unbracketed name stays legal because that is what every definition
    written so far says.

    Order is the file's, and is kept. A candidate is only ever passed over for a
    reason the deployment caused, so second place means "if you did not bind the
    first" rather than "if the first were unavailable for any reason at all".

    Duplicates are dropped rather than refused. A repeat can only be reached by
    the rule that already passed over the first one, so it changes nothing, and
    refusing it would fail a file over a line that was merely redundant.
    """
    written = fields.names(document.get("model"))
    if written:
        return tuple(dict.fromkeys(Wanted(model=name) for name in written))
    written = fields.names(document.get("alias"))
    if written:
        return tuple(dict.fromkeys(Wanted(alias=name) for name in written))
    return ()


def _flag(value: object, *, key: str, source: Path) -> bool:
    """A yes/no field, refusing the spellings YAML would quietly accept.

    `distinct: "false"` is a non-empty string and truthy in Python, which is the
    reading that says the opposite of what the file says. YAML already turns
    `true`, `yes` and `on` into `True` before this sees them, so what is left
    here is a value that arrived as something other than a bool -- and there is
    no reading of it that is not a guess.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    msg = (
        f"{source.name}: {key} is {value!r}; write true or false. A quoted "
        f"{str(value)!r} reads as text, and every non-empty text is true -- "
        f"including {'false'!r}"
    )
    raise SubagentError(msg)


def _selected(
    value: object,
    *,
    absent: Selection,
    key: str,
    source: Path,
    refuse_all: str | None = None,
) -> Selection:
    """One name-list field, or what its absence means for that field.

    `absent` differs per field and that is the point: omitting `tools` inherits
    the caller's, omitting `skills` grants none. The format has always drawn
    that distinction; it used to live in a reader that treated `None` two ways.

    `["*"]` is everything. A list, because every one of these fields is a list
    and a field whose type changes with its value is one more thing to know.
    The bare `"*"` is refused by name rather than read as a name -- the same
    trade `system_prompt` makes by accepting one block style and naming the
    others -- because a request spells this `"*"` and someone will carry the
    habit across.

    Mixing is refused too. `["*", read_file]` has no reading that is not a
    guess, and it used to have the worst one: `*` matched no tool, so the star
    silently contributed nothing.

    `refuse_all` is for the one field where everything is not a coherent answer,
    and it carries the reason rather than a flag so the message can say it. Four
    of these five fields read `["*"]` naturally -- every built-in tool, every
    workspace tool, every skill, every approved middleware. The fifth is
    `subagents`, where everything includes the definition doing the asking.
    """
    if isinstance(value, str) and value.strip() == ALL:
        msg = (
            f"{source.name}: {key} is written {value.strip()!r}; write [{ALL!r}] instead. "
            f"Every selection here is a list, so everything is a list too"
        )
        raise SubagentError(msg)

    names = fields.names(value)
    if names is None:
        return absent
    if ALL not in names:
        return names
    if refuse_all is not None:
        msg = f"{source.name}: {key} may not be [{ALL!r}] -- {refuse_all}"
        raise SubagentError(msg)
    if len(names) > 1:
        others = ", ".join(n for n in names if n != ALL)
        msg = (
            f"{source.name}: {key} mixes [{ALL!r}] with {others}; "
            f"[{ALL!r}] is everything, so naming anything beside it means "
            f"one of the two was not meant"
        )
        raise SubagentError(msg)
    return ALL

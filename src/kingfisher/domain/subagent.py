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

One level. A helper is built by a call that is not passed helpers of its own,
so a cycle cannot form -- not because one is detected, but because the code
that would build the second level never runs. `refuse_helpers_with_helpers`
refuses a *catalogue* that asks for one, so nobody writes a `subagents:` line
that is silently ignored.

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
the files is `infrastructure.subagent_store`; translating a spec into deepagents'
`SubAgent` is `infrastructure.agent`.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.capabilities import ALL, Selection

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


class SubagentError(ValueError):
    """Raised when a subagent definition cannot be read."""


@dataclass(frozen=True)
class RunOn:
    """Where a request wants one delegate to run, instead of what its file says.

    One field, and it used to be two. There was a `provider` beside the model,
    with a rule that an override had to be wholesale -- never the file's
    endpoint joined to your model, because a model name sent somewhere that has
    never heard of it is a 404 if you are lucky and a wrong-model run if you are
    not. A model resolves to its own endpoint through the catalogue now, so that
    pairing cannot be expressed and the rule has nothing left to guard.

    A name, not a `"provider:model"` string: that spelling is
    `init_chat_model`'s, and resolving a model through *it* is exactly what
    `infrastructure.models` exists to avoid.
    """

    model: str


@dataclass(frozen=True)
class SubagentSpec:
    """One subagent, as the workspace defines it."""

    name: str
    description: str
    system_prompt: str
    #: The two tool axes, granted apart because they are offered apart: the
    #: built-ins come with deepagents, `tools` is what this workspace wrote.
    #: One list meant a delegate could not ask for a workspace tool without
    #: giving up every built-in, and nothing in the file showed it happening --
    #: the same trade `_permitted_tools` split for a request.
    builtin_tools: Selection = ALL
    tools: Selection = ALL
    #: Skills this delegate is told about. `None` means *none*, which is not
    #: what `tools` means, and the difference is deliberate: tools are what a
    #: delegate needs to act, skills are what it needs to know -- and its body
    #: already is its procedure. Inheriting the caller's index would also put
    #: it in a context whose narrowness is the reason to delegate at all.
    skills: Selection = None
    #: Middleware this delegate runs with, by name, from a registry the
    #: deployment supplies. A name here selects *code*, which is why it is the
    #: one field never widened for an uploaded definition.
    middleware: Selection = None
    #: Delegates this one may consult, by name, from the same catalogue. Absent
    #: means none -- like `skills`, and for the same reason: a delegate that
    #: needed the whole catalogue would not have been worth defining.
    #:
    #: One level. A delegate named here is built without helpers of its own, so
    #: a cycle cannot form -- not because one is detected, but because the code
    #: that would build it never runs. `refuse_helpers_with_helpers` refuses the
    #: *catalogue* that would ask for one, so nobody writes a file whose
    #: `subagents:` is silently ignored.
    subagents: Selection = None
    #: What this delegate runs, by model name, out of what the catalogue
    #: defines. `None` means the deployment's own. Naming one decides where the
    #: prompt goes and whose credentials pay -- the endpoint follows from the
    #: model -- which is why it is granted rather than free.
    model: str | None = None
    #: The same decision, made generally: a name the *deployment* binds to a
    #: model of its own. For a definition that knows what kind of model it needs
    #: and cannot know its name -- which is every definition shipped inside the
    #: wheel, since a vendor's model id is not portable.
    alias: str | None = None
    #: The caller's own keys, carried and never interpreted. Kingfisher reads
    #: nothing here and never will: the moment it did, this would be a field
    #: with rules, and the point of it is to be the one place a definition can
    #: say something this format has no opinion about.
    #:
    #: Read by whatever loads the catalogue, not by the run -- see the module
    #: docstring for why the seam into a turn was left unbuilt.
    metadata: Mapping[str, object] = field(default_factory=dict)


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
    `infrastructure.definitions` does that half.
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
        tools=_selected(document.get("tools"), absent=ALL, key="tools", source=source),
        skills=_selected(document.get("skills"), absent=None, key="skills", source=source),
        middleware=_selected(
            document.get("middleware"), absent=None, key="middleware", source=source
        ),
        subagents=_selected(
            document.get("subagents"), absent=None, key="subagents", source=source
        ),
        # `or None` rather than a conditional: unset and blank mean the same
        # thing here -- run whatever the deployment runs.
        model=fields.text(document.get("model")) or None,
        alias=fields.text(document.get("alias")) or None,
        metadata=_metadata(document, source),
    )



def _selected(value: object, *, absent: Selection, key: str, source: Path) -> Selection:
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
    if len(names) > 1:
        others = ", ".join(n for n in names if n != ALL)
        msg = (
            f"{source.name}: {key} mixes [{ALL!r}] with {others}; "
            f"[{ALL!r}] is everything, so naming anything beside it means "
            f"one of the two was not meant"
        )
        raise SubagentError(msg)
    return ALL

def refuse_helpers_with_helpers(specs: Mapping[str, SubagentSpec]) -> None:
    """Refuse a catalogue where a delegate's helper has helpers of its own.

    Delegation goes one level: a delegate may consult a helper, and the helper
    works alone. That bound is what makes a cycle impossible -- `reviewer`
    naming `second-opinion` naming `reviewer` needs a helper with helpers, and
    there is no such thing.

    Enforced on the *catalogue* rather than per request, so it does not depend
    on which delegates a caller happened to activate: a set of definitions is
    either coherent or it is not. And enforced rather than ignored, because a
    file whose `subagents:` silently did nothing is the failure this format
    refuses unknown keys to avoid, arriving through a key it does define.

    The consequence belongs in the message, and in the README: the same file
    means different things depending on who reached it. `second-opinion.yaml`
    may consult `reviewer` when a caller names it directly, and may not once
    `reviewer` names it as a helper. So one file's contents constrain another's,
    and both names have to appear or the error cannot be acted on -- whoever
    reads it may own neither file.
    """
    for name, spec in sorted(specs.items()):
        for helper in spec.subagents or ():
            nested = specs.get(helper)
            if nested is not None and nested.subagents:
                msg = (
                    f"{name!r} names {helper!r} as a helper, but {helper!r} names "
                    f"helpers of its own ({', '.join(nested.subagents)}); delegation "
                    f"goes one level, so either {name!r} stops naming {helper!r} or "
                    f"{helper!r} stops naming its own"
                )
                raise SubagentError(msg)


@dataclass(frozen=True)
class Wanted:
    """What a delegate asked to run, in whichever of the two ways it may ask.

    Exactly one of these is ever set, and both being `None` is the ordinary
    case: run whatever the deployment runs. `model` is a wire id and needs no
    deployment to interpret it; `alias` is a general name and means nothing
    until something binds it, which is `Config.bound`.

    A record rather than a bare string because the two cannot be told apart by
    looking. Returned as `"cheap"`, a caller has no way to know whether to send
    that to an endpoint or to look it up -- and sending an alias to an endpoint
    is a 404 at best.
    """

    model: str | None = None
    alias: str | None = None


def resolved_model(spec: SubagentSpec, *, override: RunOn | None = None) -> Wanted:
    """What a delegate runs, once the request has had its say.

    The override replaces wholesale, and that includes replacing an *alias* with
    a model. A caller naming a concrete model has said something more specific
    than the file did, and keeping the file's alias beside it would mean
    resolving two answers to one question.

    Almost nothing else left, and that is the result rather than an oversight.
    This was `resolved_endpoint` and returned a `(provider, model)` pair,
    carrying two refusals with it: an operator override that could only ever say
    "all delegates", and the endpoint grant. The first was deleted before this
    change; the second cannot live here any more, because the endpoint is no
    longer written in the definition -- it follows from the model, through a
    catalogue only `Config` holds. Binding an alias needs that catalogue too.

    Which is the layering working rather than fighting it. The domain does not
    read deployment configuration, and
    `test_domain_imports_only_the_standard_library_and_itself` holds it to that,
    so a rule needing the catalogue belongs where the catalogue is. What is
    still a question about *names* -- may this request name this model, may it
    reach this endpoint -- stays in `capabilities`.
    """
    if override is not None:
        return Wanted(model=override.model)
    return Wanted(model=spec.model, alias=spec.alias)

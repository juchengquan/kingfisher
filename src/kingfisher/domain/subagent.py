"""Subagent definitions: `/subagents/<name>.yaml`.

A YAML document. `name`, `description` and `system_prompt` are required;
`tools`, `skills`, `middleware`, `provider` and `model` are optional and all
select by name from what the deployment already offers — how each selection is
enforced is the adapter's problem, not this format's.

    name: reviewer
    description: Checks an analysis for arithmetic errors and unsupported claims.
    tools: [read_file, glob, grep]
    skills: [tabular-qa]
    middleware: [audit]
    provider: openai
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

`provider` names which endpoint this delegate runs against, by style, out of
those the deployment has credentials for. Omitted, it runs where everything
else does. It is granted like `middleware` and for a stronger reason: it
decides which endpoint receives this delegate's prompts and whose
credentials pay for them.

`provider` and `model` move together, in one direction. A definition naming an
endpoint must say what to run there, or the deployment's own model name is sent
somewhere that has never heard of it. `model` alone is fine -- it names
something to run and nothing about where, so it runs where everything else
does.

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
ownership report, a linter -- all of which call `subagent_store.load_all` and
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
from difflib import get_close_matches
from pathlib import Path
from types import MappingProxyType

from kingfisher.domain import fields
from kingfisher.domain.capabilities import ALL, CapabilityError, Selection

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
        "provider",
        "model",
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
        "subagents": (
            "a subagent cannot delegate: deepagents gives it no `task` tool, so "
            "nesting is not something this format can express"
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

#: How alike two field names must look before one is called a typo of the
#: other. Only ever used to *word* an error, never to decide one: a guess that
#: changed behaviour would be the silent-drop bug wearing a spellchecker.
_SIMILARITY = 0.7


class SubagentError(ValueError):
    """Raised when a subagent definition cannot be read."""


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
    #: Which endpoint this delegate runs against, by style name. `None` means
    #: the deployment's default. Selecting one decides where the prompt goes
    #: and whose credentials pay, which is why it is granted rather than free.
    provider: str | None = None
    model: str | None = None
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


def _explain(key: str) -> str:
    """Why this one field is not accepted, in the terms that fit it."""
    if (reason := REFUSED.get(key)) is not None:
        return f"{key!r} is not a field of this format -- {reason}"
    near = get_close_matches(key, KNOWN, n=1, cutoff=_SIMILARITY)
    # Parenthesised, not `; `-joined: that separates one field's explanation
    # from the next, and a hint using it too would blur where each ends.
    hint = f" (did you mean {near[0]!r}?)" if near else ""
    return f"unknown field {key!r}{hint}"


def _refuse_unknown(document: Mapping[str, object], source: Path) -> None:
    """Refuse every field this format does not define, saying why for the ones
    we know about.

    A key we ignore is a key the author believes took effect. That is merely
    annoying for `tolls:`, and worse than annoying for `permissions:`, which
    someone writes *to restrict a delegate* and which currently does nothing at
    all -- the definition reads tighter than the agent it produces.

    All of them at once, not the first. Two typos in a definition used to take
    two runs to find, and the second only after fixing the first -- the same
    reason `place_data` checks every source before it copies any.
    """
    problems = [_explain(key) for key in document if key not in KNOWN]
    if not problems:
        return

    known = ", ".join(sorted(KNOWN))
    msg = f"{source.name}: {'; '.join(problems)} (this format defines: {known})"
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

    # `provider` without `model` sends the *deployment's* model name to another
    # endpoint -- a 404 if you are lucky and a wrong-model run if you are not.
    # Caught here rather than at build time so the message can name the file
    # that got it wrong.
    #
    # Not symmetric: `model` alone is fine. It names something to run and says
    # nothing about where, so it runs wherever the deployment does.
    if document.get("provider") and not document.get("model"):
        msg = (
            f"{source.name}: names provider {fields.text(document['provider'])!r} "
            "but no model; a model name means nothing without the endpoint that "
            "serves it, so name both or neither"
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
        # `or None` rather than a conditional: unset and blank mean the same
        # thing for these two -- run where everything else does.
        provider=fields.text(document.get("provider")) or None,
        model=fields.text(document.get("model")) or None,
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

def resolved_endpoint(spec: SubagentSpec, *, granted: Selection) -> tuple[str | None, str | None]:
    """Where a delegate runs, once the request has had its say.

    The definition is the only author. There was an operator override here --
    `KINGFISHER_MODEL_SUBAGENT` and `KINGFISHER_PROVIDER_SUBAGENT` -- and with
    it a rule about half-overrides, because a model name arriving at an
    endpoint that has never heard of it is a 404 if you are lucky and a
    wrong-model run if you are not. Both are gone: one variable pair could only
    ever say "all delegates", which is not a decision anyone wanted to make,
    and saying it per delegate is what the file already does. The half-pair
    mistake is still refused, at `parse` -- from the definition's side, where
    it can name the file that made it.

    What is left is the grant. `provider` chooses which endpoint receives the
    prompt and whose credentials pay, so a request may narrow it like anything
    else, and a definition naming one it may not use is refused rather than
    quietly run elsewhere.

    A rule, and it takes the spec rather than the `Config` beside it: the
    domain does not read deployment configuration, and
    `test_domain_imports_only_the_standard_library_and_itself` holds it to that.
    """
    provider, model = spec.provider, spec.model

    if provider is not None and granted != ALL and provider not in (granted or ()):
        msg = (
            f"subagent {spec.name!r} names endpoint {provider!r}, which this request "
            f"may not use; permitted {granted}"
        )
        raise CapabilityError(msg)

    return provider, model

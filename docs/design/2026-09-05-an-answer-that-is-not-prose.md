# An answer that is not prose

**Status:** proposed. **Slice 1 landed on 2026-09-06 (#372)**, correcting a
refusal whose reason deepagents contradicted; it went first because it depended
on nothing else here. Slices 2 and 3 are deliberately held: nothing renders
structured output yet, and the argument for waiting is in *What this does not
do* at the foot. It stays here until they land or the proposal is withdrawn.
**Date:** 2026-09-05
**Occasion:** an investigation into carrying A2UI, Google's declarative
agent-driven UI format, as what a kingfisher run returns. The conclusion was
that A2UI should not be named anywhere in this codebase, and that what it needs
is a generic capability this repository already deferred by name.
**Cited by symbol, not by line.** Every reference below names a function, a
constant or a heading, because this document is written to sit unbuilt while
something to render its output appears. Three of its line numbers had already
gone stale two days after it was drafted, one of them moved by its own first
slice. A `grep` for the name still lands; a line number is a claim with a
shelf life.

The `REFUSED` table in `domain/agent.py` declines `response_format` on an agent
definition, and the refusal reads like a specification:

> an agent answers a real caller who may well want a schema, and there is
> nowhere to ask for one yet -- it changes what a *run returns*, so the result,
> the service's response body and streaming all have a stake in it

Three stakeholders and a missing asking-place. This proposal answers all four,
and finds that the hard part is none of them: it is five things upstream does
that are invisible from this codebase, four of which produce a turn that looks
like it worked.

## What a run returns today

Three exits, and prose owns two of them.

`RunResult.answer` is a `str`, passed through `normalize_answer` in
`domain/result.py` to strip the `<think>` blocks some gateways inline. Prose
reaches a streaming caller only as `token` events while it is being
generated -- `RunEvent` has no `message` kind, and its docstring says why:
carrying prose twice *"would mean rendering the same text at two granularities
and asking every consumer to know that"*. Files reach a caller as `artifacts`,
paths relative to the session root.

There is no fourth exit, and nothing in the package holds a value that is
neither text nor a file.

## Why the format is not named here

A2UI is a declarative JSON format: a flat list of components with id
references, rendered by a client that holds a catalogue of pre-approved
components. The safety property everyone quotes -- data, not code -- comes from
that catalogue being *the client's*. The specification gives an envelope; a
client gives the vocabulary.

Which means an `output: a2ui` field would repeat the mistake *A wire format is
named after what it speaks* in `decisions.md` was written about. `api: openai`
promised the wire format every gateway speaks and delivered the one almost none
of them do; the fix was to
name the format after what it speaks and refuse one kingfisher cannot build.
A field naming A2UI would be worse than that, not better. With `api` the claim
was at least checkable -- an adapter exists or it does not. Whether an A2UI
document renders depends on a catalogue living in a client this package has
never seen, at a specification version (v0.9.1, with v1.0 a release candidate
and the project saying to expect changes) that this package cannot pin.

So: kingfisher supports **a schema a deployment registers**. A2UI is one such
schema. The string does not appear in the source, and the day the specification
moves, nothing here moves with it.

## The change, stated plainly

A registry the deployment supplies, mirroring `middleware`:

    Kingfisher(cfg, schemas={"a2ui": A2UI_SCHEMA})

named from an agent definition by the one field that was refused:

    response_format: a2ui

and returned beside the prose answer rather than instead of it:

    RunResult(answer="", structured={...}, stop_reason="end_turn")

The name is checked when the catalogue loads. The schema is checked when the
graph is built. Neither check happens inside a model call, which is the whole
point of both.

## Five things upstream does that this has to survive

Measured against langchain 1.3.15, langchain-core 1.5.5, deepagents 0.7.6,
langgraph 1.2.11. Four of the five produce a turn that reports success.

**The answer would become filler, and the document would be dropped.** With a
`response_format` set, `langchain.agents.factory` appends a `ToolMessage` after
the assistant message and puts the parsed object in a sibling state key:

    return {
        "messages": [output, ToolMessage(content=tool_message_content, ...)],
        "structured_response": structured_response,
    }

where `tool_message_content` defaults to `f"Returning structured response:
{structured_response}"`. `runtime.answer_in` returns `messages[-1].text`. So
`RunResult.answer` becomes that sentence, and the document -- which the `values`
chunk carries, one key away -- is never read.

**A raw schema picks its strategy from a profile kingfisher does not own.**
LangChain wraps a bare schema in `AutoStrategy`, resolved at request time by
`_supports_provider_strategy`, which asks the chat class:

    model_profile = model.profile
    if model_profile is not None and model_profile.get("structured_output") ...:
        return True

The `anthropic` adapter builds `ChatAnthropic`, and `models.yaml.example` sends
every gateway there -- *"a gateway goes on `anthropic`, whatever its vendor
calls itself."* A MiniMax gateway would therefore be judged to support
provider-native structured output because `ChatAnthropic` does, bind a native
parameter the gateway does not implement, and fail inside the first turn with
an error from somebody else's server. That is `api: openai` again, arriving
through LangChain's table instead of this one.

**A dict schema without a `title` gets a different tool name every request.**
`_SchemaSpec` performs no validation on a JSON Schema dict. It stores it, and
derives the name:

    self.name = str(schema.get("title", f"response_format_{str(uuid.uuid4())[:4]}"))

Graphs here are built per request, as `_graph_for` says. So turn one binds
`response_format_a3f9` and records a call to it in the transcript; turn two
binds `response_format_7c21` and replays a history naming a tool that no longer
exists. This one fails on turn two of a session and never on turn one, which is
the shape of bug that reaches production.

**The document is recorded twice and replayed forever.** `_record` reads state
back and `as_transcript` keeps tool calls with their full `args`. A structured
turn therefore writes the document into the session history twice -- once as the
synthetic tool call's arguments, once inside the filler sentence -- and
`user_payload` replays the whole transcript on every later turn.

**A turn that cannot satisfy its schema recommends the wrong lever.** With a
schema bound, `tool_choice` is forced to `"any"` on every model call, and both
graph edges exit only on `structured_response is not None`; otherwise, in
LangChain's own comment, *"there may have been an issue with structured output
generation, so we need to retry"*. So the loop has exactly one success exit and
two bounds. A schema the model can never satisfy burns the recursion limit and
reports `max_steps`, whose message is:

    turn stopped after {cfg.recursion_limit} steps (raise KINGFISHER_RECURSION_LIMIT)

Raising it buys a longer failure. The fix is the schema or the prompt.

## Decisions

**The agent declares it; the request does not ask for it.** The refusal says
there is nowhere to ask, and the asking-place is the definition. Not for
plumbing reasons -- `_graph_for` builds a graph per request anyway, measured at
about 0.6% of a turn -- but because a schema constrains the envelope and does
not make the output good. What does that is a system prompt describing the
client's component catalogue, and `system_prompt` is required in an agent file
with no default precisely so that nothing can supply half of an agent from
somewhere else. Splitting one decision across a file and a wire, with the half
that matters more unable to travel, is the arrangement to avoid.

A deployment wanting per-caller shaping writes two agent files. They genuinely
are two agents: one has been told about a catalogue and the other has not.

**A registry the deployment supplies, not an inline schema and not a catalogue
kind.** Inline means every agent file carries a copy of a versioned schema, and
a v1.0 that lands is a migration across a workspace -- two lists of one fact,
which `test_architecture.py` already spends an import to avoid elsewhere. It
also breaks the property `AgentSpec` says the format exists to have: an agent
file *"activates what the workspace already offers and cannot invent a tool or
write a delegate's prompt"*. A two-hundred-line schema pasted into YAML is
inventing.

A `schemas/` catalogue kind was the other candidate and is rejected for a
concrete reason rather than a stylistic one: because A2UI's catalogue is
client-supplied, a deployment's schema is a build artifact of its client. A code
registry lets it be imported from wherever it is generated; a directory asks
somebody to copy generated JSON into a workspace and keep it in step, which is
the second copy again.

**Typed loosely, for the reason `MiddlewareFactory` is.** Anything LangChain
accepts -- Pydantic model, dataclass, TypedDict, JSON Schema dict -- because
*"a signature narrower than the contract is worse than a loose one, because the
reader who believes it is the one following the docs"* -- the comment on
`MiddlewareFactory` -- and because a generated A2UI schema arrives as a dict.

**Always `ToolStrategy`, constructed here, never a raw schema handed down.**
This is the fix for the second hazard, and it is not a preference: passing a
raw schema means `AutoStrategy` means the profile lookup. `ToolStrategy` works
wherever tool calling works, which is everywhere this harness already requires.
It costs the stricter guarantee provider-native enforcement gives on OpenAI
proper, and buys the same behaviour on every endpoint including the gateways
this project recommends.

The same shape as the existing rule never to hand deepagents a model string,
which forces `use_responses_api=True`: hand a framework the loose form and it
picks a wire behaviour you did not choose.

**The registry key is the schema tool's name, passed explicitly.** The fix for
the third hazard, and free -- kingfisher constructs the `ToolStrategy`, so it
can pass `name=`. Deterministic across requests, and it hands the transcript
translation below the name it needs without recomputing what LangChain would
have called it.

Passing it also settles `title`, which is worth stating because the hazard above
reads like a rule about schemas and is not one. `_SchemaSpec` consults
`schema["title"]` *only* when no name was given; a registry that always passes
one has taken the field out of the question, and a registered schema does not
need a `title` at all.

**The schema's `description` is left alone, and it is the third place the model
is told what it is producing.** Not `title` -- that one is inert once `name=` is
passed -- but `description`, which travels:

    tool=StructuredTool(
        args_schema=schema_spec.json_schema,
        name=schema_spec.name,
        description=schema_spec.description,   # schema.get("description", "")
    )

So a registered schema with no `description` hands the model a tool with an
empty one, alongside a system prompt describing the component catalogue and the
schema constraining the envelope. Three places, and this is the one that arrives
without anybody choosing it.

Left to the schema rather than defaulted here. The registry key is a good tool
*name* and a poor description of what a document is for, and inventing prose
about a deployment's own format is the thing this design has refused everywhere
else -- kingfisher does not know what the components mean. What follows is a
line in whatever guide the field gets, not a fallback in the code: a schema worth
registering says in its `description` what it is for, because the model reads it.

**`answer` keeps holding prose, and a new `structured` field holds the
document.** Not a `str | Mapping` union: `answer` is typed `str` in
`result_payload`, in the audit record, in `run_end(answer_chars=...)` and in
`RunEvent.text`, which streams. And not JSON-in-`answer`, which is `api:
openai` in a third costume -- a field whose name promises prose delivering
something a consumer must know to parse, through a `normalize_answer` regex
that would happily eat a document containing `<think>`.

`answer` is therefore empty on a structured turn, which is the honest reading of
"this turn's answer was not prose". A consumer reading only `answer` gets an
empty string from a turn that worked, and that is accepted.

**The filler sentence never becomes the answer.** Stated separately because it
is the one line that would otherwise be written by accident.

**The transcript records one assistant message carrying the document.** The
synthetic call and its result are translated away rather than stored. This is
what `as_transcript` is for -- *"the only place that knows how LangChain spells
a conversation"* -- and a structured return is LangChain spelling an answer as a
tool call. It halves the replay cost, and it keeps a framework-generated tool
name out of a record whose whole purpose is to outlive the harness:
*"nothing here holds a provider's raw payload."*

Recording a marker instead of the document is not an option. That is the
flattened transcript `domain/transcript.py` rejects by name, and an agent that
cannot see the document it just emitted will contradict it on the next turn.

**One full copy is still replayed in every later turn, and no cap is designed.**
The remaining cost, named rather than hidden. A session-level cap is a real
piece of work with its own argument, and nothing has measured the need.

**A fourth stop reason, for a turn that never satisfied its schema.** The fact
is derivable -- with a schema declared, `max_steps` and an absent `structured`
can only mean this -- and normally that would argue against adding to what
`domain/result.py` calls the closest thing to a wire contract the package has.
What decides it is that the existing message does not merely under-inform: it
tells the operator to raise `KINGFISHER_RECURSION_LIMIT`, and they will, and
they will pay for a longer failure. `STOP_REASONS` says it expects to grow.

It needs a mapping in the service's `errors.py`. `GraphRecursionError` reaching
the HTTP surface with no mapping at all is the precedent for remembering.

**Streaming emits nothing new.** Under `ToolStrategy` the document arrives as
tool-call argument fragments, which `_token_event` already
discards by design: *"chunks holding only usage, or only the fragments of a
tool call's arguments, carry no text and are likewise nothing to show."* So a
structured turn streams `model_call` and `tool_result` events, then goes quiet
through the final generation, then delivers the document on `finished`.

The silence does not threaten the turn. Provider SSE still arrives, so the read
clock still resets and `overrun` still checks between chunks; `streaming.py`'s
`PING` still holds the connection. What is lost is visibility.

The tempting alternative is `RunEvent.channel`, which exists for exactly this
kind of growth -- *"nothing emits `reasoning` yet; the field exists so that when
one does, it is not a new event kind"*. It is a trap here. `event_payload` omits
`channel` when it equals `answer`, so a consumer that does not read the field
sees an ordinary `token` with text in it, and any consumer building an answer by
concatenating `token.text` would splice JSON into its prose. Worth knowing
before `reasoning` is lit up, since it will meet this first.

**No `Capabilities` axis.** Every axis there is a `Selection` and `intersect`
only ever subtracts -- *"a grant is a whitelist, so it can only mean less than
the workspace"*. There is no less of a schema. Removing one does not restrict an
agent, it substitutes a different agent that answers in prose. `middleware` is
on the axis because it selects code that runs; a schema is data that shapes a
return value. Access is already answered one level up by the agent's own
`groups:`.

**`kingfisher list` says which schema an agent declares.** `list` exists to show
what a workspace offers a request, and an agent that answers with a document
rather than prose is the most surprising thing about it. Discovering that from a
run is worse than reading it from a listing.

**`run_end(answer_chars=...)` stops reporting zero for a turn that worked.**
Small, and fixed inside the slice that causes it rather than left for somebody
to debug a year from now.

## Considered and rejected

**Naming A2UI.** The argument is above. Recorded because it will come back the
first time someone wants the name in a feature list, and the answer is that
kingfisher can carry an A2UI document and cannot make it renderable -- the
catalogue is the client's.

**Extending `response_format` to delegates.** The `REFUSED` table in
`subagents/reading.py` refused it with a reason that was checkably false:
deepagents does hand a delegate's structured response somewhere, serialising it
into the tool result with `json.dumps`. The refusal stays; the reason was
corrected in slice 1. The true reason is better than the false one: a delegate's
structured response reaches its parent as *text*, so a schema buys shape
discipline inside the delegate and nothing structured survives the handoff.
Extending it would double the surface -- a second registry lookup, a second
refusal, a second set of format docs -- for a feature with no consumer.

**Validating JSON Schema dicts.** Doing it properly means a `jsonschema`
dependency for a feature nothing consumes; doing it partially is a check that
passes garbage while implying it did not. A well-formed dict that is invalid
JSON Schema therefore passes both checks and fails at the provider. That hole is
left open knowingly, and it is genuinely the same class of failure `api:
openai` was.

**A `structured_output: native | tool` key per endpoint in `models.yaml`.** The
right long-term shape if provider-native enforcement ever matters for cost or
strictness, and the file that already knows a gateway is a gateway is where a
gateway's capabilities belong. Held back because nothing measures the need, and
open data added for an unpriced benefit is data that will be wrong before it is
read.

**Streaming partial documents.** A2UI's incremental story is component-level
updates keyed by id, not half-parsed JSON. A prefix of a document is not a
smaller document, and there is no client to prove otherwise.

## The order to build in

Independent slices, a pull request each off `main`, no stacking.

**1. Correct the delegate refusal.** *Landed 2026-09-06 as #372, the day this
document did.* `subagents/reading.py` stated a reason upstream contradicts, and
it went first because it depended on none of the rest and was worth having
whether or not any of it is ever built. A refusal message stating a false reason
is worse than the generic "unknown field" this format went out of its way to
avoid -- someone will check it, find it wrong, and stop trusting the other two.

**2. The feature.** Registry, the `response_format` field, the load-time name
refusal, `ToolStrategy` with an explicit name, `structured` on `RunResult` and
in `result_payload`, the transcript translation, the fourth stop reason and its
`errors.py` mapping, the `run_end` line, and the documentation in
`guides/formats.md` and `decisions.md`.

Large, and it resists splitting because every seam ships something untrue.
Landing `structured` before anything sets it is a field nothing produces, which
`test_nothing_is_defined_for_tests_alone` exists to catch. Landing the registry
before the result carries anything is a declared schema that silently changes
nothing. Landing it without the transcript translation ships the double
recording on purpose. The smallest honest unit is "a schema declared in a file
produces a document at the far end".

**3. `kingfisher list` shows the declared schema.** Separable and independently
green.

### Guards to mutate

A test that passes is not a test that bites.

* Delete the load-time name check -- the refusal test goes red.
* Drop the explicit `name=` -- a test building two graphs and comparing the
  schema tool's name goes red. This is the turn-two bug, and the one guard here
  that no natural test would catch by accident.
* Keep the synthetic call and result in the transcript -- the translation test
  goes red.
* Return `max_steps` rather than the new reason -- that test goes red.
* Hand down a raw schema rather than a `ToolStrategy` -- a test asserting the
  strategy kingfisher constructs goes red. This is the gateway hazard.

### What the layering costs

Nothing, which was the expected obstacle and is not one. `AgentSpec` gains a
`str | None`. The registry and the `ToolStrategy` live in
`infrastructure/harness/` beside `MiddlewareFactory`. The service passes it
down through an edge already declared. **No new foreign import, and no new
`HARNESS_EDGES` entry.**

## What this does not do

It does not make anything render, and slices 2 and 3 should wait until
something does.

Buildable and verifiable come apart here. A2UI's catalogue is supplied by the
client; the schema to register is that catalogue in A2UI's envelope, and the
system prompt that makes the output good is a description of the same
catalogue. With no client there is no catalogue, so there is no schema to
register and no prompt to write. The entire mechanism can be built and have
nothing true to put through it, and a green suite would not be evidence that
anything renders.

That is the ground *A wire format is named after what it speaks* in
`decisions.md` declined a Chat-Completions row on --
*"nothing needs one, and the table is built to take one the day something
measures the need"*. This proposal is the table. What it buys is that the day
something measures the need, five hazards that cost more to find than to fix
are already found.

# A model catalogue

**Status:** implemented. Two decisions changed while building — see *Corrections*.
**Date:** 2026-08-16

The question that started this was whether `infrastructure/models.py` could read
its configuration from a YAML file instead of holding it in Python. Taken
literally the answer is **no — that trade is a downgrade.** `PROVIDERS` is two
rows guarded by a `Literal` and a test; moving it to YAML costs the static type
and a parse-error path and buys nothing.

The answer changes once you notice what `api_style` actually is. It is two
decisions welded together, and prising them apart is what the file is for.

## The conflation

`api_style` means *which wire format to construct* and *which endpoint and
credentials to use*, and they are 1:1 by construction. `application/config.py:105`
builds the endpoint table keyed by provider name, one entry per style:

```python
endpoints = {
    name: Endpoint(name, url, key)
    for name, other in PROVIDERS.items()
    if name != style and (url := ...) and (key := ...)
}
```

So a deployment has **exactly one endpoint per wire format, forever**. Two
Anthropic-compatible gateways — MiniMax and a local vLLM — cannot both exist:
both want the key `anthropic` and both read `ANTHROPIC_BASE_URL`. The endpoint
a prompt goes to is not addressable, because its name is the name of its wire
format.

Unbundling those is what requires `ApiStyle` to stop being a closed `Literal`
(`config.py:29`). It is also the only argument that justifies the file. "YAML
instead of Python for a two-row table" does not.

## The file

`<workspace>/models.yaml`, overridable with `KINGFISHER_MODELS_FILE`, following
`skills_root` / `subagents_root` / `tools_root` — default inside the workspace,
individually relocatable so a fleet can share one reviewed copy.

```yaml
endpoints:
  minimax:
    api: anthropic
    base_url: https://api.minimaxi.com/anthropic
    key_env: MINIMAX_API_KEY
  openai:
    api: openai
    base_url: https://api.openai.com/v1
    key_env: OPENAI_API_KEY

default: MiniMax-M3

models:
  MiniMax-M3:
    endpoint: minimax
    max_tokens: 4096
  MiniMax-M2.5:
    endpoint: minimax
    max_tokens: 2048
    timeout_s: 60
  gpt-5:
    endpoint: openai
    max_tokens: 32000
    extra:
      reasoning_effort: high
```

`api` selects from a **closed** registry of adapters that ships with kingfisher.
Endpoints are open; wire formats are not.

## Decisions

### The adapter registry stays in Python

A deployment does not name a chat class. `Provider.resolve()` is
`getattr(import_module(module_name), class_name)`, so a config file naming
`chat_class` executes an arbitrary import in-process. This repo sandboxes
`execute` because it measured that the shell could read `.env`, `~/.aws` and the
`gh` token, and `tools_dir` is deliberately not a backend route for the same
reason. A config file must not be an import vector.

The openness would be fake anyway. `test_models.py:78` carries `LANDING_SITES`
because the providers disagree on attribute names — `base_url` lands on
`anthropic_api_url` for one and `openai_api_base` for the other — and
`test_every_provider_has_landing_sites` guards it. A new class needs that row.
So a new wire format is a kingfisher release either way, and the "open" half of
a `chat_class` field could never be exercised without one.

### Two tables, not one

Endpoints hold credentials; models point at an endpoint and carry params. One
table would denormalise, and the common case is precisely the bad one: several
models behind a single gateway, repeating `base_url` and `key_env` on each row.
Move the gateway, miss a row, and traffic keeps going to the old URL with the
old key — the failure `endpoint_for` already refuses to be quiet about.

Two tables also land on types that exist. `Endpoint(api_style, base_url, api_key)`
(`config.py:36`) *is* the first table, and `Config.endpoints` is already the
mapping. The second is not new either: `resolved_endpoint` already returns a
`(provider, model)` pair — a profile with the params missing, written inline in
each subagent file.

### Keys are real names

Endpoints are named for what they are (`minimax`, `openai`); a model entry's key
is the model id sent on the wire. No role aliases (`fast`, `cheap`), which keeps
the file self-describing and avoids a naming layer nobody asked for.

This pays off in the subagent format. A model id resolves to its endpoint
through the table, so **`provider:` deletes entirely** — along with the half-pair
refusal at `subagent.py:323` and most of `resolved_endpoint`. Today
`model: gpt-5` with no `provider:` sends `gpt-5` to the anthropic gateway, which
is the 404 that rule exists to prevent; with a lookup it routes correctly.

### The models table is closed

A model name not in the table is refused. This is what buys the simplification:
an overlay that passed unlisted names through to the default endpoint would keep
both routing paths alive, so the table would be added without anything being
removed.

**Consequence: the shipped presets stop naming models.** `extractor.yaml` says
`model: MiniMax-M2.5`, which a closed table refuses on a deployment with no
MiniMax entry. That file's own comment already worries that naming an *endpoint*
"would only stop this file working on a deployment pointed somewhere else" — and
does not notice that naming a vendor's model id has exactly the same problem. A
file that ships with kingfisher cannot portably name `MiniMax-M2.5`.

So the presets run on the deployment's default, and cost routing moves to
documentation. This is the same argument that deleted `KINGFISHER_MODEL_SUBAGENT`
for being the wrong granularity: which model is cheap *here* is a deployment's
answer, not a preset's.

Today those files fail too — they send an unknown id to whatever endpoint is
configured and get a runtime 404 mid-run. Closing the table converts a late,
confusing failure into an early, legible one.

**Rejected: reserved role names.** A small set of aliases the table may bind
(`fast: MiniMax-M2.5`) would let a preset name a role and an operator bind it,
keeping the cheap-model demonstration portable. Declined because it reintroduces
the indirection layer that real keys were chosen to avoid, and puts two naming
schemes in one table — a reader would have to know which names are model ids and
which are roles. Reconsider only if operators turn out to be writing the same
three bindings by hand.

### `build_model` takes a profile, not a `Config`

`Config` drops `model`, `api_style`, `base_url`, `api_key` and `max_tokens`, and
gains `models: Mapping[str, ModelProfile]` plus the default name.

Keeping the `Config` signature would reintroduce the defect this exists to
remove. `Config.max_tokens` would mean "the default model's ceiling" while the
table also says it per model — two sources for one value — and it fails quietly:
`delegation.py:328` `replace`s only `model`, `api_style`, `base_url` and
`api_key`, so a delegate on `gpt-5` with `max_tokens: 32000` would build at the
deployment's 4096 because nobody extended that call. Same shape as the bug
`Provider.extra` is guarded against: a configured value silently discarded.

This also deletes the `replace(cfg, ...)` pattern. Its defence at
`delegation.py:323` — "an endpoint is exactly the three Config fields a model is
built from" — stops being true once a profile carries params.

`build_model` is exported in `__init__.py`'s `__all__`, so this is a public
signature change. Pre-release; take it cleanly.

### `timeout_s` splits, and what remains is renamed

`cfg.timeout_s` currently serves three unrelated consumers: the model request
timeout (`models.py:122`), the `execute` shell timeout (`backend.py:358`), and
the JS interpreter sandbox timeout (`agent.py:240`). One number, three jobs.

The model timeout moves into the table. What is left becomes
`Config.execution_timeout_s` / `KINGFISHER_EXECUTION_TIMEOUT_S`, bounding the
shell and the interpreter only. A slow reasoning model gets a longer leash
without every shell command getting one, which is impossible today.

The rename belongs in *this* change rather than a later one. The field's meaning
is already changing here — three consumers to two — so its docstring is rewritten
regardless, and keeping the old name would ship a field whose name no longer
describes it. Two renames across two releases is worse than one, and
`.env.example` is being rewritten anyway.

`execution_timeout_s` rather than `command_timeout_s`: the interpreter does not
run a command, it runs code. `config.py` already has the word for what the two
have in common, describing the interpreter as "the one **execution surface**
`execute` can never be."

**One field, not two.** Splitting further into `shell_timeout_s` and
`interpreter_timeout_s` would be consistent with this document's thesis, and is
still declined. `api_style` had to be unbundled because one endpoint per wire
format was a hard ceiling blocking a real deployment; there is no equivalent
pressure here. Both bound how long the agent may execute something, no case for
diverging values has been measured, and a second variable runs against the goal
of fewer of them. Split it when someone has the case — the name will not need
changing again then.

**A model entry's `timeout_s` therefore falls back to a constant, not to
`Config`.** The earlier shape of this decision was "per-model timeout with a
`Config` fallback," which the rename exposes as the same conflation in a new
place: falling back to `execution_timeout_s` would make the shell's bound double
as the model default. So the models file is the only place a model timeout is
set, defaulting to 120s in the profile — exactly the symmetry `max_tokens`
already has with its 4096. `Config` has no model timeout at all.

`max_tokens` needs no such care — `models.py:121` is its only reader.

Per `runtime.py:47`, `messages` streaming turns every call into SSE and resets
the read clock per chunk, so `timeout_s` bounds inter-chunk silence rather than
total generation. The per-model override is correct but not load-bearing.

### Literal `base_url`, `key_env` indirection

The base URL is a literal in the file. It is topology, not a secret — and if it
came from an env-var name the unbundling would not work: two Anthropic-style
gateways cannot both read `ANTHROPIC_BASE_URL`, so you would invent one variable
per endpoint, which is *more* env vars, not fewer.

The key stays an env-var name, because of what this file wants to be. Every
other authored-content directory — `skills/`, `subagents/`, `tools/` — is
described as content "a person authored, reviewed and deployed," relocatable so
deployments share one reviewed copy "instead of each keeping a copy nobody can
audit centrally." A models file is that kind of artifact: it says where your
prompts go, so it belongs in version control and code review. Keys in it make
that impossible and add a second credential file to the surface `execute` is
sandboxed away from.

`key_env` is explicit, not derived from the endpoint name. An endpoint whose key
silently resolved to a variable nobody wrote is what `endpoint_for` raises about.

**Net:** required env vars drop from five (`KINGFISHER_WORKSPACE`,
`KINGFISHER_API_STYLE`, `KINGFISHER_MODEL`, plus the style's URL and key) to two
(`KINGFISHER_WORKSPACE` and one key per endpoint). `KINGFISHER_API_STYLE`,
`KINGFISHER_MODEL` and `KINGFISHER_MAX_TOKENS` are deleted, and
`KINGFISHER_TIMEOUT_S` is renamed `KINGFISHER_EXECUTION_TIMEOUT_S`.

### The file is required

No file is a startup error. No shipped default table, no fallback to the old env
path.

`application/config.py:8` already argues this: `api_style` is required and
deliberately has no default because "a default would silently pick the wrong
shape the first time kingfisher is pointed somewhere new." A default table would
name endpoints this deployment may have no credentials for, and a fallback makes
"file absent" indistinguishable from "file found" — including when
`KINGFISHER_MODELS_FILE` points at the wrong path.

The absent-file error prints a minimal working example, not just a path. It is
now the first thing a new deployment hits, and it replaces a message
(`KINGFISHER_API_STYLE is required but not set`) that came with an unusually
explanatory `.env.example`. Add `models.yaml.example`; shrink `.env.example` to
the workspace, the keys and the operational flags.

### Params: named, closed, plus `extra`

A model entry takes named params (`max_tokens`, `timeout_s`, `temperature`,
`top_p`) and refuses unknown keys, plus an explicit `extra:` mapping forwarded as
kwargs for vendor-specific settings.

Straight pass-through would contradict the strongest rule this codebase has
about its own formats — `domain/subagent.py`: "A field this format does not
define is refused, not ignored. Ignoring one is indistinguishable from honouring
it," with `tolls:` producing a delegate holding every tool and `permissions:`
reading as a restriction while doing nothing. `max_token:` singular would parse,
forward, and give you the default with no error anywhere.

Fully closed has the problem that started this: `reasoning_effort` and
Anthropic's `thinking` budget are real, do not normalise, and would each need a
release.

Named `extra` deliberately: `Provider.extra` already means this and already
carries the rule — "additive only... cannot name one of the five that come from
`Config`... a provider row must not quietly overrule a value the user
configured," enforced by `test_a_provider_row_cannot_overrule_a_configured_value`.
Same rule, same name, one concept.

**No implicit defaults beyond `max_tokens`.** An omitted param is not passed at
all. `temperature` is the one that matters: defaulting it to `0.0` for
determinism looks right in a repo that disables `skills_enabled` because "a
self-editing prompt makes runs non-reproducible" — but it would silently change
every existing deployment and take a decision away from the operator in the file
whose purpose is to hand it to them. `max_tokens` keeps its 4096, since a missing
ceiling behaves differently per vendor.

### Grants: `providers` becomes `endpoints`; `RunOn.provider` dies

`Capabilities` already has both `providers` (`:117`, default `ALL`) and `models`
(`:128`, default `None`), with deliberately opposite defaults — the first
narrows what reviewed definitions may reach, the second gates what untrusted
callers may name. That distinction survives; collapsing them would force one
default on both, and either choice is wrong for one.

`providers` is renamed `endpoints` and re-expressed as a check on the endpoint a
model resolves to. The rename is not cosmetic: `provider:` is being deleted from
the subagent format and the file calls them `endpoints:`, so leaving a grant
named `providers` would leave the word meaning something the format no longer
has. It matches `Config.endpoints` and the `Endpoint` dataclass.

`RunOn` loses `provider` and becomes a model name. Its docstring rule — that an
override must be wholesale and never "half of each — the file's endpoint with
your model" — describes a state that is now unconstructible, since the model
determines the endpoint.

An incoherent grant pair (`models: [gpt-5]` with `endpoints: [minimax]`) is
refused at capability resolution, so the message can name both lines. Same
instinct as `refuse_helpers_with_helpers` checking the catalogue for coherence
rather than waiting for a request to trip over it.

### Module layout

- `config.py` gains `ModelProfile` beside `Endpoint` — records belonging to no
  layer, read by `application/` and `infrastructure/` alike.
- `infrastructure/model_catalogue.py` (new) reads and validates the file.
- `infrastructure/models.py` keeps the adapter registry and `build_model`.

`domain/` cannot host the format the way it hosts a subagent's: it may import
only stdlib and itself, and is forbidden from reading `Config`.

The parse does not go in `definitions.py`, whose charter is stated and narrow —
"Reading a definition document into the value **the domain** works with." A
model profile is not a domain value, and widening it would make its name a
guess, which its own closing paragraph refuses.

`safe_load`, for a changed reason. `definitions.py` justifies it because
definitions "arrive from a catalogue service... which makes them input rather
than something we wrote." This file is operator-authored, so that does not
apply — but it names credential variables, and `yaml.load` would let a crafted
document construct arbitrary objects at startup.

`application/config.py` calls the parser and puts the result on `Config`,
exactly as it already imports `PROVIDERS` from `infrastructure.models`.

**Deferred:** if `model_catalogue.py` lands under ~80 lines, fold it back into
`models.py` (125 lines today) rather than keep two thin modules.

### Unreachable endpoints are dropped, and that is the whole check

A committable file shared across a fleet will list endpoints a given machine has
no key for. Requiring every `key_env` would kill the sharing that `key_env`
exists for.

So: an endpoint whose `key_env` is unset is **dropped, with a startup warning**,
and its models leave the table. Startup then refuses if the `default:` model is
gone — it is needed by definition, so a deployment that cannot run it has not
finished being set up.

This generalises an existing rule rather than inventing one — `Config.endpoints`
already means "every style this deployment has credentials for," computed by
presence.

**The catalogue-wide subagent check is not built, and should not be.** This
document originally called for refusing at startup if any catalogue subagent
named a model that was gone, by analogy with `refuse_helpers_with_helpers`. The
analogy is wrong, and building it proved so by breaking `run_on` outright.
Helper depth is structural: a catalogue asking for two levels is incoherent
however it is used, and no request can rescue it. An unrunnable model is the
opposite — `run_on` exists *precisely* so a caller can put a shipped delegate on
a model their credentials reach without editing a file they may not own, and a
catalogue-wide refusal fires before the override can apply.

So it stays per-delegate, at `as_subagent`, after the override has resolved.
Seeding a preset you cannot run costs nothing until you activate it, which is
what `presets/README.md` already documented. The refusal is wrapped there to
name the delegate: `resolve_model` knows the model and the catalogue but not who
asked, and this is the one refusal that fires on a file the reader may not own.

The error names the **variable**, not the endpoint. "endpoint `minimax` has no
credentials" sends someone to the YAML, where everything looks correct.
"`MINIMAX_API_KEY` is not set, so endpoint `minimax` is unavailable and model
`MiniMax-M3` cannot be used" sends them to the right place, in `_require`'s
existing shape.

## The run log

`JsonlRunLogger` (`runlog.py:81`) writes `model` and `api_style` on every line.
Replace `api_style` with the endpoint name. After this change `api_style` means
"which adapter" — the least informative of the three facts, and the one that
cannot distinguish two MiniMax gateways. `{"model": "MiniMax-M3", "endpoint":
"minimax"}` answers "where did this prompt go." Callers: `service.py:837`,
`main.py:506`.

## The guard that matters

Most of the test work is routine. `test_every_api_style_has_a_provider` dies with
the `Literal`; its job moves to runtime validation of `api:` against the adapter
registry, and wants a test that an unknown `api:` fails at load. `LANDING_SITES`
survives renamed, still the per-adapter edit site.
`test_describing_a_provider_does_not_import_its_sdk` survives unchanged, since
adapters keep `chat_class` as strings. The closed-table refusal, the `extra`
collision and the dropped-endpoint path each want one test.

**The guard with teeth is whether a per-model param reaches a *delegate's*
client.**

`test_every_config_value_reaches_the_client` (`test_models.py:97`) proves the
five values land — for the deployment default. There is no equivalent for a
delegate, and that is exactly where the `replace`-drops-a-field bug lives. The
whole point of per-model params is `max_tokens`, and the code path that gives a
delegate its model is the one path with no coverage that the value survives.

Write first: build a delegate on a model whose entry sets `max_tokens` and
`timeout_s` different from the deployment's, and assert both land at the
`LANDING_SITES` attribute for its adapter. Then **mutate it** — drop `max_tokens`
from the profile-to-kwargs step and confirm that test, and only that test, goes
red. If it stays green the guard is decorative, which matters here specifically
because the existing suite looks like it covers this and does not.

Second: assert a delegate on a non-default endpoint gets that endpoint's
`base_url` and `api_key`. `test_the_model_comes_from_the_config_it_is_handed`
covers the model name only.

**Result.** Both guards are in `test_subagent_endpoint.py`. The mutation was run
twice. Dropping `max_tokens` from `ModelProfile.kwargs` failed 6 tests — caught,
but not proof, since that mutation breaks the deployment path too. The faithful
one reproduces the historical bug exactly: let the delegate keep its resolved
model name and endpoint, but take the *deployment's* params, which is what
`replace(cfg, model=…, base_url=…, api_key=…)` did. That failed **2 tests, both
of them the new guards, and nothing else** — so the bug had no coverage before
and the coverage it has now is targeted rather than incidental.

## Corrections

Premises that did not survive, recorded rather than dropped.

**"Move the model config to YAML" is not the change.** Moving `PROVIDERS` alone
trades a `get_args`-derived `Literal` and `test_models.py:24` for an untyped file
and a new parse-error path. Unbundling wire format from endpoint is the change;
the file is a consequence.

**Removing `ApiStyle` looked more invasive than it is.** `provider:` in a
subagent YAML is already an open `str`, never checked against `API_STYLES` —
the domain deliberately does not read deployment config, so it is validated
dynamically by `cfg.endpoint_for()`. The `Literal` guards only `from_env`
rejecting a typo'd `KINGFISHER_API_STYLE` and the static type on two fields.

**`timeout_s` was assumed to be the model timeout.** It has three consumers.
Moving it wholesale into the table would have silently changed shell and
interpreter behaviour.

**A model grant already exists.** `Capabilities.models` was designed for exactly
this and is documented as such — "granted per name rather than as a switch." The
closed table makes its vocabulary exact rather than introducing it.

**Removing `ApiStyle` and making the file deployment-authored are one decision,
not two.** A file shipped inside the package leaves the set of styles closed at
build time, and the `Literal` was the only thing rejecting a typo — that version
deletes a guard and replaces it with nothing.

**The catalogue-wide coherence check was designed, built, and removed.** It is
the one decision here that a test caught rather than a reader. Wiring it turned
`test_run_on.py` red across the board, because that file's premise — a shipped
delegate naming a model this deployment cannot run, rescued by an override — is
exactly what the check forbids. `presets/README.md` had already documented the
right rule ("it fails only for the preset you activated"); this document
contradicted it, and the contradiction survived design review because the
`refuse_helpers_with_helpers` analogy sounded right. See the section above.

**The `execution_timeout_s` rename was scoped out, then back in.** The argument
for deferring it was scope discipline, and it does not hold: this change already
takes `timeout_s` from three consumers to two, so deferring the rename ships a
field whose name describes neither its old meaning nor its new one. Pulling it in
then exposed a second thing — that a model timeout falling back to `Config` would
recreate the conflation one level down, which is why the fallback is a constant.

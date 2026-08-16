# A subagent's endpoint and model — spec

**Status:** agreed, partly implemented
**Date:** 2026-08-16

The question was whether a subagent definition could carry both a provider and
a model, since "sometimes we use the same provider but different models".
Exploring it found that half the request already works, the other half was
blocked by `Config` rather than by the format, and that the operator override
meant to make either negotiable has never once fired.

## What already works

`model:` gives a delegate a different model on the same endpoint. Verified:

```
parent model   : MiniMax-M3
subagent model : MiniMax-M2.5
same endpoint  : True
```

`_as_subagent` builds it with `build_model(replace(cfg, model=model_id))` —
swapping the model name and keeping the style, URL and key, because there is
only one of each to keep.

## The defect: an operator override that does nothing (fixed)

`role_models` is populated from three fixed keys:

```python
ROLES = ("main", "subagent", "summarizer")
role_models = {role: ... for role in ROLES if KINGFISHER_MODEL_<ROLE> is set}
```

and looked up by the **definition's name**:

```python
cfg.role_models.get(spec.name, spec.model)
```

So the override fires only for a subagent literally named `main`, `subagent` or
`summarizer`. Measured:

```
role_models          : {'subagent': 'CHEAP-MODEL'}
operator asked for   : CHEAP-MODEL
subagent actually got: MiniMax-M2.5
```

The comment beside that line claims the opposite — *"`role_models` wins over
the definition: which model a role runs on is an operator's cost decision, and
it should not require editing content."* It is a cost control that silently does
nothing, which is the same class of failure as the quota gap: a bound nobody
knows is absent.

**Decision: look it up by role.** `KINGFISHER_MODEL_SUBAGENT` starts doing what
it says. Per-subagent overrides by name would need `ROLES` to become unbounded
and its names to come from workspace content, which is a different decision from
fixing a broken one.

## `provider:` — a delegate on another endpoint

Blocked by `Config`, which holds one `api_style`, one `base_url`, one `api_key`.
That is the "vendors simultaneously" case set aside in the session-scoping work,
where it was noted that *"Config physically cannot hold two credential pairs."*

But `.env.example` already carries both credential pairs and reads only one:

```
ANTHROPIC_BASE_URL / ANTHROPIC_API_KEY     the gateway path
OPENAI_BASE_URL    / OPENAI_API_KEY        OpenAI proper, Responses API
KINGFISHER_API_STYLE=anthropic             which one is the default
```

So in this codebase **a style already is an endpoint**.

**Decision: `provider:` names a style.** `from_env` reads every style whose
credentials are set; `KINGFISHER_API_STYLE` remains the default for anything
that does not say otherwise. No new configuration shape, and the names are
already meaningful and documented.

The limit accepted: one endpoint per style, so two gateways both speaking
`anthropic` cannot both be configured. That needs a general endpoint registry —
a new env convention, a name→endpoint table, and every existing variable
becoming a special case of it.

## Clamped, like middleware and for a stronger reason

A `provider:` line decides **which credentials are used, whose money pays, and
which endpoint receives the run's prompts and file contents**. In an uploaded
definition that is a caller routing the deployment's traffic.

This codebase already treats egress as deliberate: `enforce_local_only_tracing`
exists because *"a stray `LANGSMITH_TRACING` left over from another project
would otherwise start exporting prompts and file contents off this machine."*

**Decision: a `providers` axis on `Capabilities`, granted by the deployment and
absent from `including`** — so an uploaded definition gets no exemption, by the
same structural means as `middleware`: there is no parameter to pass.

## The override and the definition must move together

Overriding the model alone, against a definition that pins `provider: openai`,
sends a MiniMax model name to OpenAI — a 404 at best, a wrong-model run at
worst. So the pair is atomic: `KINGFISHER_PROVIDER_SUBAGENT` overrides the
endpoint, and an override that sets only the model against a definition that
pins a provider is **refused** rather than resolved. Which endpoint runs which
model should not be decided by two people who cannot see each other's half.

## Sequence

1. ~~**The `role_models` fix.**~~ Done. A test for it already existed and
   *passed*, because it built `role_models={"cheap": ...}` by hand -- a key
   `from_env` cannot produce. It validated a path production cannot reach,
   which is why the defect survived it. Corrected in place rather than
   joined by a second test.
2. **`provider:`** — the field, the multi-endpoint `Config`, the `providers`
   grant axis, and the atomic-override rule.

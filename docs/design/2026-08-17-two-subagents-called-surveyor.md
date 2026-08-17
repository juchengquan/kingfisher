# Two subagents called `surveyor`, from folders nobody coordinated

**Status:** implemented.
**Date:** 2026-08-17

The last of three. Skills got sources, tools got references, and this is the
same clash in the last place that still refused it outright — and it failed
harder than either:

```
cannot load: custom/profiler.yaml: duplicate subagent name 'profiler',
             already defined by analysis/profiler.yaml
exit code: 1
```

Not one section of `--list`, the whole inventory. No run could start, and
nobody who owned neither file could do anything about it.

## What was measured

Handing deepagents two subagents of one name does not raise. It compiles one:

```
two subagents both named 'profiler' -> compiled: ['general-purpose', 'profiler']
```

So the loader's refusal was protecting something real. It was just refusing in
the wrong place — at load, before anyone knows which subagent this run wants.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | **The catalogue keeps both**, keyed as a grant writes them: `analysis/profiler.yaml::profiler`, flat where a name is its own. | The same shape tools got, and the same reason. A clash between two files should not stop a deployment that was never going to activate both. |
| S2 | **An agent holding two of a name is refused**, naming both files. | Where the constraint actually lives. A roster is keyed by name, and the measurement above is what that costs: one delegate silently never exists. |
| S3 | **A bare name two files offer is refused**, told apart from activating both. | Different mistakes. One is a caller who has not noticed there are two; the other is a caller who wants both and cannot have them. Sending either to the other's message wastes the reader's time. |
| S4 | **`*` is refused against a colliding catalogue**, rather than quietly meaning one of them. | This is where subagents and tools part company, and the reason is the default. `subagents` activates nothing unless asked, so only a caller who explicitly wrote `*` ever sees this. `tools` defaults to everything, so the same refusal there would have broken the common path — which is why *that* axis split a grant from what an agent carries. |
| S5 | **The model still reaches a delegate by its plain name.** A reference is how a *request* says which. | `subagent_type` is what the model emits, and only one of the pair is ever activated, so there is nothing to tell apart at that end. |

## What this does not do

Two delegates cannot each have their own `surveyor` as a helper. That would
need the grant split tools have, because a helper is clamped by the request's
`subagents` grant — so both would have to be activated, and S2 refuses it.

Left undone deliberately: it is a narrower want than the tools case, it costs
the main agent the ability to reach either delegate, and the simple version is
a strict subset. If it is ever needed, the split layers on top without rework.

## Corrected while building

The note in `2026-08-17-two-tools-called-fetch.md` justified deferring this by
saying there is "one roster per request". That is wrong: each delegate is given
its own `SubAgentMiddleware`, so helper rosters are separate and two `surveyor`s
could coexist across them exactly as two `fetch`es now do. The real difference
is the default, which is S4.

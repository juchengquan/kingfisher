# What the catalogue dropped, and the two errors that followed

**Status:** implemented, C1-C9.
**Date:** 2026-08-18

`doctor` was left with two open questions: whether it should ever make a real
call to prove a credential works, and whether it overlaps the smoke. Asking the
first found a bug underneath it, and the bug is most of this document.

An endpoint whose `key_env` is unset is dropped as the catalogue loads, with a
warning, and the models on it go quietly with it. That is deliberate and right —
*"one reviewed file works across a fleet holding different subsets of keys"*.
What is not right is that nothing afterwards can tell the difference between a
name this file never defined and a name this machine cannot reach, so two
messages that exist to point somebody at a fix point them at the wrong one.

## What a deployment is actually told

A catalogue defining two endpoints, with only `MINIMAX_API_KEY` set. `gpt-5` is
defined in the file, on the `openai` endpoint.

```
$ kingfisher doctor
UserWarning: .../models.yaml: no credentials for endpoint(s) openai ...
ok    catalogue   2 model(s) on 1 endpoint(s), default 'MiniMax-M3'
FAIL  aliases     alternate -> gpt-5: named in `aliases:`, not defined under `models:`
                  -> bind each alias to a model this catalogue defines
```

```python
>>> models.resolve("gpt-5")
ConfigError: no model 'gpt-5' defined in .../models.yaml; this deployment can
run ('MiniMax-M2.5', 'MiniMax-M3')
```

Three things wrong, in rising order of seriousness. A warning printed above the
report rather than being one of its lines. A green tick on a catalogue that
lost an endpoint, because the count is of survivors. And two messages that name
the file and the list authoritatively while being false: `gpt-5` **is** defined
there, and the remedy sends a reader to edit YAML that is correct. The fix is
`export OPENAI_API_KEY`, which neither message mentions.

## The promise that was already written down

None of this needed inventing. `_aliases` keeps a binding whose model was
dropped, on purpose, and says why:

> A binding whose model was *dropped* for want of a key is kept: it is a real
> binding this machine cannot currently follow, and saying so at the point of
> use names the endpoint and the variable, where refusing here could only name
> the alias.

And `resolve` has the branch that would say it, with a comment claiming it is
reachable *"for a model a request named, whose endpoint was dropped for having
no credentials on this machine."* It is not reachable: `_models` filters those
models out before they arrive, so the lookup fails one branch earlier and
answers the wrong question. The intent survived in two docstrings and in dead
code, and nowhere else.

## Decisions

| # | Decision | Why |
|---|---|---|
| C1 | **`Models` keeps what it dropped.** | A warning is not a data structure: it fires once, at load, to stderr, and any caller wanting to *report* it has to have been listening at that moment. `doctor` was not, which is why it printed a tick. The record already documents the drop in a comment, so the fact is part of its contract — it is simply not inspectable. |
| C2 | **`resolve` consults it, so the dead branch becomes live.** | Fixed in the library rather than in `doctor`, because every caller inherits the wrong message: a request naming `run_on: gpt-5`, the server, and the command alike. Fixing the command alone leaves the API lying to everybody else, and leaves `doctor` re-deriving a diagnosis the catalogue could simply hand it. |
| C3 | **The new field must read wrong when misused.** | `Models.models` means *what can run*, and a second map of near-models beside it is exactly the sort of thing that gets read by mistake. `unreachable` rather than `dropped_models`: a name that makes `for m in models.unreachable: run(m)` look like the error it is. |
| C4 | **`doctor` reports a dropped endpoint as a check, and it is a warning.** | A shared catalogue naming endpoints this machine cannot reach is *"the normal case"* by the loader's own account, so failing on it would fail on the arrangement the design encourages. It becomes a failure only through C5, when something actually names one. |
| C5 | **`doctor` checks that each definition can run, through `model_for`.** | The highest-value check available and the one this bug produces: a workspace where `second-opinion` binds an alias to a dropped model looks entirely healthy until somebody activates that delegate. Through the same function the build uses, so the two cannot drift — and it costs a dictionary lookup. |
| C6 | **No probe. `doctor` never makes a model call.** | Its premise is being cheap enough to run before every deployment, and a command that sometimes costs money is one that comes out of the pipeline. A failed probe is also ambiguous in a way the free checks are not — network, rate limit, endpoint down, wrong key, one red line — where every other line here points at one fix. After C1–C5 the case a probe would catch has shrunk to *present but rejected*. |
| C7 | **The limit is printed, not just documented.** | `doctor` checks that a credential is **present**, not that it works, and a green tick that does not say so claims more than it knows. One clause on the catalogue line. |
| C8 | **The smoke stays out of the shipped command.** | It is not the expensive version of `doctor`. It checks `GROUND_TRUTH` against a fixture kingfisher generates and wrote the answers to — *does kingfisher still get its own dataset right*, which is a maintainer's question. Shipping it means shipping the fixture, the ground truth and the eval harness so a deployment can answer something it never asked. |
| C9 | **`doctor` points at the caller's own task as the end-to-end test.** | The honest consequence of C6 and C8 is that nothing shipped makes a real call. That is a deliberate hole and saying so is better than implying it is covered: a deployment's own task through `run(Request(...))` is a better proof than any fixture of ours. |

## Measurements

| | |
|---|---|
| what `resolve` says for a dropped model | `no model 'gpt-5' defined in .../models.yaml` — and it is defined there, on an endpoint with no key |
| the branch that would say it properly | unreachable. `_models` returns `{n: p for n, p in profiles.items() if p.endpoint in endpoints}`, so a dropped model never reaches the `endpoint is None` test its own comment describes |
| what `doctor` says today | `ok catalogue 2 model(s) on 1 endpoint(s)` — a tick over a lost endpoint — then a `FAIL` on the alias, with a remedy pointing at correct YAML |
| where the drop is reported | `warnings.warn` at load, discarded afterwards. `Models` keeps only surviving endpoints |
| what the loader already decides correctly | an alias binding a model the file does not define is refused at load; one whose model was dropped for want of a key is kept. `doctor` re-derived that distinction and got it backwards |
| what reports unrunnable definitions today | nothing. `indistinct_delegates` skips them deliberately — *"the build refuses it with the message worth reading; reporting is not refusing"* |
| the export C5 needs | `model_for`, in `infrastructure/harness/delegation.py`. The fifth name a consumer has forced public, after `offered`, `SKILL_LAYOUT`, `split_reference` and `shell_confinement` |
| what the smoke asserts | `GROUND_TRUTH` from `evals.dataset`: exact counts and seeded issue kinds in a dataset the harness generates |

## Sequenced plans

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | C1 and C3: `Models.unreachable`, carrying the endpoint and the variable that would have made it usable. Nothing reads it yet; the warning stays. | — |
| **2** | C2: `resolve` consults it, and the message names the endpoint and the variable. The dead branch's comment stops describing something that cannot happen. | 1 |
| **3** | C4, C5, C7: `doctor` gains a credentials check and a definitions check, and the catalogue line says what it did not verify. `model_for` becomes public. | 2 |
| **4** | C9: the description points at `run(Request(...))`, and B10's list of checks in *a-command-worth-shipping* is updated to match what `doctor` does. | 3 |

Phase 2 is the one worth doing even alone: it fixes a wrong message every caller
can hit, where phase 3 improves a command somebody has to run.

## Still undecided

- **Whether the load-time warning stays once `unreachable` exists.** Two
  channels for one fact is how they drift, and the warning is the one that
  reaches somebody who never runs `doctor`. Keeping both is the safe answer and
  probably right; nothing here settles it.
- **Whether a definition naming an unreachable model should refuse at load.**
  C5 reports it. The loader deliberately does not refuse, on the grounds that
  the message is better at the point of use — but a workspace that cannot serve
  a delegate it defines is arguably a configuration error, and the argument was
  made before anything reported it ahead of time.
- ~~**Whether `doctor` should make a real call.**~~ Decided as C6, no.
- ~~**Whether `doctor` overlaps the smoke.**~~ Decided as C8. They answer
  different questions, and the overlap the earlier document assumed was not
  there.

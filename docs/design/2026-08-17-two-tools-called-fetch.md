# Two tools called `fetch`, in folders nobody coordinated

**Status:** planned.
**Date:** 2026-08-17

The sibling of `2026-08-17-skills-from-several-parties.md`, and it was ruled out
there: a tool is *called* by name, a dictionary holds one entry per key, so two
can never coexist. That reasoning was about one dictionary. There is one per
**agent**, and that changes the answer.

Two subagents may each want a `fetch`, from different folders, and neither ever
sees the other's. Today the catalogue refuses both before either agent exists:

```
vendor_b/fetch.py: tool 'fetch' is already defined by vendor_a/fetch.py
```

The deployment does not start. If you own neither file, there is nothing to do.

## What was measured

With the catalogue refusal lifted, on a workspace holding `vendor_a/fetch.py`
and `vendor_b/fetch.py`:

```
catalogue holds : [('fetch', 'vendor_a/fetch.py'), ('fetch', 'vendor_b/fetch.py')]
references      : ['vendor_a/fetch.py::fetch', 'vendor_b/fetch.py::fetch']
```

Both survive with distinct references — the vocabulary already exists. Two
subagents each holding their own `fetch` build and register without complaint,
because `subagent["tools"]` is per-spec and each subagent gets its own tool
dict.

The collapses are all ours, and there are three:

```
tool objects handed to the graph : ['fetch', 'fetch']
sources mapping (keyed by name)  : {'fetch': 'vendor_b/fetch.py'}
tools_by_name collapses to       : ['fetch']  -> "from vendor b"
```

The third is the one that decides the design. **Every subagent is handed the
whole workspace tool set and narrowed afterwards by `ToolAllowlist`**, so both
`fetch`es land in one dict and one wins silently — before the allowlist runs.

Checked and *not* a problem, so nobody goes looking: `ToolAllowlist` filters the
model request as well as the call (`wrap_model_call` → `request.override`), so a
delegate is not shown tools it may not use.

## The decision that shapes it

**An agent holding two tools of one name is rejected.** Not resolved, not
deduplicated — rejected at construction, naming both files and the agent.

That is what makes the rest safe, and it is the same rule skills got one PR
ago, applied where a tool's constraint actually lives.

## Decisions

| # | Decision | Why |
|---|---|---|
| T1 | **The catalogue stops refusing duplicate names.** The refusal moves from load to construction, per agent. | The catalogue is not where the constraint lives. Refusing there stops a deployment over a clash no single agent would ever have seen, and it is unfixable by anyone who does not own both files. |
| T2 | **A reference becomes a selector, not just a checked label.** `vendor_a/fetch.py::fetch` resolves to one `Found`. | `split_reference` today returns the name plain "whichever form was written" and hands the claim off to be *checked* and discarded. That is exactly why a grant cannot currently pick between two — the thing that distinguishes them is thrown away one line after it is validated. No new syntax: this is the vocabulary presets and the README already use. |
| T3 | **The model never sees a reference.** Every agent is given a flat `fetch`. | Tool names are sent to the provider as identifiers, and `::` is not something to put in one. It also keeps the promise made when references were introduced — flat for the model, precise for the definition. Skills could show a qualifier because a skill is read by path; a tool has to be *called*, so the qualifier must resolve before the schema is built. |
| T4 | **Each agent is handed only the tool objects it was granted**, rather than the whole set plus an allowlist. | Without this the rest cannot work: both objects reach one `ToolNode` and collapse before any narrowing happens. `ToolAllowlist` stays — it is what filters the model request and refuses a call — but it stops being the thing that decides *which object* a name means. |
| T5 | **`*` against a catalogue holding a collision is refused**, not silently narrowed to the unambiguous ones. | `tools` defaults to `ALL`, so this is the common path and the tempting place to be clever. Quietly dropping one is the precise failure two PRs of skills work just removed. A deployment that deliberately ships two `fetch`es can name its main-agent tools. |
| T6 | **A bare name that two folders offer is refused, naming both references.** | The safety property, and the same shape as `research::lookup`. Adding a colliding tool turns a working grant into a loud error rather than silently changing which code runs. |

## What changes

| File | Change |
|---|---|
| `infrastructure/tool_store.py` | `found` stops refusing a duplicate name |
| `domain/tool.py` | `Offering` keyed by reference, not name; ambiguity refused; `split_reference`'s claim carried through |
| `infrastructure/agent.py` | resolve grants to objects; refuse an agent holding two of a name; `*` refused against a collision |
| `infrastructure/delegation.py` | a subagent gets its granted objects, not the catalogue |
| `main.py` | `--list` shows both, by reference where a name is ambiguous |
| `tests/test_tool_collisions.py` (new) | both survive, each agent gets its own, and every way of ending up with two is refused |

## The cost, stated

A catalogue with a collision cannot use the default grant. `tools` defaults to
`ALL`, so `Capabilities()` — the bare request — is exactly the case T5 refuses.
Such a deployment must name its main-agent tools explicitly.

That is a real narrowing of the default, and it is the price of not guessing.
Every alternative I can see either picks a winner silently or shows the model a
name that changes when an unrelated folder gains a file.

## Checked before planning

- **The built-in probe is unaffected by T4.** It assembles with an empty tool
  set — `probe = assemble(())` — so the built-in list does not depend on which
  workspace tools an agent is given, and narrowing them cannot change it.
- **`ToolAllowlist` already filters the model request**, not only the call, so
  T4 does not quietly widen what a delegate is shown.
- **`subagent["tools"]` is per-spec**, and deepagents registers those objects
  into that subagent's own dict — which is the whole reason this is buildable
  where the skills merge needed a middleware override.

## Not in scope

**Renaming for the model.** A tool could be presented as `vendor_a__fetch` and
two would coexist inside one agent. Rejected: the model-facing name would then
change when an unrelated folder gains a file, breaking prompts and few-shot
examples at a distance. If the multi-vendor pressure ever demands it, an
explicit per-folder prefix written by a person is the version to build — stable,
opt-in, and nothing changes for anyone who does not ask.

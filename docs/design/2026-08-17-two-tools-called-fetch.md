# Two tools called `fetch`, in folders nobody coordinated

**Status:** implemented.
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
| T5 | **A request's grant is what the run may *draw on*, not what the agent itself carries.** The agent takes everything granted except names more than one file defines; those go to whichever delegate names one. | Written as "refuse `*` on a collision" and rejected during the build, because it turned down the configuration this exists to enable: a delegate is clamped by the request's grant, so two delegates wanting two `fetch`es *requires* the request to hold both. Splitting the two meanings is what makes it work, and the split is only visible in a workspace that has a collision. The dropped pair is announced as `delegate_only` -- quietly holding less than was asked is the failure this codebase refuses everywhere, so the point is that it is said, not that it is dropped. |
| T6 | **A bare name that two folders offer is refused, naming both references.** | The safety property, and the same shape as `research::lookup`. Adding a colliding tool turns a working grant into a loud error rather than silently changing which code runs. |
| T7 | **A subagent definition's `tools:` selects by reference too.** | The syntax already parses — `split_reference` is called at `domain/subagent.py:391` — and then the reference is discarded: `split_reference(entry)[1]` keeps the name alone. So a definition can already *write* `vendor_a/fetch.py::fetch` and cannot yet *mean* it. This is the same one-line-deep change as T2 and the place a delegate's tools are actually chosen, so it is where the feature earns its keep. |
| T8 | **The subtraction axis takes references, and refuses an ambiguous bare name.** | `--without-tools fetch` against two `fetch`es has no safe reading. Removing both is quietly more than was asked; removing one is quietly the wrong one. Refusing matches T6, and subtraction is where a silent over-removal would be hardest to notice — the tool simply is not there. |

## What changes

| File | Change |
|---|---|
| `infrastructure/tool_store.py` | `found` stops refusing a duplicate name |
| `domain/tool.py` | `Offering` keyed by reference, not name; ambiguity refused; `split_reference`'s claim carried through |
| `infrastructure/agent.py` | resolve grants to objects; refuse an agent holding two of a name; `*` refused against a collision |
| `infrastructure/delegation.py` | a subagent gets its granted objects, not the catalogue |
| `domain/subagent.py` | a definition's `tools:` keeps the reference instead of discarding it |
| `main.py` | `--list` shows both, by reference where a name is ambiguous; `--without-*` takes references |
| `tests/test_tool_collisions.py` (new) | both survive, each agent gets its own, and every way of ending up with two is refused |

## Adjacent, and deliberately not here

**Subagents collide the same way and are not fixed by this.** Two folders can
each hold a `profiler.yaml` and `subagent_store` refuses at load, with a
`sources` map keyed by name — the identical shape. It is left out because the
payoff is different: there is one roster per request, so two subagents of one
name can never coexist in an agent the way two tools can in two delegates. A
reference would let a request *choose* which `profiler` to activate, which is
worth doing and is a smaller, separate change.

**Skills subtraction calls an ambiguous name unknown, on main today.**
`--without-skills lookup` against two of them refuses — so it is safe, and it
does not quietly drop both — but with the same sentence a genuinely absent name
gets:

```
cannot exclude unknown name(s): lookup; this workspace offers (…)
```

`SkillRegistry.resolve` distinguishes those two deliberately, because "no such
skill" and "which one did you mean" send a reader to different places. The
subtraction path does not, and the qualified forms are only visible because the
listing beside the message happens to contain them. T8 is the same fix on the
tools axis; this is the skills one, already shipped and worth folding in.

**An upload can take a foldered skill's name, on main today.** `provision`
measures "already defined" against `roots.skills.names`, and that lists the root
and stops — it returns `()` for a catalogue whose skills all live in folders. So
the rule that a request may not stand its own text in for a reviewed skill fires
for a root-level skill and silently stops applying to a foldered one. Not an
override, because sources keep them apart (`uploaded::lookup` is its own
entry) — an inconsistency rather than a hole, and one the skills work
introduced. The fix is to ask the registry rather than the repository, and it
belongs with the skills code, not here.

## The cost, stated

`--tools` grows a meaning it did not have. Everything granted used to be
callable by the agent itself; now a name two files define is delegation-only.
That is one new idea to learn, and it appears only in a workspace that actually
has two tools of one name.

The delegation ceiling is untouched, and that was the constraint the whole
design had to bend around rather than through: a delegate still cannot hold
anything the request was not granted. `test_a_delegate_still_cannot_reach_past_the_request`
drives it rather than inspecting it, for the reason `test_delegation_ceiling`
gives -- what a delegate *registers* is identical either way.

## Found while building

**The plan rejected the configuration it exists to enable.** A delegate is
clamped by the request's grant, so two delegates each wanting a different
`fetch` requires the request to hold both — and T5 as written refused exactly
that. Fixed by splitting "what this run may draw on" from "what this agent
carries", which is the T5 above.

**`SubAgent.tools` adds to the built-ins rather than replacing them.** Measured
before relying on it: a delegate handed one workspace tool keeps all eight file
tools. That is what made T4 a small change instead of also having to re-supply
every built-in per delegate.

**A test that reads a delegate's tool registry proves nothing.** A mutation
leaving the allowlist keyed on the reference rather than the bare name filters
every workspace tool out of the *model request* — the delegate holds its
`fetch` and is never offered it — and every test here still passed.
`test_a_delegate_can_actually_call_the_one_it_named` exists because of it. The
repo's own `test_delegation_ceiling` warns about this in as many words; the
warning was heeded for the negative case and missed for the positive one.

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

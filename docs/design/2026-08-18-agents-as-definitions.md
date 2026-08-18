# The main agent becomes a definition

**Status:** proposed.
**Date:** 2026-08-18

There is no file anywhere that says what the main agent is. It is assembled from
four places that do not know about each other: `prompts/system.md` plus the
workspace's `PROMPT.md`, three switches in the environment, the `default:` line
in `models.yaml`, and whatever a request's `Capabilities` narrows to. Every
delegate in `subagents/` is a reviewable document; the thing that summons them
is not.

This gives it one, `agents/*.yaml`, on the shape `subagents/` already has.

## What this rests on

Numbers already established elsewhere in the repository, reused here rather than
re-measured:

| Fact | Where it was found | What it decides here |
|---|---|---|
| A subagent costs ~4.3 ms to compile | `domain/capabilities.py` | Why `subagents:` omits to none and why `["*"]` is not free |
| The skills index costs ~464 tokens for three | `domain/subagent/reading.py` | Why `skills:` omits to none in both formats |
| Rebuilding an agent costs ~8 ms; the real cost is the prompt cache | `domain/capabilities.py` | Why the agent is fixed for a session rather than per turn |
| The catalogue is read once when the deployment is wired | `infrastructure/catalogue/layered.py` | Why a mid-session edit only lands on a restart, and why snapshotting is cheap |

**Not measured, and worth measuring before building.** How much of a turn's
prompt is `system.md`, and therefore what a per-agent prompt does to the cache
hit rate across a fleet of agents. Whether `render_system_prompt`'s
`lru_cache(maxsize=8)` is still the right size once the prompt varies per agent
rather than per deployment. Neither changes the design; both change whether one
part of it is worth the bytes.

## Decisions

| # | Decision | Why |
|---|---|---|
| A1 | **Agents are their own kind.** `agents/*.yaml`, its own reader, sharing the field readers with `subagents/` but not the field set. | Three fields disagree, and not cosmetically: `distinct` has nothing above it to differ from, the prompt composes rather than replaces, and memory is a switch a delegate has no use for. One folder would make a field's meaning depend on the request that read it rather than on the file. |
| A2 | **The agent's prompt is appended, never replaces.** `system.md`, then `PROMPT.md`, then the file's own text. | `system.md` is the harness describing itself — `/data` is read-only, `/skills` is loadable, memory lives at a path. An agent that replaces it is not leaner, it is one holding tools nobody told it about, discovering its permissions by being denied. |
| A3 | **The field is `system_prompt` in both formats**, despite meaning "the whole prompt" in one and "the last part of it" in the other. | It is the word deepagents uses and the word Anthropic uses. A kingfisher-only coinage is the thing a reader has to look up. |
| A4 | **The agent file is the baseline; `Capabilities` only ever narrows it.** `"*"` on a request means "everything this agent declares". `UNRESTRICTED` collapses into `Capabilities()`. | The narrowing direction is what makes an untrusted caller safe to accept, and it is unchanged. What moves is where the top of the lattice comes from: the agent file, not the workspace. The `subagents` special case in `UNRESTRICTED` existed because there was no file to declare a roster; now there is. |
| A5 | **Naming an agent is required.** No implicit agent, no `KINGFISHER_DEFAULT_AGENT`. | One path through `build_agent` instead of two. A default would put the most consequential choice in a place the call site never mentions — which is the same objection `models.yaml`'s `default:` answers by being one reviewed line, and an agent is not one line. |
| A6 | **The agent is fixed when the session starts, and the resolved agent is snapshotted into it.** Later turns may repeat the name; a mismatch is refused. | Swapping mid-session changes the system prompt under a conversation that already happened, so the history no longer matches the instructions that produced it. Snapshotting gives what Anthropic uses version numbers for, without version numbers: git already says what changed. |
| A7 | **The environment switches stay as the outer ceiling.** Deployment → agent → request, each step only subtracting. | Two of the three are already sayable with fields the file needs anyway: `skills: []` is `skills_enabled: false`, and an agent that does not list `eval` has no interpreter. Only memory needs a switch, because it is one file with no names to list. |
| A8 | **Omission: the tool fields inherit everything available to you; `skills` and `subagents` omit to none.** | One sentence per field, the same in both formats — for a delegate "available to you" is its caller's set, for an agent it is the workspace's. The tools/skills asymmetry is the existing one and keeps its existing reason: tools are what an agent needs to *act*, skills are what it needs to *know*. |
| A9 | **`subagents: ["*"]` is legal in `agents/` and refused in `subagents/`.** | In a subagent file "everything" includes that file, which is always a loop. In an agent file it is not self-referential. Kept as a difference rather than flattened, because flattening it either forbids a coherent statement or permits an incoherent one. |
| A10 | **The agent names `model:` or `alias:`, on subagent rules.** `models.yaml`'s `default:` stays as what an agent with no opinion runs. | Picking an agent is now how you pick a model, at the call site, rather than through a line nothing at the call site mentions. `default:` has to stay regardless: an agent file copied between deployments cannot portably name a vendor's model id, which is why `extractor` says `alias: cheap`. |
| A11 | **A subagent that names no model runs its caller's model**, not the deployment's default. | `reviewer` names none on purpose and the comment says it wants "the deployment's own model" — what it means is "the same model that produced the answer I am checking". Those were the same thing until an agent could pin one. It also makes the two settings read as opposites, which they are: say nothing and you match your caller, say `distinct` and you must not. |
| A12 | **`distinct: true` is checked against the immediate caller.** | Checked against the deployment default, an agent pinned to a model summons a delegate bound to that same model, and the check compares it against something neither of them is running. The flag exists to stop an answer that is worthless while looking fine. |
| A13 | **Model parameters stay in `models.yaml`. The agent names a model and nothing more.** | That file has no credentials in it so it can go through review, and it is meant to be the one place saying where prompts go and what they cost. An agent wanting the same model to think harder gets its own entry. |
| A14 | **Helpers come through the chain**: the agent names `reviewer`, `reviewer` names `second-opinion`, both are wired. Resolved when the catalogue loads, printed by `kingfisher list`. | Enumerating the tree makes the agent file carry names it has no relationship with, and that list goes stale the moment a file it does not own changes its own helpers. Resolving at load is where cycles are already checked, so the tree is known before any request arrives. |
| A15 | **The agent must resolve; anything below it that cannot run is withheld and reported.** | Otherwise a freshly seeded workspace does not start, because `second-opinion`'s `alternate` alias is deliberately unbound in the example config. It is also what happens today when a caller does not activate a nested helper, and `reviewer`'s prompt is already written for it. |
| A16 | **Agents cannot be uploaded. No session layer.** | An uploaded skill or subagent is the caller's own text. An agent decides where every prompt in the session goes and is pinned for the session's life. Tools are in the same position for the same stated reason: a layer added for symmetry advertises a capability that does not exist. |
| A17 | **Fourth kind in `Definitions`**, `KINGFISHER_AGENTS_DIR` to relocate, one `domain/agent.py`. | `domain/subagent/` became a package only when one file had grown into three subjects; copy the outcome when the same thing happens, not before. |
| A18 | **`description` is required**, though nothing reads it at runtime. | The agent name is now the thing a caller must choose. Choosing between three names with nothing but the names means opening three files. Anthropic makes it optional; theirs are created through an API by whoever will use them, and these are files someone else picks from. |
| A19 | **Four refusals, each with its own reason:** `distinct`, `permissions`, `interrupt_on`, `response_format`. | The generic "unknown field" reads as "not supported yet" and sends someone looking for a workaround. `interrupt_on` gets a different reason from the subagent one — an agent has both a checkpointer and a human, and what it lacks is anything in the service that surfaces an interrupt. |
| A20 | **HTTP: `agent` is required on `POST /sessions`**, optional and checked on turns, and the response says what it resolved. | The mistake happens once, where the choice is actually made. Returning the resolution costs nothing, since the agent is being snapshotted at that exact moment, and it is the only way a caller can see what it got without running a turn. |
| A21 | **`seed` writes two agent files** — `assistant`, general and naming the shipped delegates, and `surveyor`, narrow and cheap. | A workspace with no `agents/` cannot run. One file would make the only example the one where everything is switched on, which teaches the format badly, since the point of it is choosing. |
| A22 | **One breaking release, no compatibility mode.** | A transition means `Capabilities` defaults meaning two things at once depending on whether an agent was named — the "absent versus null" ambiguity the current design removed, reintroduced on purpose. |

## The format

```yaml
name: surveyor
description: Reads and profiles data without changing anything.
builtin_tools: [read_file, ls, glob, grep]
tools: [csv_profile::csv_profile]
alias: cheap
memory: false
# Added after system.md and PROMPT.md, not instead of them.
system_prompt: |
  You survey files before anyone trusts them.
```

`name`, `description` and `system_prompt` are required. `builtin_tools` and
`tools` inherit everything available when omitted; `skills` and `subagents` omit
to none. `middleware`, `model`/`alias`, `memory` and `metadata` read exactly as
they do in a subagent file.

## The cost, stated

**Every existing caller breaks.** `run("do a thing")` has nowhere to put a name.
`POST /sessions` needs a body it did not need. A workspace with no `agents/`
folder cannot run at all. All of these can be made loud, and should be: the
message names the agents the workspace has, or says to run `kingfisher seed`.

**One break cannot be loud.** A subagent that names no model changes what it
runs. Nothing goes wrong today, because an agent with no model runs the default
and so does its delegate; the two only come apart once someone pins a model, and
at that point the new behaviour is the one they wanted. Release notes, not a
shim.

**A delegate can go quietly missing.** A16 keeps agents out of a caller's hands
and A15 lets the chain run with a hole in it. The withheld report is the only
thing standing between that and silence, exactly as it is today.

**A per-agent prompt is a per-agent cache prefix.** A deployment running six
agents holds six prefixes rather than one. Stable within a session, which is
what A6 buys, but the fleet-wide hit rate is the thing to measure before
assuming this is free.

## Held back deliberately

**Pinning a helper you reach indirectly**, written `reviewer::second-opinion` on
the shape `tools:` already uses — the short form works, the long one buys a
check that the file below still brings what you think it does. Held until
someone wants the tree fixed in review, because an optional check nobody writes
is a field that only costs.

**`response_format`.** It is refused rather than absent, and the message says
why: it changes what a *run returns*, so `Result`, the service's response body
and streaming all have a stake in it. That is its own piece of work and should
not arrive as a side effect of a key in one format.

**Uploaded agents.** A new `agent_refs` beside the existing two does not change
what an agent file may say, so nothing written now becomes wrong if this comes
back.

**A default agent.** Removed rather than kept, because required naming is what
collapses two build paths into one. It can return as a deployment setting
without changing the format.

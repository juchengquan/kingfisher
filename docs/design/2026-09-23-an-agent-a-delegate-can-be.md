# An agent a delegate can be

**Status:** proposed, nothing built.
**Date:** 2026-09-23.
**Occasion:** whether an agent and a subagent could share one YAML format. The
syntax already nearly does, so that question has little left in it. The question
that does is whether **one file can serve as both**. `decisions.md` named that as
the thing that would reopen *Left for now: one spec for agents and subagents*:
*"one definition file usable both as an agent and as a delegate"*. This is that
change, in the one direction that only narrows access.
**Cited by symbol, not by line**, for the reason the other two documents here give.

## What is already true

The two formats are closer than their two sections in `guides/formats.md` suggest:

| | Agent | Subagent |
|---|---|---|
| Fields declared once on `Definition` | 14 | the same 14 |
| Keys only this format takes | `memory`, `interrupt_on` | none |
| `["*"]` on `skills` and `subagents` | taken | refused |
| `source: bundled` on a `tools` or `skills` entry | refused | taken |

`test_format_parity.py` holds the two readers to each other, and
`test_the_star_is_the_only_place_the_formats_part` pins how they read `["*"]`
differently. The other difference, `source: bundled`, is refused in an agent file by
`kinds/agents/spec.py` and held by `test_an_agent_may_not_list_a_bundled_entry`.
So a *single format* has nothing to buy, and this document does not propose one.

## The case for it, from the shipped catalogue

`assets_examples/agents/surveyor.yaml` and
`assets_examples/subagents/analysis/profiler.yaml` are one definition written twice.
Both prompts open by saying the caller *"is about to compute something from"* a
file *"and does not yet know what is in it"*, and both close with *"You describe the
file; somebody else decides what it means."*

They have already drifted apart:

- `profiler` gained `csv_columns`, a numbered procedure, and the sentence about a
  survey that implies it was exhaustive.
- `surveyor` gained `memory: false`.

Nothing went red, and nothing could. `test_format_parity.py` checks that the
*readers* agree. It cannot notice two files holding the same content.

## The change, stated plainly

**An agent file may write `delegable: true`.** A delegable agent is then also a
delegate:

- any agent's `subagents:` line can name it;
- a request's `capabilities.subagents` can name it;
- it is built as a delegate exactly as a subagent file is.

The file stays in `agents/`, keeps its agent name, and still runs as an agent when a
request names it.

Nothing moves the other way: a subagent file never becomes an agent.
`AgentSpec` and `SubagentSpec` stay two types. The conversion between them is where
this proposal's refusals live, and the `SubagentSpec` it returns is the proof that
they ran.

## Decisions

### 1. Only this direction, because only this direction narrows

As a delegate, a definition is limited by its parent in four ways:

- **Tools:** the parent's grant is the ceiling.
- **Middleware:** an agent's `middlewares:` line is the ceiling for its delegates.
- **Access:** the granting agent's `source_ids` limit who reaches its `subagents:`
  entries.
- **Gates:** a delegate inherits its parent's `interrupt_on`.

Running a delegate *as an agent* removes all four, and two of those removals widen
access with nothing in the file showing it:

- **Tools.** Leaving `tools` or `builtin_tools` out of a subagent file means *the
  parent's*, which has already been narrowed. Leaving them out of an agent file means
  everything the workspace offers, `execute` included.
- **Access.** Unset `source_ids` on a subagent means *everyone who reaches its
  parent*. On an agent it means *anyone may open a session*.

The file an author wrote as a narrow helper would become a wide agent. That is why
the reverse direction is under *Considered and rejected*, and not a later slice.

### 2. Opt-in, in the file, and checked where the file is read

The alternative is that any agent could be named as a delegate, with the delegate
checks running when some other agent's build reaches it. That puts the refusal in
front of the wrong person. `decisions.md` has refused that shape three times (*Two
definitions of one name coexist*): a failure that only the owner of another file can
fix.

With `delegable: true`, the agent's own `parse` runs the delegate checks, so a file is
either a coherent delegate or it is refused, whoever later names it. The key is also
what an author reads as *"my description is now a trigger another model reads"*
(decision 6).

### 3. What `delegable: true` refuses

Raised as `AgentError` at `parse`, each with its reason:

- **`interrupt_on`.** A delegate inherits its parent's gates, and the subagent format
  already refuses this key for that reason (`subagents.spec.REFUSED`). Here it would
  be worse than refused: it would be *accepted*, honoured when the file runs as an
  agent, and silently ignored when it runs as a delegate. The author would believe
  `execute` is gated while it runs. The refusal reuses the subagent format's message
  rather than rewording it.
- **`["*"]` on `skills` and `subagents`.** The reasons are the ones the subagent
  reader already passes as `refuse_all`. On `skills` the star reads as *all* and
  arrives as *none*. On `subagents` the set includes the file itself, which is always
  a loop. The two message strings move to one place, so the two readers cannot word
  them differently.

**Not refused: `memory`.** A delegate is never given a memory file, so `memory: false`
agrees with the delegate role and an unset `memory` means nothing to it. The field
governs the agent role only, and `guides/formats.md` says so. Whether `memory: true`
should be refused, as a promise the delegate role breaks, is open (below).

### 4. One conversion, and one set of delegates

```python
def as_delegate(spec: AgentSpec) -> SubagentSpec:
    """A delegable agent, as the delegate it may also be."""
```

- **The fields.** It copies every field declared on `Definition` and sets `bundled`,
  `carried` and `build` empty. `bundled` is empty already, since an agent file
  refuses `source: bundled`. It lives beside `SubagentSpec`: kind-to-kind imports
  are already allowed, since `kinds/agents/spec` imports `kinds/tools/spec`.
- **A test for the copy.** One test walks `dataclasses.fields(Definition)` and asserts
  each field survives the conversion. Without it, a shared field added later would
  quietly be dropped from every agent used as a delegate. `Definition` existing is
  what makes the conversion mechanical rather than a list someone keeps by hand.
- **Two readers today.** The set of delegates is read in two ways:
  - through `activation.defined_subagents`, by the harness, `service.py` and
    `reporting.py`;
  - through `Definitions.subagents.specs` directly, by `inventory.py` (four reads)
    and `Definitions.warm`.

  A delegable agent that reached only one of those would build but not list, or list
  but not build. That is the disagreement *The skill registry is populated by
  deepagents' own lister* was written to end.
- **One reader instead.** `Definitions` gains `delegates`:
  - `Definitions.subagents.specs`, plus `as_delegate` applied to each delegable agent;
  - every current reader moves to it;
  - an architecture rule refuses `Definitions.subagents.specs` anywhere else.

`refuse_cycles` needs no change. It walks what `defined_subagents` returns, so once
that is `delegates`, a loop through a delegable agent is refused like any other. That
includes an agent that names itself.

### 5. A name is the agent's, in both namespaces

A delegable agent that shares a name with a subagent is **refused at load**. This is
stricter than *two subagents of one name coexist*, and deliberately so:

- **The agent side already refuses at load.** Two agents called `assistant` stop
  startup. `delegable` brings the agent's name into the delegate namespace, and the
  stricter rule comes with it.
- **Bundles are looked up by name.** `Definitions.bundled`, `bundled_tools` and
  `bundled_skills` all key on the name. A delegable `surveyor` next to a subagent
  `surveyor` with a folder would hand the subagent's private tools to the agent.
- **Qualifying the name cannot express it.** A subagent's qualified reference is a
  path relative to `subagents/`, and a path under `agents/` has no spelling in that
  namespace that cannot collide with one.
- **The owner can always fix it:** remove `delegable`, or rename. That is the test
  *coexist and refuse later* was built to pass, and this passes it too.

### 6. Where the roles part, and nothing is enforced

These stay as they are. `guides/formats.md` says them once, under the new key:

- **The prompt is assembled differently.** As an agent: `system.md` + `PROMPT.md` +
  the file's own prompt. As a delegate: the file's own prompt + `PROMPT.md`, with no
  `system.md`. A delegable prompt has to stand without the harness prompt, as every
  subagent's already does. `surveyor` and `profiler` both address *"your caller"*,
  which reads correctly in either role.
- **The description has two readers.** As an agent, nothing reads it at run time; it
  is how a person chooses in `kingfisher list`. As a delegate, it is what the parent
  model reads to decide whether to delegate. Write it as the trigger. The label
  survives that; the reverse is not true.
- **An unset `model` falls back differently.** As an agent it runs the deployment's
  `default:`. As a delegate it runs whatever summoned it. This depends on the use,
  as it already does.

### 7. What `["*"]` means grows

An agent writing `subagents: ["*"]` gets every delegate the workspace offers, and that
set now includes delegable agents. This follows from what opting in means, and no
shipped agent writes the star there today. Listed here because it changes what an
existing line means, and the entry in `decisions.md` should say so.

## Considered and rejected

- **A subagent file that may run as an agent.** It widens access, as decision 1
  shows. An opt-in key would not fix that, because the widening is in what an
  *omitted* field means, and an author opting in would have to re-read every line
  they left out. Write the agent file and make it delegable instead.
- **Any agent may be named as a delegate, no key.** The refusals move to someone
  else's build (decision 2), and every agent's description becomes a trigger without
  its author being asked.
- **One spec for both kinds.** Kept separate. A merged class would take every field
  from both kinds, so an agent carrying a `build` would become constructible. Each
  kind's own rules would turn into checks on a `kind` field set at parse time, which
  is the wrong time here: under this proposal, the role is decided where the file is
  *used*. `test_the_known_set_matches_the_spec_it_builds` would weaken to a subset
  check. If this is ever extended to the reverse direction, the shape is three types
  rather than one: a role-free record of the file, with `AgentSpec` and
  `SubagentSpec` as checked views of it.
- **One `definitions/` folder with a `role:` key.** That would rename a kind across
  every place it appears, the scale of the `middleware` → `middlewares` change. It
  breaks every workspace and forces one rule for name clashes. All of that buys
  appearance, and the one thing it could add is the direction rejected first.
- **Prompt sharing between two files** (a `system_prompt_file:` or an include). It
  fixes `surveyor`/`profiler` and no other case. It also adds a way for a definition
  to reach outside its own file, which the format has never had.

## The order to build in

Each slice is one green commit and a pull request off `main`:

1. **The key and its refusals.**
   - `AgentSpec.delegable: bool = False`, `delegable` in `agents.spec.KNOWN`, and the
     two refusals at `parse`.
   - The `refuse_all` messages move to one place both readers import.
   - `test_format_parity.py` learns the key belongs to the agent format only.
   - Nothing is delegable yet, so no behaviour changes.
2. **`as_delegate` and `Definitions.delegates`.**
   - Every reader moves to `delegates`, with the rule refusing `Definitions.subagents.specs`
     outside it.
   - The field-copy test.
   - A delegable agent builds as a delegate, and a loop through one is refused.
3. **Names and surfaces.**
   - The load-time name refusal.
   - `kingfisher list` and `doctor` show a delegable agent among the delegates, with
     its `agents/` origin.
   - `_subject` names the file a refusal is about, instead of calling it
     `subagent 'surveyor'` when the file is in `agents/`.
4. **The examples and the prose.**
   - `surveyor` becomes delegable and takes back what `profiler` gained.
   - `profiler` is deleted, and `assistant.yaml` and `reviewer.yaml` name `surveyor`.
   - `guides/formats.md` gains the key, and `decisions.md` gains the entry. This
     document goes.

### Guards to mutate

Each should be broken once and seen to go red:

- each `parse` refusal;
- the field-copy test (drop one field from `as_delegate`);
- the single-reader rule (read `Definitions.subagents.specs` from `inventory.py`);
- the name-clash refusal (a delegable agent beside a same-named subagent with a
  bundle, with the refusal removed, should show the stolen private tool);
- the cycle through a delegable agent.

## Open, and deliberately not decided yet

- **The key's spelling.** `delegable` says what it permits. `delegate: true` reads as
  *this agent delegates*, which is the wrong meaning. Decide once, before slice 1.
- **`memory: true` on a delegable agent.** Allowed as written above, because the
  field governs the agent role. Refusing it would say the delegate role cannot keep
  the promise. The deciding question is whether anyone writes `true` today rather
  than leaving the field out: no shipped file does.
- **Whether `kingfisher list` shows a delegable agent twice** (as an agent and as a
  delegate) or once with a mark. This is presentation only, and slice 3 decides it.

## What this does not do

- It does not run a subagent file as an agent.
- It gives agents no `source: bundled`, and no compiled (`build`) or portable
  (`SUBAGENTS`) form.
- It does not merge `AgentSpec` and `SubagentSpec`, or the two readers.
- It does not move any file out of `agents/` or `subagents/`.

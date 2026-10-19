# Writing definitions

The formats a workspace can hold — an agent, a skill, a subagent, a tool —
with every field, what it means, and worked examples you can paste. This page is
the reference for the formats themselves, and it starts with the agent because
that is what a request names; everything else here is something an agent selects
from.

A tool is code rather than a document, so writing one has a page of its own:
[`tools.md`](tools.md), as middleware, the other kind written in Python, has
[`middleware.md`](middleware.md). This page says what a
definition may say *about* a tool — which it grants, and which file's when two
files offer one name.

Kingfisher ships none of these files. Its job is to find, validate and compose
definitions, and it does all three against files it did not write. It ships no
skills of its own for the same reason its base prompt carries no domain
instructions: a general agent should read the same whatever the project is.

Seed from a directory holding `agents/`, `middlewares/`, `tools/`, `skills/` and
`subagents/` — any of them, none required:

    kingfisher seed --from ./my-definitions   # or $KINGFISHER_ASSETS
    kingfisher list                           # what the workspace offers now

Nothing is loaded automatically. A definition does nothing until it is in a
workspace catalogue, and what it says there is yours.


A request then names what it wants:

```python
from kingfisher import Capabilities, Request, run
from kingfisher.plugins import LocalSessions

run(Request(
    task="Review the diff in /inputs/change.patch",
    agent="assistant",
    capabilities=Capabilities(
        tools=("read_file", "ls", "glob", "grep", "execute", "task"),
        skills=("code-review",),
        subagents=("reviewer",),
    ),
), sessions=LocalSessions())
```

`agent` is required; there is no default. Leaving a *capability* unset means
*no opinion* — everything that agent declares, which is not the same as
everything the workspace holds: an agent that names three tools cannot be asked
for a fourth. Passing an empty tuple means *none*. A definition file reads the
other way round: a field it leaves out grants none. Naming something that does
not exist raises `CapabilityError` at build time rather than running with
quietly less than you asked for.

### Putting one subagent on a different model

A definition says where its subagent runs. A request can override that for a
subagent it names — useful when the file is not yours to edit, or when the
model it pins is not one your credentials reach:

```python
from kingfisher import Request, RunOn

run(Request(
    task="Check these figures",
    agent="assistant",
    capabilities=Capabilities(subagents=("reviewer", "second-opinion"), models=("MiniMax-M2.5",)),
    run_on={"second-opinion": RunOn("MiniMax-M2.5")},
), sessions=LocalSessions())
```

**It is off until a deployment grants it, and granted per model name.** Every
other field here only ever takes something away, which is what makes an
untrusted caller safe to accept. Naming a model is the one thing that *chooses*,
and models differ in price by more than an order of magnitude, so `models` is
`None` by default.

Name a model, and its entry in `models.yaml` says where it runs. The endpoint it
resolves to keeps its own permission: choosing a model is not an exemption from
where a request's prompts may go.

---

## Agents — `/agents/<name>.yaml`, at any depth

The agent is what a request runs, and everything else on this page is something
an agent selects from: the tools it holds, the skills it may read, the subagents
it may consult, the model it runs on.

```yaml
name: surveyor
description: Reads and profiles data without changing anything.
builtin_tools: [read_file, ls, glob, grep]
tools: [csv_profile::csv_profile]
memory: false
system_prompt: |
  You survey files before anyone trusts them.

  Report what would change how somebody analyses this file, and say what you
  did not check. A survey that implies it was exhaustive is worse than one that
  names its own edges.
```

That is a whole definition: three required fields and whatever else you have an
opinion about.

Folders work here for the reason they work everywhere else on this page —
kingfisher reads these files, so nothing outside it has an opinion about the
layout. `agents/support/triage.yaml` is still `triage`, because `name:` is the
identity and the path is not.

**A request must name one.** There is no default agent; a request without
`agent` is refused, and the message lists what your workspace has. The agent
decides where every prompt in the session goes and what it costs.

**A session keeps the agent it started with.** It is resolved when the session
opens and stored beside it, so editing the file mid-conversation does not change
the instructions under a history that already happened. A later turn may name the
same agent again; naming a different one is refused.

### The prompt is added to, not replaced

`system_prompt` is the same word a subagent file uses, doing a different job. A
subagent's is the whole of its prompt but for the deployment's instructions ahead of
it. An agent's is the last of three parts:

    prompts/system.md    what the harness is — /inputs is read-only, /skills is
                         loadable, where memory lives. Ships with kingfisher
    INSTRUCTIONS.md      what this deployment asks of every agent. Yours, optional,
                         beside the definitions, and it reaches your subagents too
    system_prompt        what this agent is. Yours, and required

The middle part can come from code instead: `instructions=` on `Kingfisher` takes any
object with `text(cfg) -> str`, asked once per agent built, so a change reaches the
next turn.

There is no way to replace the first. An agent without it is not leaner — it is
one holding tools nobody told it about, discovering its permissions by being
denied. Opening a session returns what was assembled, which is where to check.

The third part is required: the two documents above it are written once for the
whole deployment and neither has heard of this agent. One line is enough — say
what this agent is and what it is careful about, and leave out anything already
true of every agent in the workspace.

### The fields

| Field | | |
| --- | --- | --- |
| `name` | required | What a request names it by. Authoritative — the filename is not |
| `description` | required | Single line. Nothing reads it at run time; it is how somebody chooses between your agents in `kingfisher list` |
| `system_prompt` | required | This agent's own instruction, added after the two documents above. Write it as a literal block — `system_prompt: \|` — so your line breaks survive |
| `builtin_tools` | optional | deepagents' own set, listed in the tools table below. `["*"]` is all of them. Unset grants **none**, as `[]` does |
| `tools` | optional | The tools *your* workspace defines. `["*"]` is all of them. Unset grants **none**, as `[]` does |
| `skills` | optional | Which procedures it is told about. Unset grants **none**; write `["*"]` for every skill the workspace offers. `builtin_tools` has to hold `read_file`, the tool a skill is opened with |
| `subagents` | optional | Subagents it may consult. Unset grants **none**; `["*"]` is every subagent the workspace offers. `builtin_tools` has to hold `task`, the tool one is called through |
| `middlewares` | optional | Names entries from a registry the deployment supplies — [`middleware.md`](middleware.md) is who supplies it. The one field that selects *code*, so it is granted, never inherited |
| `model` | optional | One entry in your `models.yaml`. Unset runs the `default:` there. A list is refused |
| `memory` | optional | `false` to run without the memory file on a deployment that wired one |
| `interrupt_on` | optional | Tools whose every call stops for a person before it runs — see [Approval gates](#approval-gates) |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |
| `source_ids` | optional | Who may open a session on this agent. Unset means everyone. Also the default audience, and the ceiling, for its `skills` entries, the one entry that may say who it is for — see [Access](#access--source_ids-in-the-definitions-source_idsyaml-for-the-vocabulary) |

One rule covers every field that grants, and a subagent file follows it too:
**leave a field out and you get none of it; write `["*"]` for everything
available to you.** A forgotten line fails closed: the agent visibly cannot act
on its first turn, rather than quietly holding the shell and every tool somebody
adds to the workspace later. So an agent meant to hold every tool says so on
both lines:

```yaml
builtin_tools: ["*"]
tools: ["*"]
```

`kingfisher list` prints what each agent holds under its name, so finding who can use
`http_fetch` means reading one listing rather than every agent file:

```
analyst — Answers questions from the data, with the database behind a source id.
    tools: csv_profile, line_count, sql_query
    builtin tools: glob, grep, ls, read_file, task
    subagents: auditor, quarters
```

A tool is spelt the way the workspace tools section spells it, however the file
wrote it, so `csv_profile::csv_profile` reads `csv_profile`. A field written `["*"]`
shows the star and what it covers today, `tools: * — today csv_profile, http_fetch,
line_count, sql_query`, because tomorrow it also covers whatever is added. A field
left out prints no line.

Each subagent gets the same two tool lines under its own name, with its own tools
spelt `counter::count`. A subagent's star reads `* — whatever the calling agent
holds` and is not expanded: a subagent is never handed more than the agent calling it,
and that differs from one agent to the next. A compiled subagent prints no `builtin
tools` line, because it is handed none. With `--as`, every line holds only what that
caller gets: a tool whose own lock shuts them out is dropped, and so is a subagent's
tool no agent they reach holds.

`--json` carries each line as `{"every": true|false, "names": [...]}`, under
`agent_tools`, `agent_builtin_tools`, `agent_subagents`, `subagent_tools` and
`subagent_builtin_tools`. For an agent's star, `names` is what it covers today. For a
subagent's, it is only the subagent's own tools, which it holds whoever calls it, and
it is empty when the subagent has none.

### An agent grants the helpers its subagents may consult

An agent activates the subagents it names and no others. A subagent may name
helpers of its own — [below](#a-subagent-that-consults-another) — and one of
those reaches a run only where the agent names it as well: `reviewer` consulting
`profiler` needs both on the agent's `subagents:` line, and an agent naming only
`reviewer` gets a reviewer that cannot delegate.

That is the rule `middlewares:` already follows, and for the reason given there:
an agent's line is the ceiling everything beneath it is clamped by, so a file
that grants one thing never quietly grants another. `kingfisher list` prints what
each agent activates, which is that list rather than the chain below it.

**The agent itself has to work.** A model your catalogue does not define
refuses. **Anything below it that cannot run is left out and reported** — which
is what lets a freshly seeded deployment run at all when one subagent names a
model you have not set up.

### Approval gates

`assets/agents/operator.yaml` is this section as a working file, and is
worth reading beside `analyst.yaml`: one is safe because it was never granted
the shell, the other because a person says yes before each command.

`interrupt_on` names tools whose every call stops and waits for whoever is
calling:

```yaml
interrupt_on: [execute, deploy]
```

A turn that reaches one of them ends with `stop_reason: awaiting_decision` and
hands back the calls on `RunResult.pending`. Answer it with a `Resume` carrying
one `Decision` per pending call — `approve` runs it, `reject` does not, and
`respond` replaces its result with text of your own. The session waits until you
do; a plain request sent to it instead runs, and says so with a
`decision_discarded` event.

From a terminal that is `kingfisher decide`:

    kingfisher run --agent operator "clean up the build"
    # stopped: awaiting_decision -- waiting for a decision on 1 call(s):
    #   4f2a91c0#0  execute  {'command': 'rm -rf build'}
    #       takes: approve, reject, respond
    # answer with: kingfisher decide --session <id> --approve 4f2a91c0#0
    kingfisher decide --session <id> --approve 4f2a91c0#0

`--reject` and `--respond CALL_ID=TEXT` are the other two. Every pending call has
to be answered in one go: the turn resumes mid-superstep, and a half-answered
gate would run the approved call and leave the other hanging. So the line a run
prints answers every call, each by the first decision it takes. `kingfisher decide
--session ID` with no decision prints what is waiting and changes nothing, which
is how to get the ids back if you have lost the output of the run that paused.

**Whole tools, and there is no narrower rule.** Gating `execute` gates every
shell command the agent writes, `ls` included. That is what an approval gate on
an unrestricted shell costs: a pattern matched against the command cannot be
made to hold — `rm -rf` and `x=rm; $x -rf` are the same command — and a gate you
can phrase around is worse than none, because it reads as protection. If that is
too coarse for an agent, what you want is an agent that was never granted
`execute`.

Two ways to get it wrong are refused rather than discovered later. A name no
tool answers to is a typo, and honouring it would leave you believing a call is
gated while it runs. Gates on a deployment with
`KINGFISHER_CONVERSATION_ENABLED=false` cannot pause at all — there is no
checkpointer to hold the paused turn — and would be skipped in silence.

A gate on a tool *this* caller was not granted is neither of those, and is
allowed: the grant already narrows per request, so there is simply nothing for
the gate to fire on.

**A gate names the tool a call carries**, which is the flat name the model sees,
and never a `where::what` reference — that spelling is refused, with the name to
write instead. So where two files each define a `fetch`, the gate is `fetch`, and
it stops a call on either of them: a call carries the name alone, so nothing
downstream can tell the two apart. It is the one place a gate is wider than the
line that wrote it.

Subagents inherit their parent's gates and cannot declare their own — the field
is refused in a subagent file. The caller answering a pause is the agent's
caller, and a subagent that could widen or narrow what stops for them would be
deciding that on their behalf. A subagent's own bundled tool is gated like any
other, by its name, which is how a tool that exists in no other graph can be
stopped at all.

**A compiled subagent's own tools cannot be.** deepagents runs a graph it was
handed as it is and gives it none of this agent's gates, and kingfisher adds no
middleware to one either. A gate on a name only one of those folders holds is
refused, with that reason rather than as a name nothing answers to. A gate on a
catalogue tool such a subagent was *also* handed stops the agent's own calls and
not that subagent's — worth knowing before you grant a compiled subagent a tool
you gate.

### Two fields are refused

Each with its own message rather than a generic "unknown field", because the
generic one reads as *not supported yet* and sends you looking for a workaround:

- **`permissions`** — deepagents' permissions *replace* the parent's rather than
  narrowing them, so writing this here would drop `/inputs` being read-only.
- **`response_format`** — it changes what a run *returns*, so the result and
  streaming both have a stake in it.

---

### Two agents with the same name

Refused, and this is the one kind that refuses. Two files claiming `assistant`
means a request for `assistant` gets whichever the walk reached last, with
nothing anywhere saying which:

```
two agents are called 'assistant' -- assistant.yaml and team/assistant.yaml. A
request names one agent and there is nothing to tell them apart, so rename one
of them
```

Tools, subagents and skills keep both and qualify them, because a *selection*
can be spelled `where::what` and a caller who meant one can say so. An agent is
not selected from a set — a request names one, before there is anything to
narrow — so there is nowhere to put the qualifier and nothing to fall back on.

**`.yml` is refused too**, for agents and subagents alike. It is valid YAML
everywhere else, so a file named that way is a definition someone wrote and
kingfisher silently would not read:

```
reviewer.yml: kingfisher reads '.yaml' here, so this file is not loaded --
rename it to reviewer.yaml
```

---

## Tools — `/tools/<module>.py`, at any depth

Two kinds. The **built-in set** comes with the agent and you select from it by
name. **Workspace tools** are Python you write, imported from the `tools/`
directory in `KINGFISHER_DEFINITIONS_DIR` and added to that set.

### The built-in set

As of deepagents 0.7.6:

| Tool | What it does |
| --- | --- |
| `read_file` | Read a file, optionally a line range |
| `write_file` | Create or overwrite a file |
| `edit_file` | Replace an exact string in an existing file |
| `delete` | Delete a file |
| `ls` | List a directory |
| `glob` | Find files by pattern |
| `grep` | Search file contents |
| `execute` | Run a shell command |
| `task` | Delegate to a subagent |
| `write_todos` | Track a multi-step plan |

Four things worth knowing before you restrict this list:

- **`read_file` is close to mandatory.** The filesystem tooling assumes it. An
  allowlist without it produces an agent that cannot see its own workspace.
  It is also how a skill is opened: the model sees an index and reads the
  skill it picks. A definition whose `skills:` names any while its
  `builtin_tools` does not hold `read_file` — a list without it, or no line at
  all — is refused when the catalogue loads, and a request narrowing it away is
  told of no skills, neither the index nor the prompt's paragraphs on them.
- **Omitting `task` disables subagents** however many you activate — `task` is
  how delegation happens. A definition whose `subagents:` names any while its
  `builtin_tools` does not hold `task` — a list without it, or no line at all —
  is refused when the catalogue loads.
- **`execute` is the one that matters for isolation.** It bypasses the
  filesystem permission layer entirely, so a request that activates the shell
  can reach anything the process can, including skills it did not activate. Deny
  rules are a real boundary only for requests without it.
- **The prompt follows the list.** An agent is not told about a tool it does not
  hold: without `execute` it hears nothing of the shell, without `write_todos`
  nothing of the checklist, and the file tools' section names only the file tools
  granted, or is left out when there are none. Single sentences follow it too: how
  to see a saved result again names only `read_file`, `grep` and `glob` where held,
  `glob` only beside `read_file`, and an agent holding none is told to call the tool
  again. With memory on, it is told to save with `edit_file`, or `write_file` where
  it lacks that; holding neither, as `operator.yaml` does, it is told its memory can
  be read but not added to. The shell does not count as a way to save. The tools'
  own descriptions follow it as well: `execute`'s advice to search with `grep` and
  `glob` and read with `read_file`, `grep`'s pointer to the shell, and `ls`'s and
  `write_file`'s to `read_file` and `edit_file` name only the tools held.

### Workspace tools

Python you write, imported from the `tools/` directory in the definitions folder
and added to the built-in set. A module declares `TOOLS`, the list it contributes, and a tool
is a LangChain tool — from `@tool`, from a `BaseTool` subclass you instantiate,
or a plain function. A `ToolSpec` holding any of them may also say who may use it,
or give it another name.

Writing one is [`guides/tools.md`](tools.md): the three shapes and `ToolSpec`, what a
tool may return, folders and packages, and every refusal the loader has. What a
*definition* says about one is here.

### Two tools with the same name

Two files may each define a `fetch` and both load, which
[`guides/tools.md`](tools.md#two-files-may-define-one-name) covers from the
loader's side. What a definition does about it is here.

A **bare name is refused** once two files offer it, because an agent dispatches
by name and would otherwise run whichever the writer did not mean:

```
this request names tool 'fetch', which more than one source offers -- naming it
alone would silently pick one: write vendor_a/fetch.py::fetch,
vendor_b/fetch.py::fetch
```

The reference is the same `file::name` a subagent's `tools:` uses.

**A request's grant says what the run may draw on, not what the agent carries.**
The agent takes everything granted except names more than one file defines, and
each such pair goes to whichever subagent names one:

```yaml
# subagents/agent_a.yaml
tools: [vendor_a/fetch.py::fetch]
```

Both subagents then have a `fetch`, and each calls its own vendor's. The model
sees a flat `fetch` in every agent and never sees a reference — a tool name goes
to the provider as an identifier, so the qualifier resolves before any schema is
built. An `interrupt_on` gate is on that flat name for the same reason, and stops
a call on either tool; see [Approval gates](#approval-gates).

The agent holding the grant is told what it could not take:

```
[subagent_only] 1 tool name(s) more than one file defines, so this agent holds
none of them -- a subagent that names one gets it: fetch
```

**A subagent still cannot exceed what the request was granted.** That ceiling is
unchanged: a request that withheld `execute` cannot have it handed back by a
definition that asks.

None of this appears in a workspace whose tool names are unique, which is most
of them. Renaming one of the two files is still the better answer for anyone who
controls both — this is for when nobody does.

---

## Subagents — `/subagents/<name>.yaml`, at any depth

A YAML document. Everything the subagent is, in one file.

Folders work here too: `subagents/analysis/profiler.yaml` is activated as
`profiler`, because `name:` is the identity and the path is not. There are no
packages — a definition is a document, not code, so a folder is only ever
organisation.

#### Saying where a tool lives

A `tools:` entry may carry the file it comes from, written `where::what`:

```yaml
tools:
  - csv_profile::csv_columns       # from the package tools/csv_profile/
  - sql_query.py::sql_tables       # a tool whose name is not its file's
  - http_fetch                     # the short form, still fine
```

Both spellings mean the same tool. The long one buys a **check**: if
`csv_columns` moves out of `csv_profile/`, the definition says so at startup
rather than being quietly wrong about a file nobody can find. The short one asks
for nothing and is never wrong.

Write the left-hand side exactly as `--list` prints it — `csv_profile`, not
`csv_profile/`. The `.py` is what tells you a file from a folder, so a package
needs no trailing slash and a pasted one is ignored.

It is a claim about *location*, never a choice between tools. Two tools cannot
share a name — the loader refuses the pair, because the agent dispatches by name
and one would silently replace the other — so there is never a second candidate
for a path to pick out.

**Built-ins take no path.** They have no file, and they live on the separate
`builtin_tools:` axis.

**Requests do not take one either.** `capabilities.tools` names tools
plainly: a request arrives from a caller who has no idea what your folders
look like, so asking them for a path would make your layout part of your API.

```yaml
name: reviewer
description: Re-checks numeric claims against the files they came from. Use before reporting figures you computed once.
builtin_tools: [read_file, glob, grep]
tools: ["*"]
system_prompt: |
  You verify claims. You do not improve prose or add analysis — another
  agent has already done that work and you are the check on it.

  Recompute each figure yourself, from the file. Report the number you got,
  the caller's number, and whether they match.
```

That is a whole definition: three required fields, two optional, nothing else
needed.

### Two tool lists, not one

`builtin_tools` is the set that comes with deepagents — the table further down
lists them. `tools` is whatever *your* workspace defines in `tools/`.

They are separate because they are granted separately: on one list, a subagent
could not ask for a workspace tool without giving up every built-in.

```yaml
builtin_tools: [read_file, glob]   # named, so only these two
tools: [http_fetch]                # and this one, costing no built-in
```

**`["*"]` means all of it. Leaving a list out means none, and so does an empty
one.** The two say the same thing to kingfisher and different things to a
reader: the shipped `extractor` writes `tools: []`, so "read-only" is visibly
meant rather than a line somebody forgot.

```yaml
builtin_tools: ["*"]   # every built-in; leave the line out for none
```

A list, because every selection here is a list. Bare `"*"` is refused by name —
a *request* spells it that way, and one spelling in one place beats two
spellings everywhere. Mixing is refused too: `["*", read_file]` cannot mean
both things at once.

Sending a subagent somewhere cheaper — or somewhere else entirely — is one
more line:

```yaml
model: gpt-5            # an entry in your models.yaml
```

Omit it and the subagent runs whatever *summoned* it — the main agent, or the
subagent that called this one. That is the usual case, and `reviewer` is the
shipped example of it: re-checking a figure is work for the same model that
produced it, whichever one that turned out to be.

A subagent that names a model and ends up on the caller's anyway is reported in
the run — `indistinct` names it, and the two crude cases it can see are the same
model as the deployment's default and a different id on the same host. Reported,
never refused: kingfisher cannot know that a subagent *needs* to differ, and
`reviewer` deliberately runs on the same model and is right to.

An endpoint is a property of the model — `models.yaml` says which one serves
`gpt-5` — so there is no second line to keep in step.

This is the only place it is said. There is no environment variable for it: one
could only say "every subagent", which is the wrong size for the decision.

**The name has to be one your catalogue defines.** The table in `models.yaml` is
closed, and a definition naming a model outside it is refused when the agent is
built, rather than reaching an endpoint that has never heard of it:

```
subagent 'second-opinion': no model 'gpt-5'; this deployment can run ('MiniMax-M3',)
```

It fails only for the subagent you *activated* — a subagent is wired only when a
request names it, so seeding one you cannot run costs nothing until you ask for
it. And `run_on` can rescue it without editing the file, which is why this is
not checked across the whole catalogue up front: the refusal would fire before
the override could apply.

Which is why **a definition somebody else wrote should not carry a `model:` line
at all.** A file you install cannot portably name a vendor's model id, and one
that does refuses to start for anyone without that entry. Every shipped
definition names nothing and says in a comment what to pin it to.

`model:` takes one name, never a list: a model this deployment cannot run refuses
on the spot, so every entry after the first would be unreachable.

| Field | | |
| --- | --- | --- |
| `name` | required | What a request activates it by. Authoritative — the filename is not |
| `description` | required | Single line. This is what the parent agent sees when deciding whether to delegate, so write it as a trigger, not a title |
| `system_prompt` | required | The subagent's whole instruction, written after `\|` |
| `builtin_tools` | optional | deepagents' own set, listed in the tools table above. `["*"]` is all of them. Unset grants **none**, as `[]` does. `eval` is refused here: the code sandbox is the agent's alone, and no subagent is handed it |
| `tools` | optional | The tools *your* workspace defines. `["*"]` is all of them. Unset grants **none**, as `[]` does. An entry written `{name: x, source: bundled}` comes from this subagent's own folder instead — see [its own folder](#tools-and-skills-of-its-own--subagentsname) |
| `skills` | optional | Which procedures it is told about. Unset grants **none**, because its body is already its procedure. Name them: `["*"]` is refused here, where an agent takes it. `source: bundled` works here too. `builtin_tools` has to hold `read_file`, the tool a skill is opened with |
| `middlewares` | optional | Names entries from a registry the deployment supplies — [`middleware.md`](middleware.md) is who supplies it. The one field that selects *code*, so it is granted, never inherited |
| `subagents` | optional | Subagents this one may consult mid-job. Unset grants **none**. One level — see below |
| `model` | optional | One entry in your `models.yaml`. The endpoint follows from it; this is where cost routing goes. Omitted, the subagent runs whatever summoned it |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |
| `source_ids` | optional | Who may reach this subagent, wherever it is used, and the only place that is said: a `subagents:` entry naming it takes no audience. Unset means everyone. Also the default audience, and the ceiling, for its `skills` entries — see [Access](#access--source_ids-in-the-definitions-source_idsyaml-for-the-vocabulary) |
| `datazone` | optional | The one datazone a caller must be in to reach this subagent. Unset means any — see [Datazones](#datazones--a-second-field-beside-source-ids) |

### A subagent that consults another

A subagent can hit a question of a different kind mid-job — `reviewer` doubting
one figure, when checking figures is not what it is for. It can ask for help:

One more line in `reviewer.yaml`, and `task` on its `builtin_tools`:

```yaml
builtin_tools: [read_file, glob, grep, task]
subagents: [second-opinion]
```

`task` is the tool a helper is called through, and naming a helper does not grant
it, so a definition naming one without `task` is refused when the catalogue loads —
whether its list leaves `task` out or it has no `builtin_tools` line at all, which
grants no built-in.

And the subagent it names, which is an ordinary definition — the only thing
that makes it a helper is being named above:

```yaml
name: second-opinion
description: Re-answers a question on a different model, to catch what one model's habits hide.
builtin_tools: [read_file, ls, glob, grep]
tools: []
model: a-different-one
system_prompt: |
  You answer the question you are given, from the files, on your own.

  You are here because a different model already answered it, and the point of
  you is to be a different model. So do not ask what the earlier answer was,
  and do not look for it — knowing it is the one thing that would make you
  agree with it.

  Give your answer and the two or three facts it rests on. Nothing else.
```

**The caller has to name both.** Asking for `reviewer` does not quietly bring
`second-opinion` along. That is deliberate: `second-opinion` runs on another
company's servers, so a caller who declined it usually declined *that*, and a
helper arriving anyway would make the list they wrote untrue.

**A caller who names only `reviewer` still gets a reviewer.** It runs without
the helper and the run reports `subagent: second-opinion` as withheld. Refusing
instead would mean nobody can use `reviewer` without also accepting OpenAI,
which is how a shared catalogue turns into three private forks.

So write the prompt to work both ways — *"if you can get a second opinion on a
contested figure, do; if not, flag it"* — because the caller decides, not the
file.

**Any depth, no loops.** A helper may name helpers of its own, and so may
those. What a catalogue may not do is come back to where it started — and that
is checked over the whole catalogue, not only the subagents one agent reaches,
because a set of files is either coherent or it is not. `kingfisher list` reports
it, and building any agent with subagents refuses it:

```
subagents reach themselves: reviewer -> second-opinion -> reviewer. Delegation
nests to any depth, so a loop would build without end -- one of these has to
stop naming the next
```

The message names the whole loop rather than one edge of it, because one edge
does not say which link to cut and whoever reads it may own none of the files.

A definition may appear in several places — two subagents may both consult the
same `checker` — and reaching one twice is not a loop.

**What it costs.** Every level is a real conversation with a real model. A
helper's tokens are on your bill and in the run events, attributed to it by name,
and its work streams into the terminal under `[second-opinion]`.

Three reasons to reach for one, one example each:

- **Independence.** A second agent that recomputes a claim without seeing how
  the first one got there catches errors that re-reading your own work does
  not. The `reviewer` above is this one.
- **Context isolation.** One that reads a large pile of files and returns a
  short answer, so the bulk stays in its context rather than yours. Give it a
  narrow `builtin_tools` and a cheap `model:`.
- **A different model.** Two models from one family share failure modes, so a
  second opinion is worth nothing until you give it a `model:` that is genuinely
  different. Where that model runs follows from the entry in `models.yaml`.

### Writing the prompt

`|` means *keep my line breaks*. That is the whole rule.

```yaml
system_prompt: |            # ✅ the ordinary case
  You verify claims.
  Be terse.

system_prompt: |            # ✅ indentation inside the prompt is kept
  1. Recompute.
     Do not reuse their script.
```

The two spaces holding the block in place are not part of your text; anything
indented past them is.

`>` is refused, because it joins consecutive lines. These two numbered steps
would reach the subagent as one run-on line, in a file that loads without
complaint and looks correct on screen:

```yaml
system_prompt: >            # ❌ refused
  1. Recompute.
  2. Say which definition you applied.
```

So is a prompt with no marker at all, or one in quotes — the same damage with
nothing to notice. `|-` and `|+` are fine: they differ from `|` only in the
blank line at the very end.

`>` stays welcome everywhere else. A `description` *is* one paragraph, and `>-`
is how anyone writes one longer than a line.

Two shapes that do not load, both loudly:

```yaml
system_prompt: |            # ❌ nothing indented under it, so nothing is in the block
You verify claims.

system_prompt: |            # ❌ present but empty
```

There is one case for the rarely-needed `|2`, where the number fixes the left
edge instead of letting YAML infer it from the first line:

```yaml
system_prompt: |2           # ✅ first line indented deeper than the rest
      ls -la /inputs        #    plain `|` cannot read this, and says so
  Then report.
```

### Your own keys

Everything above is a field this format defines. `metadata:` is the one place a
definition can say something kingfisher has no opinion about:

```yaml
metadata:
  tier: gold
  owner: platform-team
```

It must be a mapping — a bag with no shape cannot be looked up by key, and
looking up a key is the only thing anyone does with it. Kingfisher carries it
and reads nothing.

**Nothing in a run reads it.** It is for whatever loads the catalogue — a
deployment script choosing which definitions to install, an ownership report, a
check that every subagent names a team:

```python
from kingfisher.core.definitions.subagents.catalogue import LocalSubagentRepository

for spec in LocalSubagentRepository(cfg.catalogue_roots["subagents"]).specs.values():
    print(spec.name, spec.metadata.get("owner", "unowned"))
```

Handing it to the agent would mean picking a consumer, and the field is easy to
add one to later; a changed constructor is not easy to take back.

### Lists, and fields that are not here

`tools`, `skills` and `middlewares` take either form:

```yaml
tools: [read_file, grep]
tools:
  - read_file
  - grep
```

**A field not in the table above is an error, not a field that gets ignored.**
Ignoring one is indistinguishable from honouring it: `tolls: [sql_query]` leaves
`tools` unset, and the subagent would hold no tool with nothing saying why. A near
miss is named — *did you mean 'tools'?* — and the fields deepagents knows but this
format declines each say why, since "unknown field" reads as an omission worth
working around.

Skills take the opposite rule, deliberately: kingfisher does not own that
format, so an unrecognised key there is left alone.

---

### Two subagents with the same name

Kept, both of them, and qualified — the opposite of the agent rule above and for
the reason given there: a subagent *is* selected from a set, so a caller who
means one can say which.

```
subagents/surveyor.yaml            name: surveyor
subagents/team/surveyor.yaml       name: surveyor
```

`kingfisher list` then shows them as `surveyor.yaml::surveyor` and
`team/surveyor.yaml::surveyor`, and that is what a grant writes. Where a name is
its own — which is every catalogue with no clash — the bare name is the key and
nothing changes.

A definition naming a subagent by a name that is no key — one nobody defines,
or the bare name two files share — still loads. A run reaching that name refuses
it, and `kingfisher list` and `doctor` name who wrote it and fail.

The refusal sits where the constraint lives: an agent's roster is keyed by name,
so an *agent* granted two of a name is refused. Two definitions in one catalogue
that no single agent holds together are not a conflict.

The filename is not authoritative for any of this. A subagent is named by its
`name:` field, so `analysis/profiler.yaml` is activated as `profiler`; the path
only appears when two files need telling apart.

### A subagent that builds itself — `/subagents/<module>.py`

A subagent can be a Python module instead of a document. It exports `SUBAGENTS`,
a list of mappings, and each one hands over a graph it built:

```python
def _build(model, tools):
    from langchain.agents import create_agent   # deferred — see below
    from langgraph.graph import START, MessagesState, StateGraph

    builder = StateGraph(MessagesState)
    builder.add_node("answer", create_agent(model, tools))
    builder.add_node("record", record)
    builder.add_edge(START, "answer")
    builder.add_edge("answer", "record")
    return builder.compile()


SUBAGENTS = [
    {
        "name": "show-your-work",
        "description": "Answers, then reports exactly which tools it ran.",
        "tools": ["*"],                  # left out, the graph is handed none
        "build": _build,
    }
]
```

`build` is called with the model this subagent resolved to and the tool objects
the request actually granted, and must return something runnable. `kingfisher
seed` ships `show_your_work.py` as a worked example: it answers, then appends a
record of the tool calls that actually happened — computed from the transcript,
not asked of the model.

**Whatever your last node emits is what the caller gets, and only that.**
deepagents returns a subagent's result by walking back to the last `AIMessage`
with non-empty text. So a node that appends a footer as its own message does not
add to the answer — it *replaces* it, and the reply is lost. Emit one message
carrying both.

**Reach for this when a definition cannot say what you mean.** The example above
guarantees an ordering: no edge from the start to the model bypasses the survey
node. A prompt can *ask* for that and a model may skip it, most often on the
input where skipping costs most. "The same subagent, in Python" is not a reason —
a document is reviewable by people who do not read Python, and it gets the fields
this format has to refuse below.

**Five fields are refused, each because it would do nothing.**

| field | why not |
|---|---|
| `system_prompt` | the graph brings its own; write the prompt where the graph is built |
| `builtin_tools` | deepagents' own tools do not exist as objects when a subagent is assembled |
| `skills` | deepagents mounts skills for a subagent *it* builds, never for a compiled one |
| `middlewares` | middleware wraps a graph deepagents builds; this one is already built |
| `subagents` | delegation arrives through middleware, which a compiled graph is not given |

`name`, `description`, `build`, `tools`, `model`, `metadata` and `source_ids` are
what remain.

**A compiled subagent can own a bundle, and gets half of one.** Put
`surveyor.py` in `subagents/surveyor/`, list its tools in `"tools"` with
`"source": "bundled"`, and they reach the graph like any other bundle's —
`build` is handed one list, its own tools first. A `skills/` folder beside it is
refused: it cannot be listed, for the reason the table above gives — an index
arrives through middleware and a compiled graph is given none — and an unlisted
file in a subagent's folder stops the catalogue loading. Keep a compiled
subagent's procedure inside the graph.

A *package* named after the definition — `subagents/surveyor/__init__.py` — is
not a bundle. That folder is the package, and what is under it is importable as
`surveyor.tools`; the `__init__.py` decides what it exports.

**A tool grant is not a limit here.** deepagents runs the graph as given and
never applies kingfisher's allowlist to it, so `tools:` narrows what `build`
*receives* and nothing stops the graph calling something it closed over.
`kingfisher list` marks these `[compiled]` and says so underneath.

**What `build` receives is wrapped, and that is the only guard a compiled graph
gets.** Those tools take the same virtual paths as everywhere else —
`/inputs/<name>` — resolved before the tool runs; a host path in any other
argument is refused, and a tool that raises comes back as a failed result rather
than ending the run. It rides on the tool objects because there is no middleware
in front of a graph kingfisher did not build. A tool the graph closed over
instead of taking from that list gets none of it, and is handed the path the
model wrote.

**Defer the heavy imports into `build`.** Every `.py` file under `subagents/` is
imported whenever the catalogue is read — `kingfisher list` included — and
`from langchain.agents import create_agent` costs about 370 ms. At module scope
that is paid on every listing, for a subagent the request may never activate.

**A module with no `SUBAGENTS` is an error**, not a skipped file, for the reason
a tool module without `TOOLS` is: quietly offering fewer subagents than the
workspace defines is worse than saying so.

---

### A subagent that travels — a `SUBAGENTS` entry with no `build`

The third shape. A module's `SUBAGENTS` entry that writes no `build` is a
*declaration*: kingfisher assembles the subagent from what it says, exactly as it
does from a document. What makes it worth having is what it cannot say.

```python
from pathlib import Path

from langchain_core.tools import tool

HERE = Path(__file__).parent


@tool
def iso_timestamp(written: str) -> str:
    """Normalise one date to ISO 8601."""
    ...


SUBAGENTS = [
    {
        "name": "timestamps",
        "description": "Normalises the dates in a file, and flags the ambiguous ones.",
        "system_prompt": "You normalise dates. ...",
        "builtin_tools": ["read_file", "ls", "glob", "grep"],
        "tools": [iso_timestamp],        # the tools themselves, not their names
        "skills": HERE / "skills",       # resolved by this definition
        "metadata": {"ships_with": "acme-agents"},
    }
]
```

**It may write `name`, `description`, `system_prompt`, `builtin_tools`, `tools`,
`skills`, `metadata` and `source_ids`, and nothing else.** `tools` and `skills` mean
something different here: they hold what the definition brought, never names,
because a name is a lookup in *your* catalogue and a string or `{name: ...}` entry
in `tools` is refused. The fields it may not write name something only one
deployment knows — `subagents` names *your* subagents, `middlewares` selects code
*you* registered, `model` a profile in *your* `models.yaml`. A definition written
somewhere else cannot mean anything by them, so each is refused with that reason
rather than a generic "unknown key".

`source_ids` names ids in *your* `source_ids.yaml` too, and is taken anyway, because
it fails the safe way: a name your vocabulary does not declare stops the deployment
at startup, naming it, and with no `source_ids.yaml` the line has no effect. It is
who may reach this subagent, an **and** with the audience of the agent granting it,
and it is read by kingfisher and never handed to deepagents. A request pins any
subagent's model by name with `run_on`, so `model` stays with the deployment too.

**This is what makes a subagent importable from an installed package.** Export
`SUBAGENTS` from the package, and a deployment takes it by writing one file of
its own:

```python
# subagents/acme.py — the whole of the opt-in
from acme_agents import SUBAGENTS
```

Kingfisher discovers no packages and names none. Installing one changes nothing
until somebody writes that line, and the line lives in the definitions folder
beside every other definition, where `kingfisher list` and `doctor` already report from.

**The shape decides the rules, not where the entry came from.** A re-export hands
over mappings indistinguishable from ones typed into the same file, so there is
nothing for a rule about imports to fire on. Write `build` and you get the
compiled rules; leave it out and you get these. A definition that genuinely needs
your `sql_query` is not portable and belongs in a document, which is the better
format for it anyway.

#### What it carries is its own

`tools` and `skills` here hold the things rather than naming them — a folder's
contents, for a definition that has no folder. An entry of `tools` may be a
`ToolSpec`, as one of a tool file's `TOOLS` may, and what it says holds here too. They
reach that subagent and nothing else: its tools are in no catalogue, so no agent can be
granted them and no request can narrow them away. That is what an imported subagent
being **atomic** means. You use it, or you do not; there is no third option where you
take it apart.

`skills` is a directory, or a list of them — the list being deepagents' own
`SubAgent["skills"]`, so a spec written to that TypedDict loads as it is. Each is a
directory **on this host**, not a path inside the agent's backend: deepagents'
examples write `/skills/...`, which names a route in one deployment, and a
package cannot know another's routes. Kingfisher mounts each directory under
`/skills/subagents/` — one directory at the bundle's own route, several numbered
beneath it in the order listed — and tells the subagent where. Two directories
holding a skill of one name, or one directory inside another, are refused.

Each must be **absolute** — a relative path would resolve against whatever
directory kingfisher was started in, so the definition would find its skills from
one working directory and silently offer none from the next. The definition
resolves it, typically `Path(__file__).parent / "skills"`; kingfisher never
guesses.

`builtin_tools` is the exception, and deliberately. Those are deepagents' tools
rather than the definition's, so a request or agent that withheld `execute` still
withholds it. A package cannot grant itself a shell. A definition carrying `skills`
has to list `read_file` there, the tool a skill is opened with, or it is refused.

`kingfisher seed` ships `timestamps/` as a worked example.

#### Written to deepagents' `SubAgent`

A package written for deepagents, by somebody who has never heard of kingfisher,
types its subagent as deepagents' `SubAgent` TypedDict. Most of one loads as it is:

| `SubAgent` key | Here |
|---|---|
| `name`, `description`, `system_prompt` | as written |
| `tools` | a tool or a plain function; a `dict` is refused, since it names a tool rather than carrying one |
| `skills` | directories on this host, as above — not deepagents' `/skills/...` routes |
| `model` | refused: it names a profile only this deployment defines; a request pins one with `run_on` |
| `middleware` | refused: deepagents merges it by name, so the `FilesystemMiddleware` its docs suggest would replace the one carrying this deployment's rules |
| `permissions` | refused: deepagents' rules replace the parent's rather than narrowing them |
| `interrupt_on` | refused: a subagent inherits its caller's gates |
| `response_format` | refused: the parent reads a subagent's result as text either way |
| `mode` | refused: `fork` would hand a subagent its parent's whole conversation and state, where each is built from its own grant |

A refused key stops the spec loading, with the reason in the message. So does
`runnable`, deepagents' `CompiledSubAgent`: a graph built before this deployment
saw it is out of reach of what a request grants. Write `build` instead — see
*Compiled subagents* below.

The type also cannot say two things this format can. With no `builtin_tools`, the
subagent holds none of deepagents' tools — it cannot so much as read a file, and one
carrying `skills` is refused, since a skill is opened with `read_file`. Your
deployment's file adds the key where it takes the spec, and the package's dict stays
a `SubAgent`:

```python
# subagents/acme.py
from acme_agents import versions_subagent

SUBAGENTS = [{**versions_subagent, "builtin_tools": ["read_file", "ls", "glob", "grep"]}]
```

What it names is still narrowed by the request. With no `metadata`, it cannot record
where it came from. Needing that, or wanting the package to choose its own built-ins,
is the point to add the key and annotate the dict as kingfisher's `PortableSubagent`
instead — see below.

`kingfisher seed` ships `versions/` as a worked example, annotated as a `SubAgent`
so that `ty` refuses any key the TypedDict does not define, with its own module
adding `builtin_tools` the way a deployment's file would.

#### Checked before it loads — `PortableSubagent` and `CompiledSubagent`

Kingfisher publishes a `TypedDict` for each Python shape, so a type checker refuses a
misspelt or refused key in the file, where otherwise startup would:

```python
from __future__ import annotations   # or the annotation is evaluated, and needs kingfisher

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingfisher import PortableSubagent

SUBAGENTS: list[PortableSubagent] = [
    {"name": "timestamps", "description": "...", "system_prompt": "..."},
]
```

`CompiledSubagent` is the same for an entry with `build`. Each takes exactly the keys
its shape takes, `source_ids` and `datazone` included, which `SubAgent` has no word
for. Imported under `TYPE_CHECKING`, the package still pins no kingfisher version and
runs without it installed: the entry is a plain dict either way.

A type cannot say everything the reader checks. A name in a portable entry's `tools`,
a relative `skills` path and a source id your vocabulary does not declare all pass a
checker and are refused at load. The shipped `timestamps/`, `quarters.py` and
`show_your_work.py` are annotated this way.

#### When it wants a helper of its own

`subagents:` is refused here, so a portable definition cannot delegate to another.
There are two ways to get what a helper would have given you, and they cost
different things.

**Ship the helper as a subagent of its own**, in the same `SUBAGENTS` list, and let
an agent grant the two side by side. Each keeps the harness it would have had —
skills, the tool narrowing, the path translation. What it costs is the package's
encapsulation: the deployment has to know to grant both, and the order they work in
lives in somebody else's agent prompt rather than with the definitions.

**Or write `build`** and compose the graph yourself, delegating inside it. That keeps
the whole procedure in the package and is the right shape for one, because internal
delegation is a graph-shaped thing. What it costs is everything in *A subagent that
builds itself* above: a compiled graph is run as given, so it is mounted no skills
index and wrapped in none of kingfisher's middleware, and it reads what it needs
itself.

Folding the step into the prompt is **not** a third option, whatever it looks like.
The reason to delegate is that the subagent burns its own context and hands back an
answer; a prompt cannot buy you a fresh context, which is the one thing you were
after.


---

### Tools and skills of its own — `/subagents/<name>/`

A subagent can keep tools and skills that belong to it alone. Put them in a
folder named after it:

```
subagents/
  redactor/
    redactor.yaml          name: redactor  — the folder is named after this
    tools/
      mask_secrets.py
```

That is the rule for which folder is whose: **a folder is a bundle when it holds a
definition whose `name` matches the folder.** A folder that names no definition is ordinary
grouping — `subagents/analysis/profiler.yaml` is unchanged by any of this.

**Why you would.** An agent writing `tools: ["*"]` gets *every* tool the catalogue
holds, so anything in `tools/` is something the top-level agent can call. A
bundle is the only place a capability can sit that it cannot. It is how a
subagent comes to be trusted with something its caller is not.

**List what it takes from the folder, and only that arrives.** Each entry says
where it comes from:

```yaml
name: redactor
description: Quotes from files that may hold credentials, with secrets masked first. Use before quoting an untrusted file.
builtin_tools: [ls, glob, grep]   # no read_file: raw text never enters the transcript
tools:
  - name: mask_secrets
    source: bundled       # subagents/redactor/tools/
system_prompt: |
  You quote from files that may contain credentials. `mask_secrets` first,
  always; never read a file you are about to quote from any other way.
```

A skill of its own sits in `skills/` beside `tools/` and is listed the same way,
`{name: sampling, source: bundled}` under `skills:`. Like any skill it is opened
with `read_file`, so the definition has to hold that. `redactor` holds no
`read_file`, on purpose, so its procedure is its prompt; `kingfisher seed` ships
`timestamps/` as the subagent with a skill of its own.

`source` takes `shared`, the catalogue's own `tools/` or `skills/`, and
`bundled`, this subagent's folder. A plain name means `shared`, so every other
line you have written keeps its meaning. `redactor` lists nothing shared, so it
gets no catalogue tool; to keep every catalogue tool as well, write
`tools: ["*", {name: mask_secrets, source: bundled}]`.

**The list and the folder must match, in both directions.** A file in
`redactor/tools/` that the definition does not list stops the catalogue loading,
and so does a listed name the folder does not hold:

```
subagent 'redactor': redactor/tools/ holds later, which tools: does not list --
add each as {name: <it>, source: bundled}, or move it out of the folder. Only what
is listed reaches the subagent, so the list and the folder have to match
```

That is what makes the folder safe to change. Dropping a file into it cannot
hand the subagent a capability with no line in any file altered, and renaming
`redactor` cannot take every one away without a word: the definition still lists
entries from a folder it no longer owns, and that is refused too. `kingfisher
list` prints the mismatch under the subagent and `doctor` fails on
`bundled entries`, so both say it before startup does.

**A bundled name cannot be a built-in's.** A `read_file` in `redactor/tools/` would
take the name the agent runtime dispatches, and `redactor` would call your tool
where every other graph calls the file reader -- with none of the rules that one
applies. Refused where a catalogue tool of that name is refused, and for the same
reason:

```
workspace tool(s) read_file would replace a built-in of the same name; rename
them in subagents/redactor/tools
```

Checked for every bundle the catalogue holds, whether or not a request activates
that subagent, so `Kingfisher(...)` refuses it at startup and `doctor` fails on
`tools` with the folder to go and open. Tools a portable definition carries in
Python are held to the same rule.

**The subagent holds them whatever the request granted.** Activating `redactor`
is what grants its parts — a caller never names them, which is the point, since
naming them would mean knowing they exist. Leaving `redactor` out of a request's
`capabilities.subagents` declines the whole subagent; there is no finer lever, deliberately. For the same
reason a bundled `skills:` entry takes no `source_ids`: it reaches whoever reaches the
subagent.

**Only a subagent has a folder.** `source: bundled` in an agent file is refused,
and so is the path form `file::name` on a bundled entry — the folder already says
where it is. `source: shared` is accepted in either file.

**A compiled subagent gets the tools half only.** Its `tools/` reach the graph;
a `skills/` folder beside it is refused. [A subagent that builds
itself](#a-subagent-that-builds-itself--subagentsmodulepy) has the detail.

**A bundled tool answers for its name.** A definition inheriting the catalogue
with `"*"` and listing its own `fetch` gets its own, whatever the catalogue grows
later. Listing `fetch` both as shared and as bundled is refused, since a subagent
dispatches by name. `kingfisher list` prints the inherited case as
`fetch  [private tool, shadowing the catalogue's]`, because shadowing is only
acceptable while it is visible.

**One definition per bundle folder.** `redactor/helper.yaml` beside
`redactor/redactor.yaml` is refused: whether `helper` is inside the bundle has
no honest answer, and the two answers differ in what `helper` may call.

**A folder nobody is named for is reported, not refused.** A grouping folder may
have directories in it, so a `tools/` or `skills/` under a folder that names no
definition is legal. It is usually a renamed bundle, and the definition that
walked away is refused from its own side if it listed anything — so
`kingfisher list` prints

```
  ! redactor/ holds tools/ or skills/ that reach no subagent — nothing in it is named redactor
```

and `doctor` warns on `subagent bundles`, neither of them failing.

**`tools` and `skills` are reserved directory names** anywhere under
`subagents/`, so a skill's own `config.yaml` is never read as a subagent
definition. A grouping folder cannot be called either of them.

**What a listing shows.** Private assets appear indented under their owner:

```
subagents
  redactor  (redactor/redactor.yaml) — Quotes from files that may contain credentials…
      tools: redactor::mask_secrets
      builtin tools: glob, grep, ls
      mask_secrets  [private tool]
  timestamps  (timestamps/timestamps.py) — Finds dates in a file, normalises them…
      tools: timestamps::iso_timestamp
      builtin tools: glob, grep, ls, read_file
      iso_timestamp  [private tool, carried]
      iso-8601  [private skill, carried]
```

**Two limits, stated rather than discovered.**

*A private skill is unadvertised, not unreadable.* Skills are mounted read-only
under `/skills/`, and anything holding `read_file` can open a bundle's skills
there — no read of one is ever denied. The shared catalogue differs only under a
request that names the skills it grants, which denies reads of the rest; a
request granting every skill leaves all of it open. What a bundle buys for a
skill is that no other subagent is *told* about it. A private tool is
stronger: an ungranted tool is never bound into an agent, so it cannot be
called at all. Do not put in a bundled skill anything you would mind another
subagent reading.

*A bundled skill's scripts are not runnable.* A skill's scripts are executed by
the shell against `$KINGFISHER_SKILLS`, and the sandbox grants the shell the
skills catalogue only. A bundle sits under `subagents/`, so its skills can be
read and listed but not run. Keep executable skills in the shared catalogue.

*A subagent's skills sit directly in its folder.* `skills/<name>/SKILL.md`, as in a
mount, and not the one folder of grouping the shared catalogue allows: the subagent
is given its folder as one source, and a source is read one level down. A skill in a
subfolder is not loaded or granted, and `kingfisher list` and `doctor` say where it
is. A definition listing one as `source: bundled` is refused, saying it sits in a
subfolder.

---

## Skills — `/skills/<name>/SKILL.md`, or one folder deep

deepagents' format, unchanged. A directory per skill, `SKILL.md` with `name` and
`description` in frontmatter and the procedure in the body.

**One folder of grouping, and no more** — where tools and subagents nest as deep
as you like. The difference is who reads them: those are walked by kingfisher,
while a skill is read by the *agent*, and deepagents looks for `SKILL.md`
directly inside each entry of a source and no further. Each folder is registered
as its own source, which buys exactly one level:

```
skills/code-review/SKILL.md            -> code-review
skills/research/lookup/SKILL.md        -> research::lookup
skills/research/deep/lookup/SKILL.md   -> unreachable
```

`--list` reports anything hiding below that, because the alternative is a
catalogue that simply looks empty.

### Two skills with the same name

A folder is a *source*, and a skill's full identity is `source::name` — the same
spelling a subagent's `tools:` uses. That lets a vendor pack and a team's own
folder both ship a `lookup` without one replacing the other.

**A bare name stays legal wherever it is unique.** The qualifier is required once
two sources offer the same name, and then it is *required* rather than guessed:

```
capability error: 'lookup' is offered by more than one source, so naming it
alone would silently pick one: write legal::lookup, research::lookup
```

That refusal is the point. Adding a colliding skill turns a working grant into a
loud error instead of silently changing which skill the caller gets.

Renaming is still the better answer for anyone who controls both files — the
model chooses between two entries on description alone, and two distinct names
tell it more than one name and a qualifier. This exists for when nobody controls
both.

The mechanism is progressive disclosure: **only the name and description are in
context by default.** The body is read when the agent decides the skill applies.
So the description does the work — it is a trigger condition, not a summary. Say
when to reach for this, in the words a task would use.

```markdown
---
name: code-review
description: Reviewing a diff or a set of source files for defects — correctness,
  error handling, and tests — and reporting findings with enough evidence to act on.
---

# Code review

Read the diff first, then the files it touches. Report each finding with the
file, the line, and what goes wrong — a finding nobody can locate is a comment.
```

Three shapes:

- **Single file.** `skills/<name>/SKILL.md` and nothing else. The common one.
- **A `reference/` file the body points to,** read on demand. Use this when the
  detail is long and most tasks will not need it: `SKILL.md` stays short enough
  that reading it is cheap, and the reference is fetched only when it applies.
- **A script the body tells the agent to run.** Put it under the skill —
  `skills/<name>/scripts/` — and give the command in the procedure, reaching it
  through `$KINGFISHER_SKILLS`, which the shell exports as the catalogue's path:

      python3 "$KINGFISHER_SKILLS/incident/timeline/scripts/timeline.py" inputs/api.log

  Reach for this where a step is mechanical and a model doing it by eye would
  miss things — `skills/incident/timeline/` walks every line of a log so a
  write-up is not built from whatever the model happened to read. The command
  takes shell paths, not the file tools' virtual ones, and the script runs
  under whatever interpreter the shell finds, so keep it to the standard
  library: nothing installs a skill's dependencies. Only a skill in the shared
  catalogue can do this — [a bundle's are readable and not
  runnable](#tools-and-skills-of-its-own--subagentsname).

A skill the agent declines to read is not a failure. If the task did not warrant
it, not loading it is the mechanism working.

---

## Access — `source_ids:` in the definitions, `source_ids.yaml` for the vocabulary

Which source ids may reach which agents, subagents, tools and skills. Optional:
with no `source_ids.yaml`, kingfisher controls nothing by source id. A tool says who
it admits in its own file, through a `ToolSpec` — see
[`tools.md`](tools.md#or-a-toolspec-when-the-tool-says-who-may-use-it).

A source id is a name in this deployment's vocabulary and nothing else — not a
path a definition was read from, and not a data file a run is handed. Nothing
resolves it to either.

Three files to read alongside this section: `assets/source_ids.yaml`,
`agents/analyst.yaml` and `subagents/auditor.yaml`. `seed` leaves them behind by
default, because a workspace that has not declared `sales_db` cannot read a
definition that names it — copy `source_ids.yaml` first, then
`kingfisher seed --all`.

**Audiences live in the definitions.** Each thing says who may reach *it*, in its
own file: an agent or a subagent on its `source_ids:` line, a tool in its
`ToolSpec`. A skill's own file is `SKILL.md`, which the model reads whole and a
vendor's pack does not let you edit, so the definition offering it says it on the
skill's entry. What is central is only the vocabulary — which names
exist, and which contain which — and that file holds no policy at all.

```yaml
# source_ids.yaml  — the whole file
source_ids:
  A:
  B:
  C:
  warehouse: [A, B, C]
```

```yaml
# agents/assistant.yaml
name: assistant
description: Answers questions about the data in this workspace.
source_ids: [A, B]                 # who may open a session on this agent
builtin_tools: ["*"]               # `task` reaches reviewer, `read_file` a skill
tools: [sql_query, http_fetch]     # names: a tool's lock is in its own file
subagents: [reviewer]              # names: a subagent's audience is its own line
skills:
  - name: audit
    source_ids: [A]                # only A is told about this procedure
  - code-review                    # a bare name means "at my audience"
system_prompt: |
  ...
```

```python
# tools/sql_query.py  — this tool is for A, in every agent that grants it
TOOLS = [ToolSpec(sql_query, source_ids=["A"])]
```

`source_ids.yaml` in `KINGFISHER_DEFINITIONS_DIR`, beside the definitions it governs,
so every deployment sharing that folder shares one vocabulary. Read once at startup, so a
revocation lands on restart. A file that is present and will not parse stops the
deployment: a vocabulary that cannot be honoured must never come up as *no*
vocabulary, because then no audience can be checked at all.

### Who is calling

Once a vocabulary exists, every call has to say:

```python
kf = Kingfisher(config_from_env(), sessions=LocalSessions())
kf.run(Request(task="...", agent="assistant"), source_ids=["B", "C"])
```

`kf.run(...)` with nobody named is **refused**. That refusal is the point: the
dangerous failure is a handler that forgot the boundary, and without it that
handler would serve every caller everything with nothing to show for it. To run
with no caller deliberately, say so:

```python
kf.run(..., source_ids=UNSCOPED)           # a value someone typed, and greppable
```

It takes source id *names*, never a `Capabilities`. A name is resolved against the
definitions this deployment wrote, so the only thing anyone can hand in is an
input — there is no spelling of "give me everything" except `UNSCOPED`.

### The spellings, and one rule

`source_ids:` on a definition is the **default audience** for everything it holds,
since what it holds is reached only through it, and the **ceiling** on what a
`skills:` entry may say. That one rule covers every form the selection fields
already had, so a definition written before audiences existed keeps its exact
meaning once a `source_ids:` line is added above it:

```yaml
source_ids: [A, B]
tools: ["*"]               # every tool, for A and B
```
```yaml
source_ids: [A, B]
tools: [sql_query]         # a list — that tool, for A and B
```
```yaml
source_ids: [A, B]
builtin_tools: [read_file] # what a skill is opened with
skills:                    # the long form — per entry
  - name: audit
    source_ids: [A]            #   for A only
  - name: review
    source_ids: ["*"]          #   for anyone who reaches this definition
```

A tool its own file locks is held to that as well, so `sql_query` above is for
whoever opens this agent *and* passes the tool's `ToolSpec`.

**A skills entry is a name, or a mapping of `name` and `source_ids`** — the same
shape `middlewares:` takes for its `settings`, so there is one long form to learn
rather than one per field.

The field itself stays a list. Writing the whole field as a mapping keyed by name
is refused, and the message shows the entry to write instead: YAML collapses
`{a: X, a: Y}` before any reader sees it, so a name written twice would lose one
of its audiences silently, with nothing able to refuse or report it.

**Only the entries you restrict need one.** An entry that says nothing inherits
the definition's own audience, so an agent told about five skills and restricting
one writes one `source_ids:` line rather than five:

```yaml
source_ids: [A, B]
builtin_tools: [read_file]
skills:
  - name: audit
    source_ids: [A]            # narrower than the definition
  - review                 # a bare name, so [A, B] — the definition's own
  - code-review
```

Mixing the two spellings in one list is the ordinary case, not a special one:
an entry with nothing to attach writes nothing. Long entries that restrict
nothing mean exactly what bare names mean, which is what keeps the spellings
honest about an unrestricted name.

`source_ids: []` on an entry is refused rather than read as "nobody": leaving the
line out is how you say "no restriction", so an empty one is an unfinished edit.
```yaml
source_ids: [A, B]
skills: []                 # none, as it always meant
```

An entry's audience is always an **and** with the definition's own line. The
only way to reach an entry is through the definition holding it, and a caller
who cannot open the agent is never told about its skills — so `audit: source_ids:
[A]` under `source_ids: [A, B]` means "opens this agent, *and* holds A".

That makes a source id from outside the definition's line useful rather than wrong:

```yaml
source_ids: [sales_db, audit_log]
builtin_tools: [read_file]
skills:
  - name: pii-handling
    source_ids: [pii]        # anyone who opens this agent, and holds pii
```

Startup reports it, because the same line is what somebody trying to *widen*
would write by accident:

```
access:
  narrows past this definition's own audience,
  so it reaches only callers holding both:
    agent analyst: skill pii-handling  [pii]
```

Said rather than refused, because no rule can tell the two apart — `[pii]`
meaning "and pii" and `[audit_log]` written under `[sales_db]` by somebody who
wanted to add `audit_log` are the same shape, and only the author knows which.

**Any overlap grants.** A longer list means *more* callers, which is what
everyone reads an access list as meaning. `["*"]` is everyone.

### Which fields take an audience

On an entry, `skills` alone. A tool and a subagent each say who may use them in
their own file, and an entry under `tools:` or `subagents:` that writes
`source_ids` is refused at load, saying where the line goes instead:

```yaml
source_ids: [A, B]
builtin_tools: ["*"]         # `task` and `read_file`, for the lines below
tools: [sql_query]           # the lock is ToolSpec(sql_query, source_ids=[...])
subagents: [reviewer]        # the audience is reviewer.yaml's own source_ids:
skills:
  - name: audit
    source_ids: [A]           # only A is told this procedure exists
```

So who may use a tool in an agent is three checks, each in one place: whether the
caller can open the agent, its own `source_ids:`; whether the agent lists the tool,
its `tools:`; and whether the caller passes the tool's own lock. To answer "who can
use `http_fetch`?" you read its tool file, not every agent that lists it. An agent
still decides what it holds by listing it — leave a tool off and it is not this
agent's. What that gives up is one tool locked differently in two agents, which
listing covers when it comes up.

A skill reaches the model by a different road from a tool — it is advertised
through a middleware rather than registered as a callable — and a skill out of
reach is not advertised at all.

Note what that is and is not, because a skill was never a boundary: removing it
means the agent is not *told* about it. The file is still on disk, so this is
guidance, and an agent holding `execute` can read anything. The boundary is the
tools a skill's procedure would need, which have locks of their own.

**`builtin_tools` deliberately takes none.** deepagents registers its own tools
itself, so kingfisher can filter them but never leave them out of a graph, and a
model has been measured calling `execute` from memory when it was never offered.
Writing a mapping there is refused rather than parsed as a
single tool named `{'execute': ['A']}`.

What gates the built-ins is which *agents* a source id may open: an agent declaring
`builtin_tools: [read_file, ls, glob, grep]` cannot yield the shell to anyone,
whatever they ask for.

### Who reaches what, in one listing

A skills entry's audience is a property of *this definition's use of it*: `reviewer`
may tell only `[A]` about `audit` while `analyst` tells `[A, B]`, and both are
correct. So there is no single line answering "who is told about `audit`?", and
`kingfisher list` answers it instead, with a roll-up by skill beside the
per-definition view, so a use quietly wider than its neighbours is visible rather
than something you would only find by grepping. A tool's lock and a subagent's
audience are each one line, printed once:

```
access — by definition
  agent analyst  [sales_db, audit_log]
      skill tabular-qa  [sales_db]
  subagent ledger  [*]  datazone: finance
  subagent reviewer  [A, B]
  tool counter::count  [A]
  tool sql_query  [sales_db]

access — by skill
  tabular-qa
      analyst  [sales_db]
```

A datazone follows the source ids it is required beside, and a subagent's own tool
is named for its subagent, `counter::count`. `--json` carries the same, under
`audiences`. Run with `--as`, the listing shows none of this: what a caller reaches
is theirs to see, and the policy is not.

### No `source_ids:` line means everyone

An absent optional field means no restriction, which is what it means everywhere
else in these formats — and reading it as "nobody" would stop every unannotated
definition working the moment `source_ids.yaml` appeared. So adoption is
incremental: annotate the sensitive definitions first.

That must not also be silent, so startup names what carries no line:

```
access:
  no source_ids: line, so reachable by everyone:
    agent assistant
    subagent extractor
```

### One grant, everywhere

The caller's source ids bound the agent, its subagents, and their subagents alike. A
subagent's own `source_ids:` is its audience — "this one is sensitive wherever it is
used" — and the only one: a parent names it and says nothing about who reaches it.

So a subagent whose tools are partly out of reach **runs with the rest**, and
nothing tells the caller what it lacks — [out of reach reads as not
offered](#what-a-caller-can-see):

```yaml
# subagents/reviewer.yaml
name: reviewer
source_ids: [A, B, C]
tools: [sql_query, http_fetch]
```

```python
# tools/sql_query.py
TOOLS = [ToolSpec(sql_query, source_ids=["A", "B"])]
```

A caller in `C` gets a `reviewer` holding `http_fetch` and not `sql_query`.
Write subagent prompts to cope — *"if you can check the figure against the
database, do; if not, say so"* — because the caller decides, not the file.

**An ungranted tool never reaches the graph.** It is not attached and then
refused: `create_deep_agent` is handed only what the caller reaches, so the model
is never told it exists and never spends context on its schema. An ungranted
subagent is never compiled, so its graph is never paid for either.

### Compiled subagents

A Python-declared subagent may carry `source_ids`, and lists its tools by name:

```python
SUBAGENTS = [
    {
        "name": "profiler",
        "description": "...",
        "source_ids": ["A", "B"],
        "tools": ["sql_query"],
        "build": _build,
    }
]
```

`source_ids` is a real boundary: whether a compiled subagent is built at all is
kingfisher's decision, so there is nothing there for a graph to ignore.

`tools` is a list of names, as in a file. A tool's own lock narrows what is
**handed to** `build`, with the caveat the plain list already carries: deepagents
applies no allowlist to a graph it did not build, so a `build` that ignored what
it was given could call anything it holds. A mapping saying who may use each tool,
`{"sql_query": {"source_ids": ["A"]}}`, is refused: the lock goes in the tool's
file. `skills` and `subagents` are refused for a compiled subagent regardless —
see `NOT_COMPILED`.

### What a caller can see

**Out of reach reads as not offered.** An asset a caller's source ids do not reach is
absent from listings, from the "this workspace offers …" in a refusal, and from
the report of what a run withheld. Naming one gives the same answer naming a typo
does, so nothing lets a caller enumerate the catalogue by guessing.

A mistake in a definition's own file is the file's, so it is refused whoever is
calling: a subagent nothing defines, two of a name, a loop. What such a refusal offers
instead is still only what the caller reaches, but three of them name what they are
about, out of reach or not, because no true wording hides that it exists: a file naming
a bare name two subagents share, an agent's file naming both, and a loop. A caller
meets these only through a file the operator has to fix anyway.

The operator's view is the whole of it:

```
$ kingfisher list                 # every definition, its audience, and a roll-up
$ kingfisher list --as B,C        # exactly what that caller sees
$ kingfisher list --as warehouse  # check what it covers before trusting it
```

`list` is exempt from the refusal above, and only `list`: it is read-only, and
whoever runs it is on the host with the definitions already in front of them.
Running a *turn* still has to say who is calling.

### Agents, and what a session does not let you keep

`agents` is checked where the *name* is resolved: when a session is opened, and
again on **every turn afterwards**.

A session pins its agent for life, and a session id is a bearer credential.
Checked only at the open, holding one would be a durable grant to an agent you
may not open, and a caller who lost a source id would keep running what they had. So:

- A leaked session id runs no agent its holder could not open themselves. It does
  hand over the conversation: its holder is shown everything earlier in it, results
  from tools their own source ids do not reach included. Kingfisher has no users to
  tell apart, so keeping each session id to the people allowed to read it is the
  caller's job, as deciding their source ids is.
- A demotion takes effect on the caller's next turn, and an in-flight
  conversation on an agent they can no longer reach stops being usable. That is
  intended, not a bug — it is the same answer as "you may not run this agent",
  arriving at the first moment it became true.

### The vocabulary is closed

A source id named in a definition, or by a caller, that `source_ids.yaml` does not
declare is **refused**. Both directions matter and they fail differently:

- A caller naming an undeclared source id would otherwise reach nothing, which looks
  exactly like a caller who was denied.
- A definition naming one would otherwise invent a source id nobody holds, and the
  only symptom would be a tool quietly reachable by no one — found weeks later
  by whoever needed it.

A list body expands one name into others, once, when the file is read:

```yaml
source_ids:
  warehouse: [A, B, C]
```

A caller holding `warehouse` reaches anything listing `A`, `B` or `C`, without
`warehouse` appearing on a single definition. A loop is refused naming the whole
cycle rather than one edge, since one edge does not tell a reader which link to
cut.

### Requiring several source ids at once

An audience list is an **or**: any one of the names is enough. An entry of that
list may be an **and**, written as a **set**, which is satisfied only in full:

```yaml
source_ids: [warehouse, {finance_db, pii}]
```

Either a `warehouse` holder, or a caller holding *both* `finance_db` *and*
`pii`. That is
or-of-ands, which is the shape access rules actually take, and it stays one
field with no rule about how two fields combine.

Where the same requirement appears more than once, name it in the vocabulary
instead — the same shape, so the named form is simply a name for the inline one:

```yaml
# source_ids.yaml
source_ids:
  finance_db:
  pii:
  finance_db_pii: {finance_db, pii}
```

```yaml
# any definition
source_ids: [warehouse, finance_db_pii]
```

Use the inline form for a one-off and the named form for anything reused; a name
that appears on two definitions and means the same thing on both belongs in the
vocabulary, where changing it changes both.

The brackets are the whole difference, and they are told apart by shape rather
than by a keyword: `[a, b]` is what a name covers, `{a, b}` is what it requires.
A name is one or the other and cannot be both, so the question of what a name
that grants and costs at once would mean never comes up.

Consequences worth knowing:

- **Covering satisfies a requirement.** Expansion runs first and requirements are
  checked against whatever is held afterwards, so a `warehouse` that covers both
  parts satisfies a compound of them.
- **A caller may not present a compound name.** It is what holding its parts adds
  up to, not something to claim. The refusal names the parts to send instead.
- **A covers list may not hand out a compound**, named or inline.
  `warehouse: [finance_db_pii]` and `warehouse: [{finance_db, pii}, A]` are both
  refused — either would hand a `warehouse` holder the compound while they hold
  neither part. Cover the parts instead. Requirement loops are refused the way
  covering loops are.
- **A set is a set of names, not a mapping.** `{finance_db: pii}` is refused:
  read as a set it would take `finance_db` and throw away what was written
  beside it.
- **In Python, a requirement is a Python set:**
  `source_ids=["warehouse", {"finance_db", "pii"}]`. A list or tuple inside the
  audience is refused rather than read as *all of*, because the same brackets
  already mean *any of* one level up.

`kingfisher list` writes a requirement the way the file does, `{finance_db, pii}`,
and prints what each named compound requires above the audiences, since a name
alone tells a reader nothing on the line it appears on. The `--json` form nests
it instead — `[["finance_db", "pii"]]` — so a script never has to parse a set out
of a string.


### Datazones — a second field beside source ids

A caller may also be **in a datazone**: one name, never several. A subagent or a
tool may say which datazone it is for, and a caller reaches it only from that one.
`assets/subagents/quarters.py` is this section as a working file, and `analyst.yaml`
grants it.

```yaml
# source_ids.yaml
source_ids: [sales_db, audit_log]
datazones: [finance, hr]
```

```yaml
# subagents/ledger.yaml
name: ledger
description: Reads the ledger.
datazone: finance
system_prompt: |
  ...
```

```python
kf.run(Request(task="...", agent="analyst"), source_ids=["sales_db"], datazone="finance")
```

`kingfisher run`, `decide` and `list` take `--datazone NAME` beside `--as`. In
Python, a subagent declared without `build` writes `"datazone": "finance"`, and a
tool file writes `ToolSpec(tool, datazone="finance")`.

- **It is an *and* with everything else.** A caller has to pass the subagent's or
  tool's `source_ids` *and* be in its datazone.
- **A caller in no datazone reaches only what names none.** Forgetting to pass one
  must not hand a caller every datazone's tools. `UNSCOPED` switches both checks off.
- **An agent has no datazone**, and writing one is refused: an agent runs in
  whichever datazone its caller names. Nor does an entry —
  `{name: payroll, datazone: hr}` is refused, since on an entry it could only repeat
  the tool's own or contradict it.
- **A datazone the vocabulary does not declare stops the deployment**, as an
  undeclared source id does. With no `source_ids.yaml`, `datazone:` lines have no
  effect, and a caller naming a datazone is refused.
- **Nothing is refused for being unreachable.** An agent that names a finance tool is
  not wrong for an hr caller — the tool is left out of that caller's build.

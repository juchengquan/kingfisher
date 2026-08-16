# Presets

One working definition of each thing a request can activate — a skill, a
subagent, a tool — for you to copy and edit. Nothing here is loaded
automatically: a preset does nothing until it is in a workspace.

    uv run main.py --seed-presets     # all of them, into $KINGFISHER_WORKSPACE

They ship *inside* the package, so that works from an installed kingfisher and
not only from a checkout. This is not the package growing domain content: a
preset demonstrates a **format** and is rewritten on first contact with a real
task, where domain content would presume what your project is about. Kingfisher
ships no skills of its own for the same reason its base prompt carries no
domain instructions — a general agent should read the same whatever the project
is.

To take one rather than all of them, copy it:

    cp -r "$(python -c 'import kingfisher.presets as p; print(p.__path__[0])')/skills/code-review" \
          "$KINGFISHER_WORKSPACE/skills/"

A request then names them:

```python
from kingfisher import Capabilities, Request, run

run(Request(
    task="Review the diff in /data/change.patch",
    capabilities=Capabilities(
        tools=("read_file", "ls", "glob", "grep", "execute", "task"),
        skills=("code-review",),
        subagents=("reviewer",),
    ),
))
```

Leaving a field unset means *no opinion* — everything the workspace offers.
Passing an empty tuple means *none*. Naming something that does not exist raises
`CapabilityError` at build time rather than running with quietly less than you
asked for.

### Putting one delegate on a different model

A definition says where its delegate runs. A request can override that for a
delegate it names — useful when the file is not yours to edit, or when the
model it pins is not one your credentials reach:

```python
from kingfisher import Request, RunOn

run(Request(
    task="Check these figures",
    capabilities=Capabilities(subagents=("reviewer", "second-opinion"), models=("MiniMax-M2.5",)),
    run_on={"second-opinion": RunOn("MiniMax-M2.5")},
))
```

**It is off until a deployment grants it, and granted per model name.** Every
other field here only ever takes something away — a request picks from what the
workspace offers and cannot invent anything, which is what makes an untrusted
caller safe to accept. Naming a model is the one thing that *chooses*, and
models differ in price by more than an order of magnitude. `models` is `None`
by default, so a caller who was granted nothing can choose nothing.

**It replaces what the delegate runs, and where follows from that.** There
were two fields here once, and a rule that an override had to replace both or
neither — the file's endpoint joined to your model is a 404 if you are lucky
and a wrong-model run if you are not. One field cannot be half of anything, so
the rule is gone: name a model, and its entry in `models.yaml` says where it
runs.

The endpoint that model resolves to keeps its own permission. Choosing a model
is not an exemption from where a request's prompts may go.

---

## Tools — `/tools/<module>.py`, at any depth

Two kinds. The **built-in set** comes with the agent and you select from it by
name. **Workspace tools** are Python you write, imported from the workspace's
`tools/` directory and added to that set.

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

Three things worth knowing before you restrict this list:

- **`read_file` is close to mandatory.** The filesystem tooling assumes it. An
  allowlist without it produces an agent that cannot see its own workspace.
- **Omitting `task` disables subagents** however many you activate — `task` is
  how delegation happens.
- **`execute` is the one that matters for isolation.** It bypasses the
  filesystem permission layer entirely, so a request that activates the shell
  can reach anything the process can, including skills it did not activate. Deny
  rules are a real boundary only for requests without it.

### Workspace tools

A `.py` file in `$KINGFISHER_WORKSPACE/tools/` defining `TOOLS` — the list of
tools it contributes. Nothing is inferred: a helper in the same file stays a
helper.

- [`http_fetch.py`](tools/http_fetch.py) — **something the built-in set cannot
  do at all.** The clearest reason to write one.
- [`sql_query.py`](tools/sql_query.py) — **making an existing capability
  narrower.** `execute` could already reach the database, but it could reach
  everything else too. A tool states the reach in code, so a request can
  activate `sql_query` and *not* the shell.
- [`csv_profile/`](tools/csv_profile/) — **a tool that outgrew one file.** Two
  tools sharing a notion of what a column is, as a package.

The docstring is not decoration — it is what the model reads when deciding
whether to call the tool, exactly like a skill's `description`. Write it as a
trigger condition and say what the arguments mean in a caller's words.

#### Folders, when one file stops being enough

`tools/` may be as deep as you like, and `__init__.py` decides what a folder is:

```
tools/
├── http_fetch.py                  a module, as always
├── research/
│   ├── find_company.py            organisation — each file independent
│   └── legal/filings/lookup.py    as deep as you want
└── csv_profile/
    ├── __init__.py                a package — one unit
    ├── profile.py                 the tools
    └── columns.py                 a helper, and an ordinary module
```

**A folder without `__init__.py` is organisation.** Each file is loaded on its
own and declares its own `TOOLS`, exactly as a flat one does.

**A folder with `__init__.py` is a package.** It is imported whole, declares
`TOOLS` once, and nothing inside it is scanned separately — so its modules
import from each other normally and a helper is a helper because it is not
exported, not because it is spelled `_columns.py`. Reach for this the moment a
tool grows a second file.

Two things follow, and both are the point:

- **A folder never reaches a name.** A tool is named by itself, so
  `research/find_company.py` still offers `find_company`, and a request grants
  it by that name. Moving a file between folders changes nothing a caller
  types. `--list` says where each one came from when the name does not.
- **Skills cannot do this.** See below — the difference is real and worth
  knowing before you try.

Modules starting with `_` are still skipped, which is now mostly a way to park
a file you have not finished. Inside a package you do not need it.

Five things the loader will refuse, all for the same reason: an agent quietly
holding different tools than the workspace defines is worse than a run that
stops.

| Refused | Why |
| --- | --- |
| A module with no `TOOLS` | Scanning for callables would guess at intent |
| A module that will not import | Skipping it gives the agent silently fewer tools |
| Two modules claiming one tool name | `tools_by_name` is a dict; the later would win in silence. Checked across folders, so two people cannot each add a `find_company` |
| A tool named like a built-in | Same, except the thing that vanishes is `read_file` |
| A relative import in a loose file | It has no parent package and never will. The error says to make the folder a package |

Hidden directories and `__pycache__` are not descended into. A virtualenv left
under `tools/` would otherwise be imported, and this directory is imported
rather than read.

**A tool is code, and it runs in the kingfisher process** — not in the agent's
sandbox, and not under the filesystem permissions. `tools/` is deliberately
*not* a backend route, so no file tool can reach it; the only agent that could
write one is an agent already holding `execute`, which can run anything on the
host regardless. Treat this directory the way you treat the rest of your
source: it is yours, not the agent's.

`KINGFISHER_TOOLS_DIR` relocates it, the way `KINGFISHER_SKILLS_DIR` does, so a
catalogue of tools can be deployed once and shared by every workspace.

---

## Subagents — `/subagents/<name>.yaml`, at any depth

A YAML document. Everything the delegate is, in one file.

Folders work here too, and for the same reason they work for tools: kingfisher
reads these, so nothing outside it has an opinion about the layout.
[`analysis/profiler.yaml`](subagents/analysis/profiler.yaml) sits in a folder
and is still activated as `profiler` — the `name:` field is the identity and the
path is not, which was already true of the filename.

There are no packages here. A definition is a document, not code, so there is
nothing to import and a folder is only ever organisation.

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

**Requests do not take one either.** `--tools` and `capabilities.tools` name
tools plainly. A definition is written once and read many times, often by
someone who did not write it, and that is where a location pays; a flag is typed
once and thrown away. A request also arrives from a caller who has no idea what
your folders look like — asking them for a path would make your layout part of
your API, and moving a file a breaking change for people who never saw it.

```yaml
name: reviewer
description: Re-checks numeric claims against the files they came from. Use before reporting figures you computed once.
builtin_tools: [read_file, glob, grep]
system_prompt: |
  You verify claims. You do not improve prose or add analysis — another
  agent has already done that work and you are the check on it.

  Recompute each figure yourself, from the file. Report the number you got,
  the caller's number, and whether they match.
```

That is a whole definition: three required fields, one optional, nothing else
needed.

### Two tool lists, not one

`builtin_tools` is the set that comes with deepagents — the table further down
lists them. `tools` is whatever *your* workspace defines in `tools/`.

They are separate because they are granted separately, and one list meant a
delegate could not ask for a workspace tool without giving up every built-in:

```yaml
builtin_tools: [read_file, glob]   # named, so only these two
tools: [http_fetch]                # and this one, costing no built-in
```

**Omitting a list means all of it. An empty list means none.** That difference
does real work: every shipped preset writes `tools: []`, because "read-only"
has to keep meaning read-only rather than quietly growing whatever the
workspace adds later.

To say "all of them" out loud, write `["*"]`:

```yaml
builtin_tools: ["*"]   # same as leaving the line out, said on purpose
```

A list, because every selection here is a list. Bare `"*"` is refused by name —
a *request* spells it that way, and one spelling in one place beats two
spellings everywhere. Mixing is refused too: `["*", read_file]` cannot mean
both things at once.

Sending a delegate somewhere cheaper — or somewhere else entirely — is one
more line:

```yaml
model: gpt-5            # an entry in your models.yaml
```

Omit it and the delegate runs whatever the deployment runs. That is the usual
case, and `reviewer` is the shipped example of it.

There was a `provider:` beside it, naming an endpoint by style, and a rule that
the two moved together. Both are gone. An endpoint is a property of the model —
`models.yaml` says which one serves `gpt-5` — so there is no second line to keep
in step, and the half-pair mistake cannot be written.

This is the only place it is said. There is no environment variable for it: one
could only say "every delegate", which is the wrong size for the decision — it
would silently defeat `second-opinion`, whose whole job is to be a different
model from the one beside it.

**The name has to be one your catalogue defines.** The table in `models.yaml` is
closed, and a definition naming a model outside it is refused when the agent is
built, rather than reaching an endpoint that has never heard of it:

```
subagent 'second-opinion': no model 'gpt-5'; this deployment can run ('MiniMax-M3',)
```

It fails only for the preset you *activated* — a subagent is wired only when a
request names it, so seeding one you cannot run costs nothing until you ask for
it. And `run_on` can rescue it without editing the file, which is why this is
not checked across the whole catalogue up front: the refusal would fire before
the override could apply.

Which is why **no preset ships with a `model:` line.** A file inside the wheel
cannot portably name a vendor's model id: `extractor` said `MiniMax-M2.5` and
would refuse to start for anyone without a MiniMax entry.

They name an `alias:` instead — a general name your catalogue binds:

```yaml
# models.yaml
aliases:
  cheap: MiniMax-M2.5     # extractor, profiler
  alternate: gpt-5        # second-opinion
```

A definition writes `model:` *or* `alias:`, never both: an alias is a model name
once bound, so a file saying both has said one thing twice with no rule for
which wins.

**An unbound alias refuses the build**, and does not fall back to the default.
That is the whole reason the indirection is worth having. `second-opinion` exists
in order not to be the model beside it; handing it that very model because
nobody bound `alternate` is the answer nobody asked for, and it is invisible —
the delegate builds, answers, and the answer is worth nothing. Refusing fires
only when a request *activates* the delegate, so seeding presets you have not
bound for still costs nothing until you use them.

| Field | | |
| --- | --- | --- |
| `name` | required | What a request activates it by. Authoritative — the filename is not |
| `description` | required | Single line. This is what the parent agent sees when deciding whether to delegate, so write it as a trigger, not a title |
| `system_prompt` | required | The delegate's whole instruction, written after `\|` |
| `builtin_tools` | optional | deepagents' own set, listed in the tools table above. Unset means all of them; `[]` means none |
| `tools` | optional | The tools *your* workspace defines. Unset means all of them; `[]` means none |
| `skills` | optional | Which procedures it is told about. Unset grants **none** — the opposite of `tools`, because its body is already its procedure |
| `middleware` | optional | Names entries from a registry the deployment supplies. The one field that selects *code*, so it is granted, never inherited |
| `subagents` | optional | Delegates this one may consult mid-job. Unset grants **none**. One level — see below |
| `model` | optional | An entry in your `models.yaml`. The endpoint follows from it; this is where cost routing goes |
| `alias` | optional | A general name your `models.yaml` binds to a model. For a definition that knows what *kind* of model it needs and cannot know its name. Not with `model` |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |

### A delegate that consults another

A delegate can hit a question of a different kind mid-job — `reviewer` doubting
one figure, when checking figures is not what it is for. It can ask for help:

One more line in `reviewer.yaml`:

```yaml
subagents: [second-opinion]
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

**One level.** A helper works alone. A file named as somebody's helper may not
declare helpers of its own, and a catalogue that asks for it is refused when
the definitions load, naming both files:

```
'reviewer' names 'second-opinion' as a helper, but 'second-opinion' names
helpers of its own (extractor); delegation goes one level, so either
'reviewer' stops naming 'second-opinion' or 'second-opinion' stops naming its own
```

That bound is what makes a loop impossible: `reviewer` → `second-opinion` →
`reviewer` needs a helper with helpers, and there is no such thing.

It does mean a file can mean different things depending on who reached it.
`second-opinion.yaml` may consult `reviewer` while callers name it directly,
and stops being allowed to the moment `reviewer` names it as a helper — so
adding one line to one file can invalidate another that nobody touched. The
error names both, because whoever reads it may own neither.

**What it costs.** Every level is a real conversation with a real model. A
helper's tokens are on your bill and in the run log, attributed to it by name,
and its work streams into the terminal under `[second-opinion]` — so this is
visible rather than merely charged.

Three reasons to reach for one, one example each:

- [`reviewer.yaml`](subagents/reviewer.yaml) — **independence.** A second agent
  that recomputes a claim without seeing how the first one got there catches
  errors that re-reading your own work does not.
- [`extractor.yaml`](subagents/extractor.yaml) — **context isolation.** It reads
  a large pile of files and returns a short answer; the bulk stays in its
  context rather than yours. Note the narrower `tools`, and add a cheap `model:`
  of your own.
- [`second-opinion.yaml`](subagents/second-opinion.yaml) — **a different
  model.** Two models from one family share failure modes, so this one is meant
  to answer somewhere else entirely. It is the one preset that is worth nothing
  until you give it a `model:`, and its comment says so.

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
would reach the delegate as one run-on line, in a file that loads without
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
      ls -la /data          #    plain `|` cannot read this, and says so
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
check that every delegate names a team:

```python
from kingfisher.infrastructure.subagent_store import LocalSubagentRepository

for spec in LocalSubagentRepository(cfg.subagents_dir).specs.values():
    print(spec.name, spec.metadata.get("owner", "unowned"))
```

Handing it to the agent would mean picking a consumer, and the obvious one —
passing the definition to a middleware factory — changes a published argument
for a use nobody has yet. The field is easy to add a consumer to later; a
changed constructor is not easy to take back.

### Lists, and fields that are not here

`tools`, `skills` and `middleware` take either form:

```yaml
tools: [read_file, grep]
tools:
  - read_file
  - grep
```

**A field not in the table above is an error, not a field that gets ignored.**
Ignoring one is indistinguishable from honouring it: `tolls:` used to hand a
delegate *every* tool its parent had, because unset `tools` means inherit. A
near miss is named — *did you mean 'tools'?* — and the fields deepagents knows
but this format declines (`permissions`, `subagents`, `interrupt_on`,
`response_format`) each say why, since "unknown field" reads as an omission
worth working around when the answer is that honouring it would be wrong.

Skills take the opposite rule, deliberately: kingfisher does not own that
format, so an unrecognised key there is left alone.

---

## Skills — `/skills/<name>/SKILL.md`, exactly one level

deepagents' format, unchanged. A directory per skill, `SKILL.md` with `name` and
`description` in frontmatter and the procedure in the body.

**This is the one that cannot be nested**, and the exception is worth a sentence
because tools and subagents both can. Those are read by kingfisher, which walks
as deep as you like. A skill is read by the *agent*, through a filesystem route,
and deepagents lists the directory once and looks for `SKILL.md` directly inside
each entry. It does not go further.

So `skills/grouped/company-lookup/SKILL.md` is not tidied away — it is
unreachable, and nothing raises. `--list` reports any folder hiding one, because
the alternative is a catalogue that simply looks empty.

The mechanism is progressive disclosure: **only the name and description are in
context by default.** The body is read when the agent decides the skill applies.
So the description does the work — it is a trigger condition, not a summary. Say
when to reach for this, in the words a task would use.

- [`code-review/`](skills/code-review/) — single file. The common shape
- [`release-notes/`](skills/release-notes/) — a `reference/` file the body
  points to, read on demand. Use this shape when the detail is long and most
  tasks will not need it: `SKILL.md` stays short enough that reading it is cheap

A skill the agent declines to read is not a failure. If the task did not warrant
it, not loading it is the mechanism working.

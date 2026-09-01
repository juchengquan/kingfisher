# Writing definitions

The four formats a workspace can hold — an agent, a skill, a subagent, a tool —
with every field, what it means, and worked examples you can paste. This page is
the reference for the formats themselves, and it starts with the agent because
that is what a request names; everything else here is something an agent selects
from.

Kingfisher ships none of these files. Its job is to find, validate and compose
definitions, and it does all three against files it did not write — every
definition is content a workspace rewrites on first contact with a real task,
where the framework has no business having an opinion. It ships no skills of its
own for the same reason its base prompt carries no domain instructions: a
general agent should read the same whatever the project is.

Definitions ship with kingfisher — a working example of each format, copied
into your workspace and yours to edit from then on:

    kingfisher seed                   # copies them into $KINGFISHER_WORKSPACE
    kingfisher list                   # and what the workspace offers now

Nothing is loaded automatically. A definition does nothing until it is in a
workspace catalogue, and what it says there is yours; kingfisher never reads
back from the copy it shipped.

To seed from your own set instead, point at a directory holding `agents/`,
`tools/`, `skills/` and `subagents/` — any of them, none required:

    kingfisher seed --from ./my-definitions

They were their own distribution for a while, found through a
`kingfisher.assets` entry point so that anyone could publish a pack, and then a
set inside the wheel. A directory covers the same ground without a wheel, a
publish step or metadata, so both went; if a second publisher ever wants a group
it comes back. `KINGFISHER_ASSETS` names the directory, and `--from` overrides
it for one run.


A request then names what it wants:

```python
from kingfisher import Capabilities, Request, run

run(Request(
    task="Review the diff in /data/change.patch",
    agent="assistant",
    capabilities=Capabilities(
        tools=("read_file", "ls", "glob", "grep", "execute", "task"),
        skills=("code-review",),
        subagents=("reviewer",),
    ),
))
```

`agent` is required; there is no default. Leaving a *capability* unset means
*no opinion* — everything that agent declares, which is not the same as
everything the workspace holds: an agent that names three tools cannot be asked
for a fourth. Passing an empty tuple means *none*. Naming something that does
not exist raises `CapabilityError` at build time rather than running with
quietly less than you asked for.

### Putting one delegate on a different model

A definition says where its delegate runs. A request can override that for a
delegate it names — useful when the file is not yours to edit, or when the
model it pins is not one your credentials reach:

```python
from kingfisher import Request, RunOn

run(Request(
    task="Check these figures",
    agent="assistant",
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

## Agents — `/agents/<name>.yaml`, at any depth

The agent is what a request runs, and everything else on this page is something
an agent selects from: the tools it holds, the skills it may read, the delegates
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

**A request must name one.** There is no default agent and no implicit one; a
request without `agent` is refused, and the message lists what your workspace
has. The agent decides where every prompt in the session goes and what it costs,
and a default would put that choice somewhere the call site never mentions.

**A session keeps the agent it started with.** It is resolved when the session
opens and stored beside it, so editing the file mid-conversation does not change
the instructions under a history that already happened — a deploy mid-session is
ordinary, and that is exactly when a live conversation would otherwise pick up a
different prompt from the one its own transcript was produced under. A later turn
may name the same agent again; naming a different one is refused.

Over HTTP that lands where the choice is made:

```
POST /sessions   {"agent": "surveyor"}
```

which answers with the session id *and* what it resolved to — the one moment you
can see what you got without running a turn.

### The prompt is added to, not replaced

`system_prompt` is the same word a subagent file uses, doing a different job. A
subagent's *is* the whole prompt. An agent's is the last of three parts:

    prompts/system.md    what the harness is — /data is read-only, /skills is
                         loadable, where memory lives. Ships with kingfisher
    PROMPT.md            what this workspace is about. Yours, optional, and it
                         reaches your delegates too
    system_prompt        what this agent is. Yours, and required

There is no way to replace the first, and that is not a gap. An agent without it
is not leaner — it is one holding tools nobody told it about, discovering its
permissions by being denied. Opening a session returns what was assembled, which
is where to check rather than guess.

The third part is required, for the reason `description` is. The two documents
above it are written once for the whole deployment and neither has heard of this
agent, so a file that leaves the prompt out is a list of tools with nothing
anywhere saying what they are for. One line is enough — say what this agent is
and what it is careful about, and leave out anything already true of every agent
in the workspace.

### The fields

| Field | | |
| --- | --- | --- |
| `name` | required | What a request names it by. Authoritative — the filename is not |
| `description` | required | Single line. Nothing reads it at run time; it is how somebody chooses between your agents in `kingfisher list` |
| `system_prompt` | required | This agent's own instruction, added after the two documents above. Write it as a literal block — `system_prompt: \|` — so your line breaks survive |
| `builtin_tools` | optional | deepagents' own set, listed in the tools table below. Unset means all of them; `[]` means none |
| `tools` | optional | The tools *your* workspace defines. Unset means all of them; `[]` means none |
| `skills` | optional | Which procedures it is told about. Unset grants **none**; write `["*"]` for every skill the workspace offers |
| `subagents` | optional | Delegates it may consult. Unset grants **none**; `["*"]` is every subagent the workspace offers |
| `middleware` | optional | Names entries from a registry the deployment supplies. The one field that selects *code*, so it is granted, never inherited |
| `model` | optional | An entry in your `models.yaml`. Unset runs the `default:` there. May be a list, tried in order |
| `memory` | optional | `false` to run without the memory file on a deployment that wired one |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |
| `groups` | optional | Who may open a session on this agent. Unset means everyone. Also the default audience, and the ceiling, for its `tools`, `subagents` and `skills` entries — see [Access](#access--groups-in-the-definitions-groupsyaml-for-the-vocabulary) |

The two tool fields inherit and the two name fields do not, which is the same
rule a subagent file follows and worth saying as one sentence: **leave a tool
field out and you get everything available to you; leave `skills` or
`subagents` out and you get none.** Tools are what an agent needs to *act* and
it can do nothing without them. Skills and delegates are what it needs to
*know* and *ask*, and most agents need neither.

### Helpers arrive with the delegate that wants them

An agent naming `reviewer` gets whatever `reviewer` names, and whatever those
name in turn. The chain is worked out when the catalogue loads, and
`kingfisher list` prints it — so an agent file never carries a name it has no
relationship with, and never goes stale because a file it does not own changed
its own helpers.

**The agent itself has to work.** A model your catalogue does not define
refuses. **Anything below it that cannot run is left out and reported** — which
is what lets a freshly seeded workspace run at all when one delegate names a
model you have not set up.

### Three fields are refused

Each with its own message rather than a generic "unknown field", because the
generic one reads as *not supported yet* and sends you looking for a workaround:

- **`permissions`** — deepagents' permissions *replace* the parent's rather than
  narrowing them, so writing this here would drop `/data` being read-only along
  with everything else it inherits.
- **`interrupt_on`** — an agent has a checkpointer and a human, unlike a
  delegate; what is missing is anything in the service that surfaces an
  interrupt to a caller.
- **`response_format`** — refused rather than absent. An agent returns to a real
  caller who may well want JSON, and there is nowhere to ask for that yet
  because it changes what a run *returns*: the result, the service's response
  body and streaming all have a stake in it.

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

```python
from langchain_core.tools import tool


@tool
def http_fetch(url: str) -> str:
    """Fetch a URL and return the body as text. Use when a task names a page
    the workspace does not already hold."""
    ...


TOOLS = [http_fetch]
```

Three reasons to write one:

- **Something the built-in set cannot do at all.** The clearest reason, and the
  shape above.
- **Making an existing capability narrower.** `execute` can already reach a
  database, but it can reach everything else too. A tool states the reach in
  code, so a request can activate `sql_query` and *not* the shell.
- **A tool that outgrew one file.** Two tools sharing a notion of what a column
  is, as a package: `tools/csv_profile/__init__.py` defines `TOOLS`, and the
  modules beside it are imported relatively. Only the package is scanned; a
  helper module inside it stays a helper.

The docstring is not decoration — it is what the model reads when deciding
whether to call the tool, exactly like a skill's `description`. Write it as a
trigger condition and say what the arguments mean in a caller's words.

#### The decorator is optional

`@tool` makes a `BaseTool`. A plain function works too, and `TOOLS` may hold
either:

```python
def line_count(path: str) -> str:
    """Count the lines in a text file. Use before reading a file you expect to
    be long, so you can ask `read_file` for the part you want."""
    ...


TOOLS = [line_count]
```

Kingfisher names a tool by `.name` where there is one and by `__name__` where
there is not, so everything downstream — a grant, an allowlist, the failure
guard — keys on the same string either way. The shipped `line_count` is written
this way on purpose.

**You give up none of what the model sees.** The docstring becomes the
description and the annotations become the argument schema, exactly as they do
under the decorator. What `@tool` buys is *control* over those: a name that is
not the function's, a description that is not the docstring, and its other
options.

**What you give up is on your side of the fence.** A plain function is not a
`BaseTool`, so your own tests call it rather than `.invoke` it, and there is no
`.args` to read back. For most tools that is simpler; for one whose schema you
want to assert, reach for the decorator.

#### Or a class, when you want to declare the schema

The third shape, and the one to reach for when the arguments deserve describing
one by one, or when the tool needs state or an `_arun`:

```python
class Shout(BaseTool):
    name: str = "shout"
    description: str = "Return the text in capitals. Use when asked to shout."
    args_schema: Type[BaseModel] = ShoutInput

    def _run(self, text: str) -> str:
        return text.upper()


TOOLS = [Shout()]
```

**Note the `()`.** `TOOLS` holds tools, not classes, and the class is the one
mistake here that used to produce a *successful* wrong answer: it loaded, was
offered to the model under the class name `Shout` rather than the `shout` it
declares — on a pydantic model that field is not a class attribute — and calling
it built a new instance and handed that back as the result. It is refused now,
naming what to write:

```
shout.py: TOOLS names the class 'Shout' rather than a tool -- write Shout() to
build one. A class loads and is offered to the model, and calling it returns a
new instance as if it were an answer
```

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

- **A folder reaches a name only when it has to.** A tool is named by itself,
  so `research/find_company.py` offers `find_company` and a request grants it by
  that name. Moving a file between folders changes nothing a caller types.
  `--list` says where each one came from.

  The exception is two files defining one name — see below. Then the bare name
  is refused and the file is what tells them apart.
- **Skills go one folder deep, not many.** See below — the difference is real
  and worth knowing before you try.

### A module with no `TOOLS` is an error

Not a skipped file. Quietly offering fewer tools than the workspace defines is
the failure `CapabilityError` exists to prevent one layer down — a request names
a tool, the name is not there, and nothing says the file that should have
supplied it was read and ignored:

```
research/find_company.py: must define TOOLS, the tools it contributes
```

Files whose names begin with `_` are helpers and are never modules of the
catalogue, which is how a loose file keeps something private without needing a
folder. `SUBAGENTS` works the same way for a Python-declared subagent.

### Two tools with the same name

Vendors do not coordinate. Two folders may each define a `fetch`, and both load
— the catalogue used to refuse the pair, which stopped the deployment over a
clash and was unfixable by anyone who owned neither file.

A **bare name is refused** once two files offer it, because an agent dispatches
by name and would otherwise run whichever the writer did not mean:

```
this request names tool 'fetch', which more than one source offers -- naming it
alone would silently pick one: write vendor_a/fetch.py::fetch,
vendor_b/fetch.py::fetch
```

The reference is the same `file::name` a subagent's `tools:` already used, and
it now *selects* rather than merely being checked.

**A request's grant says what the run may draw on, not what the agent carries.**
Those were the same list until a name could mean two tools. The agent takes
everything granted except names more than one file defines, and each such pair
goes to whichever delegate names one:

```yaml
# subagents/agent_a.yaml
tools: [vendor_a/fetch.py::fetch]
```

Both delegates then have a `fetch`, and each calls its own vendor's. The model
sees a flat `fetch` in every agent and never sees a reference — a tool name goes
to the provider as an identifier, so the qualifier resolves before any schema is
built.

The agent holding the grant is told what it could not take:

```
[delegate_only] 1 tool name(s) more than one file defines, so this agent holds
none of them -- a subagent that names one gets it: fetch
```

**A delegate still cannot exceed what the request was granted.** That ceiling is
unchanged: a request that withheld `execute` cannot have it handed back by a
definition that asks.

None of this appears in a workspace whose tool names are unique, which is most
of them. Renaming one of the two files is still the better answer for anyone who
controls both — this is for when nobody does.

Modules starting with `_` are still skipped, which is now mostly a way to park
a file you have not finished. Inside a package you do not need it.

Seven things the loader will refuse, all for the same reason: an agent quietly
holding different tools than the workspace defines is worse than a run that
stops.

| Refused | Why |
| --- | --- |
| A module with no `TOOLS` | Scanning for callables would guess at intent |
| An entry that is not a tool | `TOOLS = ["line_count"]` — the *name* of the tool, where the tool goes, by analogy with every other format here, which is data. It was offered to the model under the name `'line_count'`, quotes and all, and the build died later naming no file |
| A class where an instance was meant | `TOOLS = [Shout]` for `[Shout()]`. See [the note below](#or-a-class-when-you-want-to-declare-the-schema) — the one mistake here that produced a *successful* wrong answer |
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
A definition at `subagents/analysis/profiler.yaml` is still activated as
`profiler` — the `name:` field is the identity and the path is not, which was
already true of the filename.

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
does real work: the shipped `extractor` writes `tools: []`, because
"read-only"
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

Omit it and the delegate runs whatever *summoned* it — the main agent, or the
delegate that called this one. That is the usual case, and `reviewer` is the
shipped example of it: re-checking a figure is work for the same model that
produced it, whichever one that turned out to be.

A delegate that names a model and ends up on the caller's anyway is reported in
the run — `indistinct` names it, and the two crude cases it can see are the same
model as the deployment's default and a different id on the same host. Reported,
never refused: kingfisher cannot know that a delegate *needs* to differ, and
`reviewer` deliberately runs on the same model and is right to.

There was a `distinct: true` for saying it did need to differ, which turned that
report into a refusal. It went with `second-opinion`, its only user.

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

It fails only for the delegate you *activated* — a subagent is wired only when a
request names it, so seeding one you cannot run costs nothing until you ask for
it. And `run_on` can rescue it without editing the file, which is why this is
not checked across the whole catalogue up front: the refusal would fire before
the override could apply.

Which is why **a definition somebody else wrote should not carry a `model:`
line at all.** A file you install cannot portably name a vendor's model id:
`extractor` said `MiniMax-M2.5` and would refuse to start for anyone without a
MiniMax entry. Every shipped definition names nothing and says in a comment what
to pin it to, which is the only form that both works on a fresh seed and admits
that a preference was intended.

There was an `alias:` for saying it portably — a general name each deployment
bound under `aliases:` in `models.yaml` — and it is gone. Two spellings of one
question is one more than the format needs, and a reader could not tell which
kind of claim `cheap` was without opening another file.

The candidate *list* went with it. A list meant "try these in order", and the
only thing that ever passed one over was an alias a deployment had not bound; a
model this deployment cannot run refuses on the spot, and always did. So every
entry after the first was unreachable, and `model:` takes one name.

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
| `model` | optional | One entry in your `models.yaml`. The endpoint follows from it; this is where cost routing goes. Omitted, the delegate runs whatever summoned it |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |
| `groups` | optional | Who may reach this delegate, wherever it is used. Unset means everyone. Also the default audience, and the ceiling, for its `tools`, `subagents` and `skills` entries — see [Access](#access--groups-in-the-definitions-groupsyaml-for-the-vocabulary) |

### A delegate that consults another

A delegate can hit a question of a different kind mid-job — `reviewer` doubting
one figure, when checking figures is not what it is for. It can ask for help:

One more line in `reviewer.yaml`:

```yaml
subagents: [second-opinion]
```

And the delegate it names, which is an ordinary definition — the only thing
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
is checked over the whole catalogue when the definitions load, not per request,
because a set of files is either coherent or it is not:

```
subagents reach themselves: reviewer -> second-opinion -> reviewer. Delegation
nests to any depth, so a loop would build without end -- one of these has to
stop naming the next
```

The message names the whole loop rather than one edge of it, because one edge
does not say which link to cut and whoever reads it may own none of the files.

A definition may appear in several places — two delegates may both consult the
same `checker` — and reaching one twice is not a loop. Each is built once for
each position it occupies rather than once per route to it, so a wide catalogue
costs what it has, not what it can describe.

Depth costs you nothing to *declare*. It costs on every axis that matters at
run time, which is the next paragraph.

**What it costs.** Every level is a real conversation with a real model. A
helper's tokens are on your bill and in the run log, attributed to it by name,
and its work streams into the terminal under `[second-opinion]` — so this is
visible rather than merely charged.

Three reasons to reach for one, one example each:

- **Independence.** A second agent that recomputes a claim without seeing how
  the first one got there catches errors that re-reading your own work does
  not. The `reviewer` above is this one.
- **Context isolation.** One that reads a large pile of files and returns a
  short answer, so the bulk stays in its context rather than yours. Give it a
  narrow `builtin_tools` and a cheap `model:`.
- **A different model.** Two models from one family share failure modes, so a
  second opinion is worth nothing until you give it a `model:` that is genuinely
  different — and a `provider:` if that model lives somewhere else.

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

### Two subagents with the same name

Kept, both of them, and qualified — the opposite of the agent rule above and for
the reason given there: a delegate *is* selected from a set, so a caller who
means one can say which.

```
subagents/surveyor.yaml            name: surveyor
subagents/team/surveyor.yaml       name: surveyor
```

`kingfisher list` then shows them as `surveyor.yaml::surveyor` and
`team/surveyor.yaml::surveyor`, and that is what a grant writes. Where a name is
its own — which is every catalogue with no clash — the bare name is the key and
nothing changes.

The refusal moved to where the constraint actually lives: an agent's roster is
keyed by name, so an *agent* granted two of a name is refused. Two definitions
sitting in one catalogue that no single agent ever holds together are not a
conflict, and refusing them stopped deployments over a clash nobody had asked
for — unfixable by anyone who owned neither file.

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
        "build": _build,
    }
]
```

`build` is called with the model this delegate resolved to and the tool objects
the request actually granted, and must return something runnable. `kingfisher
seed` ships `show_your_work.py` as a worked example: it answers, then appends a
record of the tool calls that actually happened — computed from the transcript,
not asked of the model.

**Whatever your last node emits is what the caller gets, and only that.**
deepagents returns a delegate's result by walking back to the last `AIMessage`
with non-empty text. So a node that appends a footer as its own message does not
add to the answer — it *replaces* it, and the reply is lost. Emit one message
carrying both.

**Reach for this when a definition cannot say what you mean.** The example above
guarantees an ordering: there is no edge from the start to the model that does
not pass through the survey node. A prompt can *ask* for that step and a model
may skip it — occasionally, and most often on the input where skipping costs the
most. That is a real reason. "The same delegate, in Python" is not: a document
is reviewable by people who do not read Python, and it gets four fields this
format has to refuse.

**Five fields are refused, each because it would do nothing.**

| field | why not |
|---|---|
| `system_prompt` | the graph brings its own; write the prompt where the graph is built |
| `builtin_tools` | deepagents' own tools do not exist as objects when a delegate is assembled |
| `skills` | deepagents mounts skills for a delegate *it* builds, never for a compiled one |
| `middleware` | middleware wraps a graph deepagents builds; this one is already built |
| `subagents` | delegation arrives through middleware, which a compiled graph is not given |

`name`, `description`, `build`, `tools`, `model` and `metadata` are what
remain.

**A tool grant is not a limit here.** deepagents runs the graph as given and
never applies kingfisher's allowlist to it, so `--tools` narrows what `build`
*receives* and nothing stops the graph calling something it closed over.
`kingfisher list` marks these `[compiled]` and says so underneath.

**Defer the heavy imports into `build`.** Every `.py` file under `subagents/` is
imported whenever the catalogue is read — `kingfisher list` included — and
`from langchain.agents import create_agent` costs about 370 ms. At module scope
that is paid on every listing, for a delegate the request may never activate.

**A module with no `SUBAGENTS` is an error**, not a skipped file, for the reason
a tool module without `TOOLS` is: quietly offering fewer delegates than the
workspace defines is worse than saying so.

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
    skills/
      redaction/SKILL.md
```

That is the whole rule: **a folder is a bundle when it holds a definition whose
`name` matches the folder.** A folder that names no definition is ordinary
grouping and stays exactly what it was — `subagents/analysis/profiler.yaml` is
unchanged by any of this. Both shapes ship, side by side, in the definitions
`kingfisher seed` writes.

**Why you would.** An agent that omits `tools:` gets *every* tool the catalogue
holds, so anything in `tools/` is something the top-level agent can call. A
bundle is the only place a capability can sit that it cannot. It is how a
delegate comes to be trusted with something its caller is not.

**The delegate holds them whatever the request granted.** Activating `redactor`
is what grants its parts — a caller never names them, which is the point, since
naming them would mean knowing they exist. `--without-subagents redactor`
declines the whole delegate; there is no finer lever, deliberately.

**They come automatically.** `redactor.yaml` above writes no `tools:` and no
`skills:` line, and still holds both. The file being in the folder is the
declaration; listing it again in the definition would be a second place to keep
in step with the first. A `tools:` line still governs which *catalogue* tools it
also gets.

**A bundle wins a name the catalogue also uses.** If `redactor/tools/` defines a
`fetch` and so does `tools/`, the delegate gets its own — permanently, whatever
the catalogue grows later. `kingfisher list` prints that as
`fetch  [private tool, shadowing the catalogue's]`, because shadowing is only
acceptable while it is visible.

**One definition per bundle folder.** `redactor/helper.yaml` beside
`redactor/redactor.yaml` is refused: whether `helper` is inside the bundle has
no honest answer, and the two answers differ in what `helper` may call.

**`tools` and `skills` are reserved directory names** anywhere under
`subagents/`, so a skill's own `config.yaml` is never read as a subagent
definition. A grouping folder cannot be called either of them.

**What a listing shows.** Private assets appear indented under their owner:

```
subagents
  redactor  (redactor/redactor.yaml) — Quotes from files that may contain credentials…
      mask_secrets  [private tool]
      redaction  [private skill]
```

**Two limits, stated rather than discovered.**

*A private skill is unadvertised, not unreadable.* Skills are mounted read-only
under `/skills/`, and anything holding `read_file` can open anything there —
which is already true of every skill a request did not activate. What a bundle
buys for a skill is that no other delegate is *told* about it. A private tool is
stronger: an ungranted tool is never bound into an agent, so it cannot be
called at all. Do not put in a bundled skill anything you would mind another
delegate reading.

*A bundled skill's scripts are not runnable.* A skill's scripts are executed by
the shell against `$KINGFISHER_SKILLS`, and the sandbox grants the shell the
skills catalogue only. A bundle sits under `subagents/`, so its skills can be
read and listed but not run. Keep executable skills in the shared catalogue.

**A caller cannot upload one.** Uploads may carry a skill or a subagent, never a
tool — a bundle holds code that runs in this process, so bundles come from the
deployment's catalogue and nowhere else.

---

## Skills — `/skills/<name>/SKILL.md`, or one folder deep

deepagents' format, unchanged. A directory per skill, `SKILL.md` with `name` and
`description` in frontmatter and the procedure in the body.

**One folder of grouping, and no more** — where tools and subagents nest as deep
as you like. The difference is who reads them: those are walked by kingfisher, a
skill is read by the *agent* through a filesystem route, and deepagents lists a
source one level deep and looks for `SKILL.md` directly inside each entry. It
does not go further. So each folder is registered as its own source, which buys
exactly one level:

```
skills/code-review/SKILL.md            -> code-review
skills/research/lookup/SKILL.md        -> research::lookup
skills/research/deep/lookup/SKILL.md   -> unreachable
```

`--list` reports anything hiding below that, because the alternative is a
catalogue that simply looks empty.

### Two skills with the same name

A folder is a *source*, and a skill's full identity is `source::name` — the same
spelling a subagent's `tools:` uses for a tool in a package. This is what lets a
vendor pack and a team's own folder both ship a `lookup` without one replacing
the other, which is what a catalogue assembled from several parties looks like
after long enough.

**A bare name stays legal wherever it is unique**, which today is everywhere.
The qualifier is only required once two sources offer the same name, and then it
is *required* rather than guessed:

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

Two shapes:

- **Single file.** `skills/<name>/SKILL.md` and nothing else. The common one.
- **A `reference/` file the body points to,** read on demand. Use this when the
  detail is long and most tasks will not need it: `SKILL.md` stays short enough
  that reading it is cheap, and the reference is fetched only when it applies.

A skill the agent declines to read is not a failure. If the task did not warrant
it, not loading it is the mechanism working.
---

## Access — `groups:` in the definitions, `groups.yaml` for the vocabulary

There is a worked set to read alongside this section: `examples/groups.yaml`,
`examples/agents/analyst.yaml` and `examples/subagents/auditor.yaml`. Between
them they show a vocabulary with a containing group, a definition where only the
restricted entry carries an audience, and a delegate that runs with fewer tools
for a narrower caller.

`seed` leaves those three behind by default, the way it leaves a definition
naming middleware behind: a workspace that has not declared `analysts` cannot
read a definition that names it. Copy `groups.yaml` first, then
`kingfisher seed --all`.

Which user groups may reach which agents, delegates, tools and skills. Optional:
with no `groups.yaml`, kingfisher controls nothing by group and behaves exactly
as it did before this existed.

**Audiences live in the definitions.** An agent or a subagent says who may reach
*it*, and may say who reaches each thing it holds. What is central is only the
vocabulary — which names exist, and which contain which — and that file holds no
policy at all.

```yaml
# groups.yaml  — the whole file
groups:
  A: {}
  B: {}
  C: {}
  admin: {contains: [A, B, C]}
```

```yaml
# agents/assistant.yaml
name: assistant
description: Answers questions about the data in this workspace.
groups: [A, B]                 # who may open a session on this agent
tools:
  sql_query:
    groups: [A]                # this agent's sql_query is for A only
  http_fetch:
    groups: [A, B]
subagents:
  reviewer:
    groups: [A]
skills: [code-review]          # a plain list means "these, at my audience"
system_prompt: |
  ...
```

`<workspace>/groups.yaml` by default; `KINGFISHER_GROUPS_FILE` points elsewhere,
so several deployments can share one vocabulary. Read once at startup — a
revocation lands on restart, the way every other deployment setting here does. A
file that is present and will not parse stops the deployment: a vocabulary that
cannot be honoured must never come up as no vocabulary, because then no
definition's audience can be checked at all.

### Who is calling

Once a vocabulary exists, every call has to say:

```python
kf = Kingfisher(config_from_env())
caller = kf.for_groups(["B", "C"])     # bind once, reuse
caller.run(Request(task="...", agent="assistant"))
```

`kf.run(...)` with nobody named is **refused**. That refusal is the point: the
dangerous failure is a handler that forgot the boundary, and without it that
handler would serve every caller everything with nothing to show for it. To run
with no caller deliberately, say so:

```python
kf.for_groups(UNSCOPED).run(...)       # a value someone typed, and greppable
```

It takes group *names*, never a `Capabilities`. A name is resolved against the
definitions this deployment wrote, so the only thing anyone can hand in is an
input — there is no spelling of "give me everything" except `UNSCOPED`.

### The four spellings, and one rule

`groups:` on a definition is the **default audience** for everything it holds,
and the **ceiling** on what any entry may say. That one rule covers every form
the selection fields already had, so a definition written before audiences
existed keeps its exact meaning once a `groups:` line is added above it:

```yaml
groups: [A, B]
tools:                     # omitted — every tool, for A and B
```
```yaml
groups: [A, B]
tools: [sql_query]         # a list — that tool, for A and B
```
```yaml
groups: [A, B]
tools:                     # a mapping — per entry
  sql_query:
    groups: [A]            #   for A only
  http_fetch:
    groups: ["*"]          #   for anyone who reaches this definition
```

An entry is a mapping with a `groups:` line, not a bare list. The same word the
definition's own line uses, meaning the same thing one level down — so an entry
says which fact it is stating, has somewhere to put a second one later, and can
have a mistyped key refused. `sql_query: [A]` is refused by name, showing the
form to write: two spellings of one thing is what this format keeps deleting,
and the long one is worth having only while it is the only one.

**Only the entries you restrict need one.** An entry that says nothing inherits
the definition's own audience, so an agent holding five tools and restricting one
writes one `groups:` line rather than five:

```yaml
groups: [A, B]
tools:
  sql_query:
    groups: [A]            # narrower than the definition
  http_fetch:              # says nothing, so [A, B] — the definition's own
  line_count:
```

A mapping where nothing is restricted means exactly what the plain list means,
which is what keeps the two spellings honest about an unrestricted name.

`groups: []` on an entry is refused rather than read as "nobody": leaving the
line out is how you say "no restriction", so an empty one is an unfinished edit.
```yaml
groups: [A, B]
tools: []                  # none, as it always meant
```

An entry naming a group the definition itself does not admit is **refused**:

```
assistant.yaml: tools entry 'sql_query' is for C, but this definition is only
reachable by A, B -- so that line never reaches anyone
```

That is dead policy rather than a narrowing — nobody reaching `assistant` is
ever in `C` — and it is almost always a group name typed from memory.

**Any overlap grants.** A longer list means *more* people, which is what
everyone reads an access list as meaning. `["*"]` is everyone.

### Which fields take an audience

`tools`, `subagents` and `skills` — all three, and identically:

```yaml
groups: [A, B]
tools:
  sql_query:
    groups: [A]
subagents:
  reviewer:
    groups: [A]
skills:
  audit:
    groups: [A]           # only A is told this procedure exists
  review:
    groups: [A, B]
```

A skill reaches the model by a different road from a tool — it is advertised
through a middleware rather than registered as a callable — but the rule is the
same, and a skill out of reach is not advertised at all.

Note what that is and is not, because a skill was never a boundary: removing it
means the agent is not *told* about it. The file is still on disk, so this is
guidance, and an agent holding `execute` can read anything. The boundary is the
tools a skill's procedure would need, which have audiences of their own.

**`builtin_tools` deliberately takes none.** deepagents registers its own tools
itself, so kingfisher can filter them but never leave them out of a graph —
`infrastructure.harness.narrowing` records a live run where a model called
`execute` from memory. Writing a mapping there is refused rather than parsed,
because three sibling fields take one and reading it as a single tool named
`{'execute': ['A']}` is exactly the silent wrong answer this format refuses
everywhere else.

What gates the built-ins is which *agents* a group may open: an agent declaring
`builtin_tools: [read_file, ls, glob, grep]` cannot yield the shell to anyone,
whatever they ask for.

### Audiences are per use-site

A tool's audience is a property of *this definition's use of it*, not of the
tool. `reviewer` may restrict `sql_query` to `[A]` while `analyst` opens it to
`[A, B]`, and both are correct — they are different contexts.

The cost is that there is no single line answering "who reaches `sql_query`?".
`kingfisher list` answers it instead, with a roll-up beside the per-definition
view, so a call site quietly wider than its neighbours is visible rather than
something you would only find by grepping.

### No `groups:` line means everyone

An absent optional field means no restriction, which is what it means everywhere
else in these formats — and reading it as "nobody" would stop every unannotated
definition working the moment `groups.yaml` appeared. So adoption is
incremental: annotate the sensitive definitions first.

That must not also be silent, so startup names what carries no line:

```
access:
  no groups: line, so reachable by everyone:
    agent assistant
    subagent extractor
```

### One grant, everywhere

The caller's groups bound the agent, its delegates, and their delegates alike. A
delegate's own `groups:` is its intrinsic ceiling — "this one is sensitive
wherever it is used" — and a parent's `subagents:` entry narrows it further for
that parent's context. The two intersect; neither widens the other.

So a delegate whose tools are partly out of reach **runs with the rest and
reports what was withheld**:

```yaml
# subagents/reviewer.yaml
name: reviewer
groups: [A, B, C]
tools:
  sql_query:
    groups: [A, B]
  http_fetch:
    groups: [A, B, C]
```

A caller in `C` gets a `reviewer` holding `http_fetch` and not `sql_query`.
Write delegate prompts to cope — *"if you can check the figure against the
database, do; if not, say so"* — because the caller decides, not the file.

**An ungranted tool never reaches the graph.** It is not attached and then
refused: `create_deep_agent` is handed only what the caller reaches, so the model
is never told it exists and never spends context on its schema. An ungranted
subagent is never compiled, so its graph is never paid for either.

### Compiled subagents

A Python-declared delegate may carry `groups` and per-tool audiences:

```python
SUBAGENTS = [
    {
        "name": "profiler",
        "description": "...",
        "groups": ["A", "B"],
        "tools": {"sql_query": {"groups": ["A"]}},
        "build": _build,
    }
]
```

`groups` is a real boundary: whether a compiled delegate is built at all is
kingfisher's decision, so there is nothing there for a graph to ignore.

The per-tool audience narrows what is **handed to** `build`, and carries exactly
the caveat the plain `tools` list already carries there: deepagents applies no
allowlist to a graph it did not build, so a `build` that ignored what it was
given could call anything it holds. `kingfisher list` marks compiled delegates
for that reason. `skills` and `subagents` are refused for a compiled delegate
regardless — see `NOT_COMPILED`.

### What a caller can see

**Out of reach reads as not offered.** An asset a caller's groups do not reach is
absent from what they are told: absent from listings, from the "this workspace
offers …" in a refusal, and from the report of what a run withheld. Naming one
gives the same answer naming a typo does. Nothing lets a caller enumerate the
catalogue by guessing, and nothing sends them to try something they will only be
refused for.

The operator's view is the whole of it:

```
$ kingfisher list                 # every definition, its audience, and a roll-up
$ kingfisher list --as B,C        # exactly what that caller sees
$ kingfisher list --as admin      # check `contains` before trusting it
```

`list` is exempt from the refusal above, and only `list`: it is read-only, and
whoever runs it is on the host with the definitions already in front of them.
Running a *turn* still has to say who is calling.

### Agents, and what a session does not let you keep

`agents` is checked where the *name* is resolved: when a session is opened, and
again on **every turn afterwards**.

That second half is the part worth knowing. A session pins its agent for life,
and a session id is a bearer credential — holding one is how a caller proves the
session is theirs. Checked only at the open, holding one would be a durable grant
to an agent you may not open, and a caller who lost a group would keep running
what they had before. So:

- A leaked session id grants nothing its holder could not open themselves.
- A demotion takes effect on the caller's next turn, and an in-flight
  conversation on an agent they can no longer reach stops being usable. That is
  intended, not a bug — it is the same answer as "you may not run this agent",
  arriving at the first moment it became true.

### The vocabulary is closed

A group named in a definition, or by a caller, that `groups.yaml` does not
declare is **refused**. Both directions matter and they fail differently:

- A caller naming an undeclared group would otherwise reach nothing, which looks
  exactly like a caller who was denied.
- A definition naming one would otherwise invent a group nobody is in, and the
  only symptom would be a tool quietly reachable by no one — found weeks later
  by whoever needed it.

`contains` expands one name into others, once, when the file is read:

```yaml
groups:
  admin: {contains: [A, B, C]}
```

A caller in `admin` reaches anything listing `A`, `B` or `C`, without `admin`
appearing on a single definition. Without it, a broad group has to be written on
every line and re-written on every line anyone adds. A loop is refused naming the
whole cycle rather than one edge — one edge does not tell a reader which link to
cut, and they may own none of the groups involved.

### Uploads are unchanged

A request may still bring its own subagent or skill. Those cannot escalate: an
uploaded definition is text the caller wrote, and it holds only the tools their
groups already reach — `middleware`, `endpoints` and `models` are never widened
by an upload. What it buys someone is new instructions, never new powers.

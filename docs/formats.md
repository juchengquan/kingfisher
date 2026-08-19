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
`kingfisher.assets` entry point so that anyone could publish a pack. A directory
covers the same ground without a wheel, a publish step or metadata, so the group
went; if a second publisher ever wants one it comes back.


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
alias: cheap
memory: false
system_prompt: |
  You survey files before anyone trusts them.

  Report what would change how somebody analyses this file, and say what you
  did not check. A survey that implies it was exhaustive is worse than one that
  names its own edges.
```

That is a whole definition: two required fields and whatever else you have an
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
    system_prompt        what this agent is. Yours, optional

There is no way to replace the first, and that is not a gap. An agent without it
is not leaner — it is one holding tools nobody told it about, discovering its
permissions by being denied. Opening a session returns what was assembled, which
is where to check rather than guess.

### The fields

| Field | | |
| --- | --- | --- |
| `name` | required | What a request names it by. Authoritative — the filename is not |
| `description` | required | Single line. Nothing reads it at run time; it is how somebody chooses between your agents in `kingfisher list` |
| `system_prompt` | optional | This agent's own instruction, added after the two documents above |
| `builtin_tools` | optional | deepagents' own set, listed in the tools table below. Unset means all of them; `[]` means none |
| `tools` | optional | The tools *your* workspace defines. Unset means all of them; `[]` means none |
| `skills` | optional | Which procedures it is told about. Unset grants **none**; write `["*"]` for every skill the workspace offers |
| `subagents` | optional | Delegates it may consult. Unset grants **none**; `["*"]` is every subagent the workspace offers |
| `middleware` | optional | Names entries from a registry the deployment supplies. The one field that selects *code*, so it is granted, never inherited |
| `model` | optional | An entry in your `models.yaml`. Unset runs the `default:` there. May be a list, tried in order |
| `alias` | optional | A general name your `models.yaml` binds. For an agent file that travels between deployments and cannot portably name a vendor's model id. Not with `model` |
| `memory` | optional | `false` to run without the memory file on a deployment that wired one |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |

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

**The agent itself has to work.** A model your catalogue does not define, or an
alias nobody bound, refuses. **Anything below it that cannot run is left out and
reported** — which is what lets a freshly seeded workspace run at all, since
`second-opinion` wants an `alternate` the example config deliberately leaves
unbound.

### Four fields are refused

Each with its own message rather than a generic "unknown field", because the
generic one reads as *not supported yet* and sends you looking for a workaround:

- **`distinct`** — there is nothing above an agent for it to differ from.
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

Which makes the two settings opposites, and they are. Say nothing and you match
your caller. Say `distinct: true` and you must not.

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
line.** A file you install cannot portably name a vendor's model id: `extractor`
said `MiniMax-M2.5` and would refuse to start for anyone without a MiniMax
entry. Say `alias:` instead and let each deployment bind it.

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
only when a request *activates* the delegate, so seeding definitions you have
not bound for still costs nothing until you use them.

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
| `model` | optional | An entry in your `models.yaml`. The endpoint follows from it; this is where cost routing goes. May be a list, tried in order |
| `alias` | optional | A general name your `models.yaml` binds to a model. For a definition that knows what *kind* of model it needs and cannot know its name. Not with `model`. May be a list, and an alias you never bound is passed over rather than fatal |
| `distinct` | optional | `true` when running on the same model as whatever summoned it defeats this delegate. Turns "ended up on the same model" from a note in the run report into a refusal, and is what makes a list of candidates worth writing |
| `metadata` | optional | A mapping of your own keys. Nothing in a run reads it — it is for whatever loads the catalogue |

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

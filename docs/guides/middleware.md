# Registering middleware

Middleware is code the deployment writes and a definition may only *name*. A
class goes in a dict handed to `Kingfisher`, an agent or subagent file writes
`middlewares: [that-name]`, and the two halves meet nowhere else.

The asymmetry is the design rather than a gap. A tool or a skill is read out of
the definitions folder, which the agent's shell can write to where no fence is on;
middleware wraps the agent that would be doing the writing. So a `middlewares/`
directory is one the agent's shell is denied, `seed` leaves behind any definition
naming middleware this deployment cannot build, and nothing a request carries
widens the axis — *Capabilities* in [`decisions.md`](../decisions.md) has the
rule.

This page is the deployment's half. What a definition may then write — the field,
the long form with `settings`, what is granted rather than inherited — is
[`formats.md`](formats.md). The shipped middleware is
`assets/middlewares/summarize_by_count.py`; the suite loads and runs it, so it
cannot rot quietly, and nothing here repeats it.

**A kingfisher middleware is a LangChain middleware.** The base class is
`langchain.agents.middleware.AgentMiddleware`, the hooks are LangChain's to
define, and the import in your file is `langchain`, not `kingfisher`. Which
hooks exist and what each one is handed is documented upstream; what deepagents
and LangChain were *measured* doing is [`findings.md`](../findings.md). Nothing
here wraps either.

## Registering

```python
from kingfisher import Kingfisher

from assets.middlewares.summarize_by_count import SummarizeByCount

kingfisher = Kingfisher(
    cfg,
    middlewares={
        "summarize-by-count": SummarizeByCount,
    },
)
```

The key is the name a definition writes. It is not the class name and does not
have to resemble it. Two keys over one class are two labels for one
configuration, because `defaults` lives on the class: a variant is a second class.

**A value is a class, or a callable taking no arguments.** A class is the shape
that can be configured: it is constructed per graph with its own `defaults` plus
whatever the definition was allowed to write. Anything else is called with
nothing — a lambda closing over the values it needs is a registry entry, and a
definition writing `settings` beside one is refused rather than built without
them.

Registering is not permitting. A name a request withheld is refused even though
the deployment registered it, and `Config.grants` is where a deployment says what its
definitions may reach by default.

## When it needs something YAML cannot hold

An HTTP client, a database connection, a metrics handle. None of them can be a
`settings:` value, and the answer is not a different naming scheme — it is a
**class that closes over the object**, registered in your own program:

```python
client = httpx.Client(base_url=...)          # yours, made once


class Traced(AgentMiddleware):
    defaults = {"level": "info"}
    yaml_settable = frozenset({"level"})

    def __init__(self, level: str) -> None:
        self._client = client                # captured, not configured
        self._level = level


kingfisher = Kingfisher(cfg, sessions=LocalSessions(), middlewares={"traced": Traced})
```

A definition still writes `settings: {level: debug}`, because it is a class and
`yaml_settable` still applies. Every graph gets its own middleware; every one of
them shares the client. A zero-argument factory closing over the same object
would work too, and costs you `settings:` — a factory has already chosen its
values, so a definition writing one is refused.

**A file in the definitions folder can make one of its own, and cannot share yours.** A
`middlewares/` module is imported once, when `Kingfisher` reads its catalogue, so
a client made at module level there is made once too. What it cannot reach is an
object your program already holds — the connection pool, the metrics handle the
rest of the process writes to. Put that where it is made: in the program that
constructs `Kingfisher`.

**And it is the wrong answer for anything this build decides.** A client is the
same object for every agent and every turn, which is why closing over one works.
The model an agent runs is not — the agent file may pin one, a subagent may pin
another, a request may override either — so a class closing over a model closes
over the deployment's *default* and runs that whatever the agent in front of it
was pinned to. The next section is for those.

## When it needs something this build decides

Four things are not the deployment's to make once: **the model this graph runs**,
**the backend it reads and writes through**, **the definition it was built
for**, and **the id the call running now will be recorded under**. A class asks
for them by name:

```python
class SummarizeByCount(SummarizationMiddleware):  # deepagents', not langchain's
    name = "summarize-by-count"
    wants = frozenset({"model", "backend"})

    def __init__(self, model, backend, messages: int) -> None:
        ...
```

`wants` is the third class attribute, beside `defaults` and `yaml_settable`, and
each name in it is filled in where the agent is assembled. `model` is what *this*
graph runs — the agent's own, a subagent's own, or whatever a subagent inherited
from the one that summoned it. `backend` is the filesystem the agent sees, rooted
at this session. `definition` is the `AgentSpec` or `SubagentSpec` this instance
belongs to. `result_id` is a function: called in `wrap_tool_call`, it returns the
id the call's result will be recorded under, such as `A-0042`, or `None` for a call
that never is — a file tool, `write_todos` — and wherever no ledger is kept.

**The id is taken before the call runs**, so a call that then fails leaves it
unused, and no other result is given it. A turn that dies before keeping its
ledger does hand its numbers out again, so write a file named after one where an
overwrite does no harm. Never write it in the tool results folder,
`/scratchpad/tool_results/` by default: a file there named like a result, which no
row names, is handed to `UnusedResults`, and the default deletes it.

**The set is not a closed list.** It is whatever the two build sites hold, and
both hold the same keys so a `middlewares:` line means the same thing in an agent
file and a subagent file. A want nothing there answers to is refused when the
agent is built, listing what this build does provide — which is the listing to
read, because there is no constant anywhere to look up instead.

**A wanted key is a name in yaml and an object in Python.** A class may list
`model` in `yaml_settable` as well as in `wants`, which is not a contradiction:

```yaml
middlewares:
  - name: my-summarizer
    settings: {model: MiniMax-M2.5}
```

`MiniMax-M2.5` is resolved through the same catalogue a subagent's `model:` field
goes through — `models.yaml`, the profile's params, the endpoint's `base_url` —
and an endpoint this request may not reach is refused before a prompt is sent
anywhere. Write nothing and the fallback applies, which is the model the agent is
already running. A `defaults` entry may name one too, which is how a deployment
pins the model in its own code and opens nothing.

**It is a key of `models.yaml`, not a nickname for one.** The key there *is* the
id sent on the wire — `Models` refuses a table where the two differ — so what goes
here is `MiniMax-M2.5` or `gpt-5`, whatever your catalogue defines, and never a
label like `cheap` chosen for this line.

`backend`, `definition` and `result_id` cannot be written, because there is no
name a file could carry for them. A class naming one in `defaults` or `yaml_settable` is
refused, whether or not any definition ever writes it.

**A middleware still imports nothing from kingfisher.** What it receives is a
LangChain chat model, a deepagents backend, a frozen spec and a function returning
a string.
`summarize_by_count.py` imports deepagents' summarizer and nothing from kingfisher.

## Or put it in the definitions folder

`middlewares/` is a definition kind, so a deployment that does not need to wire
anything in Python can write a file instead and `kingfisher seed` will copy it:

```python
from langchain.agents.middleware import AgentMiddleware


class Audit(AgentMiddleware):
    name = "audit"
    ...


MIDDLEWARES = [Audit]
```

`MIDDLEWARES` is declared rather than inferred, exactly as `TOOLS` is, so a class
imported to build a variant is not offered as a second entry nobody meant to
expose.

**The name comes from the class.** `AgentMiddleware.name` is a property that
answers the class's own name, so `Audit` is selected as `Audit` unless the
class overrides it with a string, as this one does.

**Classes only, here.** A registry may hold a zero-argument factory; a file may
not, and the difference is not tidiness. A file is imported once, so an object
in `MIDDLEWARES` would be built once and shared by every graph in the process —
the state leak that makes a cap stop capping. A class in a file does everything
a factory does and takes settings besides.

**Both sources answer one field**, and a name in both is refused rather than
resolved: a definition names one, and nothing would say which it got. The code
registry is not going away — a middleware closing over a live object, a
connection or a metrics client, cannot be a file in a directory.

**Middleware in the definitions folder is not the caller's.** A definition still only names,
nothing a request carries can widen `middlewares`, and the file is put there by whoever
administers the definitions folder. What makes this safe at all is that the definition
roots are denied to the agent's shell — `decisions.md`, under *Confining the
shell*. Before that, a `middlewares/` directory would have been a cap the capped
thing could rewrite.

## What a definition may configure

Two class attributes, and neither is required — `wants` above is the third and is
the harness's half rather than a definition's:

```python
class SummarizeByCount(SummarizationMiddleware):
    defaults: ClassVar[dict[str, Any]] = {"messages": 60}
    yaml_settable: ClassVar[frozenset[str]] = frozenset({"messages"})
```

`defaults` is the deployment's half and applies whole when a definition writes no
settings at all, so a bare `middlewares: [summarize-by-count]` is a working line
rather than a no-op. `yaml_settable` is the whitelist of keys a definition may
override. The merge is per key and deployment first: a definition that writes one
key leaves every other at its default. A class naming no `yaml_settable` is a
class no definition may configure, which is the default.

Which keys belong in that whitelist is a judgment with a test behind it: not
whether a key looks harmless but whether *more* or *different* is a failure mode.
`messages` passes. deepagents' own trigger still summarizes by size whatever
count is written, so a definition choosing a lower one chooses more summaries, for
its own agent and nobody else's.

**A definition that writes `["*"]` gets everything registered and granted.** That
is worth knowing before adding an entry to the dict: registering a new middleware
attaches it to every definition holding a star, and no file mentions it. A file
in the definitions folder's `middlewares/` counts as registered, and `kingfisher
seed` copies the shipped one there — so on a seeded deployment a star takes it and
whatever is added later, which is why no shipped definition writes one. A star also
resolves quietly smaller when a request narrows the axis, where a *named* entry
refuses — so a definition that must have its audit hook should name it.

### A limit a definition can set is no limit

A key that bounds what a definition may spend fails the whitelist's test: more of it
is the failure. It goes in `defaults` and stays out of `yaml_settable`:

```python
class CallCap(AgentMiddleware):
    name = "call-cap"
    defaults: ClassVar[dict[str, Any]] = {"limit": 20}
    yaml_settable: ClassVar[frozenset[str]] = frozenset()  # `limit` is the deployment's


class LooseCallCap(CallCap):
    name = "call-cap-loose"
    defaults: ClassVar[dict[str, Any]] = {"limit": 100}
```

Opened, the first definition wanting more than twenty calls would write
`{limit: 1000000}` and be within its rights, because the format let it. A
sampling rate that can be set to zero, a log destination that can be pointed off
the machine and a retention window that can be shortened are the same shape, and
none reads as dangerous in a single file: what the setting costs is paid by
whoever runs the definition, not whoever wrote it. A definition writing a shut key
is refused, told what the entry does accept.

A second ceiling is a second class, a subclass overriding `defaults` under its own
`name`, and a definition chooses between them by name. That keeps the decision in
code, and it narrows like every capability: a request may withhold
`call-cap-loose` and leave `call-cap` in reach, which a number in a mapping could
not say.

## Write the sync hook

kingfisher runs a turn with `graph.stream`, so the hook that runs is the sync one
— `wrap_tool_call`, `wrap_model_call`, `before_model` — and never its `a`-prefixed
twin. A middleware written only as `awrap_tool_call` fails loudly: the turn raises
the first time it reaches that hook, rather than skipping it.

**`Kingfisher.astream` and `arun` are the exception, and they invert it.** Those
drive `graph.astream`, so on that path the `a`-prefixed hook is the one that runs
and the sync one never does. A middleware with only `wrap_model_call` raises the
first time an `astream` turn reaches it — loudly, with a message naming the fix.

So which hook runs depends on which entry point the caller used, and the remedy
is the same either way: **write both halves.** A middleware that
implements only one works under one of the two entry points and refuses the
other, which is a deployment-shaped surprise rather than a bug you can find by
reading the class.

A subclass fails quietly. A LangChain middleware that implements both halves —
`SummarizationMiddleware` is one — keeps its own sync hook when a subclass
overrides only `abefore_model`: the subclass is installed, the turn runs, and none
of the subclass's code does. That is the failure worth guarding hardest against
here, because what it produces is not an error. Override the sync hook whatever
else you override.

## Names deepagents already uses

`create_deep_agent` does not append what it is handed. It merges by name, and
`AgentMiddleware.name` defaults to the class name — so a class of yours called
`FilesystemMiddleware` **replaces** deepagents' own rather than running beside
it. The registry key is a separate string and is not involved: nothing on either
side of that would otherwise mention it. `findings.md` records the upstream
behaviour, including the asymmetry that the exclusion path refuses to strip these
and the custom path replaces them without a word.

Two of them deepagents will not run without: **`FilesystemMiddleware`** and
**`SubAgentMiddleware`**. Replacing either is a deployment's business — a fence
of one's own over the filesystem is a reasonable thing to build — but an
unreplaced `FilesystemMiddleware` is what backs every built-in file tool and
enforces the `permissions` rules, so the substitution has to do that job too.

Kingfisher warns rather than refuses, once, at build time. If you meant it there
is nothing to do; if you did not, rename the class and leave the registry key
alone.

`summarize_by_count.py` means it. deepagents' summarizer records what it summarized
as an index into the conversation, so a second summarizer beside it -- one that
rewrites the conversation, or another deepagents one keeping its own index -- leaves
the model looking at the wrong messages. `SummarizeByCount` takes that summarizer's
place instead: the class keeps `summarize-by-count` for definitions to write, and the
instance answers to `SummarizationMiddleware`, which is the name deepagents merges by.

**What kingfisher keeps below the summarizer follows yours.** The PDFs a turn may hold
open are half of where the graph's summarizer fires, and clearing starts at half of it
unless the model entry sets `clear_results_at`. Both are read off the summarizer that
answers to `SummarizationMiddleware` on that graph -- yours, where a definition names it,
and deepagents' own elsewhere. A token count is taken as written, a fraction of the
window as that share of it, several clauses by the earliest that can fire; a clause
counting messages alone fires at any size, and is left out. So a summarizer firing at
100,000 tokens holds a turn to about 146 KB of PDF and starts clearing at 50,000, and a
`clear_results_at` at or past 100,000 is refused when the agent is built. A summarizer
kingfisher cannot read -- neither deepagents' nor langchain's, or one counting only
messages -- is sized as deepagents' own, and the notice it is built with says so.

**Clearing counts as your summarizer counts.** Its `token_counter` is read off the same
summarizer, so calibrating it against what your gateway reports moves the trigger and
where clearing starts together: a counter that counts twice the estimate clears at half
the conversation it did. Pass it to deepagents' summarizer as its own keyword, in a
class taking that summarizer's place as `SummarizeByCount` does. It is called on every
model call, and past the mark on each message a pass may clear as well, so a counter
asking the gateway over the network makes that many requests: a calibrated
`chars_per_token` on `count_tokens_approximately` makes none. Left alone,
deepagents' own counts as langchain's default does, which on `ChatAnthropic` is 3.3
characters a token, scaled by the usage the provider last reported.

## What your middleware sees

**Already-narrowed capabilities.** A deployment's middleware runs after the
narrowing kingfisher applies, so it observes the tool set the request and the
definition actually left in reach rather than what the definition asked for.

**Host paths, not the paths the model wrote.** Running after kingfisher's own puts
your hook below the translation, so `wrap_tool_call` on an offered tool is handed
`/Users/.../sessions/s/inputs/n.txt` where the model wrote `/inputs/n.txt`. An audit
log fills with host paths, and a matcher looking for `/outputs/` never matches.
Translate back against the session's own root, or match on the tail.

**An exception from your hook ends the turn, whichever tool was called.** Raise in
`wrap_tool_call` and the caller gets your exception -- on an offered tool as on a
built-in such as `read_file`. It is the loud answer deliberately: a failed result
would carry *the tool's* name, so your fault would read as the tool's while the turn
carried on with your rule not applied. Catch what your hook can raise, and where a
call should fail softly, return a failed `ToolMessage` rather than raising -- that is
what kingfisher's own guards hand back, and the model reads it as the call's result.

**One graph, not one turn.** The factory runs again for each graph built: the
agent's, each declared subagent's, and each general-purpose subagent's. A counter
counts that graph. `CallCap` is a per-turn cap only because the agent happens to
be built once per turn, and a limiter that has to bound a whole turn needs
somewhere to live that a graph does not have.

**Every graph, subagents included.** The general-purpose subagent deepagents
supplies is replaced by one carrying this deployment's middleware, so delegating
is not a way around an audit hook — otherwise the way to run unaudited would be
to ask for nothing in particular.

A subagent inherits none of its parent's middleware — each definition's is built
from its own `middlewares:` field. A portable subagent, which may not write one, runs
the main agent's instead, unless a `SubagentMiddlewares` says otherwise
([`ports.md`](ports.md#subagentmiddlewares--what-a-portable-subagent-runs)).

**So register the class, not an instance of it.** `middlewares={"audit": Audit}`,
never `{"audit": Audit()}`. Building it yourself hands every graph the same
object, and the state it accumulates is exactly what a counter or a rate limit
is for — `CallCap` would spend its budget on the first graph and refuse every
tool call afterwards, for the life of the process. A registry entry that cannot
be called is refused when `Kingfisher` is given it. A zero-argument factory is
the other accepted shape, for the entry whose values were decided when the
deployment wrote the lambda.

## What refuses, and when

Nothing here is discovered mid-run. The first fires earlier than the rest: it is
a fact about the registry rather than about any definition, so `Kingfisher`
refuses it as the registry arrives, before a definition has named anything. The
two about `middlewares/` files fire as that folder is read at startup, and raise
the loader's `ValueError` naming the file. The others are raised while the agent
is built, before the model is reached. All but those two raise `CapabilityError`.

| What happened | What it says |
|---|---|
| A registry entry that cannot be called — an already-built middleware, most often | `register the class itself, or a zero-argument factory returning one`, and why one shared object would be wrong |
| A `middlewares/` file exports something that is not an `AgentMiddleware` class | what `MIDDLEWARES` takes, refused as the directory is read rather than at the first turn |
| Two `middlewares/` files define one name | both files, and to rename a class or give one an explicit `name` |
| A name is both registered in code and defined in the definitions folder | both sources, and to rename the class or its registry key |
| A definition names middleware nothing registered | `names unregistered middleware`, with what this deployment did register |
| A definition names middleware the request withheld | `names middleware this request may not use` |
| A definition writes a setting outside `yaml_settable` | what that entry does accept, or that it accepts nothing at all |
| A definition writes settings for an entry registered as a factory | that only a registered *class* takes settings |
| A registered class needs an argument `defaults` does not cover | `could not build middleware`, naming the entry and what it was called with |
| A class `wants` something this build does not provide | what it wants and what is provided, since there is no list to look up instead |
| A class gives a wanted key a value, and that key is not written by name | that the harness hands it over whole, so a value there has nowhere to go |
| A definition names a model whose endpoint the request withheld | `resolves to endpoint ... which this request may not reach`, before anything is sent |
| A registered factory declares `wants` | that only a registered *class* is handed anything |

The last one is the deployment's mistake rather than the definition's, and it is
worded that way: every argument the class requires belongs in `defaults`.

`seed` is the one that reports rather than raises. A definition naming middleware
this deployment cannot build is left behind with its names printed; register them
and seed again with `--all`.

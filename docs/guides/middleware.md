# Registering middleware

Middleware is code the deployment writes and a definition may only *name*. A
class goes in a dict handed to `Kingfisher`, an agent or subagent file writes
`middleware: [that-name]`, and the two halves meet nowhere else.

The asymmetry is the design rather than a gap. A tool or a skill is read out of
the workspace, and the workspace is a directory the agent can write to;
middleware wraps the agent that would be doing the writing. So there is no
`middleware/` directory, `seed` leaves behind any definition naming middleware
this deployment cannot build, and an upload never widens the axis — *Capabilities*
in [`decisions.md`](../decisions.md) has the rule, and
`assets_examples/middleware/call_cap.py` has the long version.

This page is the deployment's half. What a definition may then write — the field,
the long form with `settings`, what is granted rather than inherited — is
[`formats.md`](formats.md). The two worked examples are
`assets_examples/middleware/call_cap.py` and `tool_note.py`, in that order; the
suite loads and runs both, so they cannot rot quietly, and nothing here repeats
them.

**A kingfisher middleware is a LangChain middleware.** The base class is
`langchain.agents.middleware.AgentMiddleware`, the hooks are LangChain's to
define, and the import in your file is `langchain`, not `kingfisher`. Which
hooks exist and what each one is handed is documented upstream; what deepagents
and LangChain were *measured* doing is [`findings.md`](../findings.md). Nothing
here wraps either.

## Registering

```python
from kingfisher import Kingfisher

from assets_examples.middleware.call_cap import CallCap, CallCapGenerous

kingfisher = Kingfisher(
    cfg,
    middleware={
        "call-cap-strict": CallCap,
        "call-cap-generous": CallCapGenerous,
    },
)
```

The key is the name a definition writes. It is not the class name and does not
have to resemble it — two keys over one class is a normal thing to register, and
`call_cap.py` argues for when that is the right shape.

**A value is a class, or a callable taking no arguments.** A class is the shape
that can be configured: it is constructed per graph with its own `defaults` plus
whatever the definition was allowed to write. Anything else is called with
nothing — a lambda closing over the values it needs is a registry entry, and a
definition writing `settings` beside one is refused rather than built without
them.

Registering is not permitting. A name a request withheld is refused even though
the deployment registered it, and `grants` is where a deployment says what its
definitions may reach by default.

## What a definition may configure

Two class attributes, and neither is required:

```python
class ToolNote(AgentMiddleware):
    defaults: ClassVar[dict[str, object]] = {"text": "", "max_length": 500}
    yaml_settable: ClassVar[frozenset[str]] = frozenset({"text"})
```

`defaults` is the deployment's half and applies whole when a definition writes no
settings at all, so `middleware: [tool-note]` is a working line rather than a
no-op. `yaml_settable` is the whitelist of keys a definition may override. The
merge is per key and deployment first: a definition that writes `text` leaves
`max_length` at the default. A class naming no `yaml_settable` is a class no
definition may configure, which is the default and is `CallCap` deliberately.

Which keys belong in that whitelist is a judgment with a test behind it, and
`tool_note.py` is the page for it: the question is not whether a key looks
harmless but whether *more* or *different* is a failure mode. A ceiling the
ceilinged thing can raise is not a ceiling.

**A definition that writes `["*"]` gets everything registered and granted.** That
is worth knowing before adding an entry to the dict: registering a new middleware
attaches it to every definition holding a star, and no file mentions it. A star
also resolves quietly smaller when a request narrows the axis, where a *named*
entry refuses — so a definition that must have its audit hook should name it.

## Write both paths

A hook implemented on one path only applies on one path only. `stream` and
`astream` are two loops over one turn, so a cap or a note or an audit record
written as `wrap_tool_call` and not `awrap_tool_call` is silently absent for
every caller who reached for the other entry point. Both examples implement both
and say so at the second one.

This is kingfisher's requirement rather than LangChain's, and it is the failure
worth guarding hardest against here, because what it produces is not an error —
it is a hook that appears to be installed and is not running.

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

## What your middleware sees

**Already-narrowed capabilities.** A deployment's middleware runs after the
narrowing kingfisher applies, so it observes the tool set the request and the
definition actually left in reach rather than what the definition asked for.

**One graph, not one turn.** The factory runs again for each graph built: the
agent's, each declared delegate's, and the general-purpose delegate's. A counter
counts that graph. `CallCap` is a per-turn cap only because the agent happens to
be built once per turn, and a limiter that has to bound a whole turn needs
somewhere to live that a graph does not have.

**Every graph, delegates included.** The general-purpose delegate deepagents
supplies is replaced by one carrying this deployment's middleware, so delegating
is not a way around an audit hook — otherwise the way to run unaudited would be
to ask for nothing in particular.

A delegate inherits none of its parent's middleware — each definition's is built
from its own `middleware:` field.

**So register the class, not an instance of it.** `middleware={"audit": Audit}`,
never `{"audit": Audit()}`. Building it yourself hands every graph the same
object, and the state it accumulates is exactly what a counter or a rate limit
is for — `CallCap` would spend its budget on the first graph and refuse every
tool call afterwards, for the life of the process. A registry entry that cannot
be called is refused when `Kingfisher` is given it. A zero-argument factory is
the other accepted shape, for the entry whose values were decided when the
deployment wrote the lambda.

## What refuses, and when

Everything below raises `CapabilityError`, and nothing here is discovered
mid-run. The first fires earlier than the rest: it is a fact about the registry
rather than about any definition, so `Kingfisher` refuses it as the registry
arrives, before a definition has named anything. The others are raised while the
agent is built, before the model is reached.

| What happened | What it says |
|---|---|
| A registry entry that cannot be called — an already-built middleware, most often | `register the class itself, or a zero-argument factory returning one`, and why one shared object would be wrong |
| A definition names middleware nothing registered | `names unregistered middleware`, with what this deployment did register |
| A definition names middleware the request withheld | `names middleware this request may not use` |
| A definition writes a setting outside `yaml_settable` | what that entry does accept, or that it accepts nothing at all |
| A definition writes settings for an entry registered as a factory | that only a registered *class* takes settings |
| A registered class needs an argument `defaults` does not cover | `could not build middleware`, naming the entry and what it was called with |

The last one is the deployment's mistake rather than the definition's, and it is
worded that way: every argument the class requires belongs in `defaults`.

`seed` is the one that reports rather than raises. A definition naming middleware
this workspace cannot build is left behind with its names printed; register them
and seed again with `--all`.

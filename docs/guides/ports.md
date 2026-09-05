# Writing an adapter

Kingfisher reaches the world through twelve Protocols in
[`domain/ports.py`](../../src/kingfisher/domain/ports.py). Each has a default
that works on one host with its own disk. This page is for a deployment that
needs one of them to be something else — a bucket, a mount, another machine.

A different question from *what can I set?* — [`configuration.md`](configuration.md)
lists the variables, and `.env.example` argues each one where it is set. This
page is about writing the thing a setting names.

Satisfaction is by shape. A port is a Protocol, not a base class, so an adapter
implements the methods and inherits nothing — a test satisfies most of them with
a dict.

## The two ways to supply one

**As a constructor argument**, which every port accepts:

```python
kingfisher = Kingfisher(cfg, sessions=MyStore(), session_root=MyRoot())
```

**As a setting**, which two of them accept. The difference matters more than it
looks: a constructor argument only reaches the construction site you control,
and `kingfisher run` builds its own instance with nowhere to point it. A setting
is read inside `Kingfisher.__init__`, so every entry point inherits it.

```
KINGFISHER_SESSION_STORE_FACTORY=mycompany.stores:build_sessions
KINGFISHER_SERVICE_FILE_STORE_FACTORY=mycompany.stores:build_files
```

Both name `module:name` — something **callable with no arguments** that returns
the adapter. Zero arguments is the whole convention: kingfisher does not know
whether your store wants a bucket, a region, a DSN or a pool, so it asks for none
of them and your factory reads its own configuration. A class with a no-argument
`__init__` satisfies it as readily as a function.

```python
def build_sessions() -> S3SessionStore:
    return S3SessionStore(bucket=os.environ["MY_BUCKET"], prefix="sessions/")
```

Kingfisher checks the **name**, not the building. A spec that will not parse, a
module that will not import, an attribute that is not there, a result of the
wrong shape — each is a `ConfigError` naming the setting. A factory that raises
its own exception is left alone: that is your code failing at your job, its type
may be one your own error handling knows, and the traceback already says which
setting reached it.

## Checking what you wrote

Two ports ship their contract as runnable checks. Import them and point them at
your adapter:

```python
from kingfisher import SESSION_STORE_CONTRACT

@pytest.mark.parametrize("check", SESSION_STORE_CONTRACT, ids=lambda c: c.__name__)
def test_my_store_keeps_the_contract(check):
    check(lambda: S3SessionStore(bucket="kept", prefix="sessions/"))
```

No test framework comes with them — the checks are plain functions that raise
`AssertionError` — so unittest or a loop works as well as pytest.

They are worth running even if your adapter looks obviously correct. `knows()`
had no test anywhere in this repository until the kit was written, and a store
that answers `True` for every id — which is what a bucket reporting a prefix as
present does — passes every other test in this suite while letting a caller
resume a session they invented.

## `SessionStore` — where a session's files live

Four methods over bytes: `fetch`, `save`, `knows`, `forget`. Keys are paths
relative to the session root, the same vocabulary `artifacts()` returns.

**A local directory is a perfectly good implementation.** What the design forbids
is kingfisher *assuming* a disk, not a deployment choosing one — so
`KINGFISHER_SESSION_STORE` naming a directory is not a lesser answer.

Three things the kit will hold you to that the signatures do not say:

- **`save` merges, it does not mirror.** A file the store holds and this call
  does not mention survives. That is what lets a caller send only what changed,
  and it is why `forget` exists.
- **`knows` must be false for an id you never saved.** It is what proves a
  resumed session belongs to whoever named it.
- **A session id that climbs out is refused**, with `UnsafeReferenceError`, on
  all four methods. Import it from `kingfisher`; the service maps that type to
  400 and anything else becomes a 500.

Verified with `SESSION_STORE_CONTRACT` — twelve checks.

## `FileStore` — where a caller's files are fetched from

One method, `fetch(file_id)`, returning `{path: bytes}`. A mapping and not bare
bytes even for a single file, because one ref may name a bundle.

The port has no verb for writing. Kingfisher never puts anything into a file
store; it resolves what a caller already put there. That is why the kit is handed
a `Planted` rather than a factory — you plant, by whatever means your store has,
and tell the checks what you planted:

```python
from kingfisher import FILE_STORE_CONTRACT, Planted

check(Planted(store=S3FileStore(...), ref="sales.csv",
              contents={"sales.csv": b"a,b\n1,2\n"}))
```

**Half of what a file store must get right is which exception it raises.** A ref
that does not resolve is `UnknownReferenceError`; one that names somewhere it may
not is `UnsafeReferenceError`. A bare `FileNotFoundError` cannot be told from
your disk being wrong, and answers 500 to a caller's typo.

Verified with `FILE_STORE_CONTRACT` — four checks.

## `SessionRoot` — where a session's directory is, for one turn

`hold(session_id)` returns a context manager giving a `Path`. This is the port
for a deployment whose session tree exists only while a turn runs — a tmpfs, a
mount made per turn, a volume attached on demand.

**No kit, and no tests naming it.** So the contract is written out here, because
it is subtle in four ways and nothing else will tell you:

- **A directory, not a backend.** The file tools and the shell are two views of
  one directory, and the harness cannot tell a plain directory from a mount: it
  resolves the root once and checks containment per access. Return a path; never
  import the harness.
- **A symlink out of the root is refused**, because that containment check
  resolves before it compares. A session cannot be composed out of links to
  shared content — it has to be a real directory, or a mount presenting as one.
- **One turn.** A session deliberately spans machines, which is what
  `SessionStore` is for; a mount held between turns assumes the process that made
  it is still there for the next one. Held as a context manager so that what was
  mounted is released when the turn ends, including when it ends badly.
- **Kingfisher never closes what you built.** Anything set up per turn belongs
  inside `hold`. Anything set up when your provider was constructed — a pool, a
  thread, a mount made once at startup — is yours to release. Two owners of one
  lifetime is what that rule prevents.

Kingfisher creates the layout inside what you hand it. A provider that created
`data`, `memory` and the rest would break every time this repository adds a
directory.

## `CommandRunner` — what runs a shell command

`run(command, timeout=None)` returning a `CommandResult`. For a deployment that
runs commands somewhere else, or as another user, or with resource limits.

Supplied as a **callable taking the session directory**, not an instance:
`Kingfisher(cfg, runner=lambda session_dir: MyRunner(session_dir))`. A runner is
built for one turn — kingfisher's own Landlock fence is, because its policy is
generated from the session — and a shared instance could not know which session
it was running for.

**No kit, and no tests naming it.** Three things to know:

- **`local` decides whether the fence is applied.** The command arrives already
  confined when `local` is True, which is the default and what you get by not
  saying. A runner that ships the command elsewhere must set `local = False`,
  because the confinement names paths on *this* host — a `sandbox-exec -f
  /Users/.../shell.sb` sent to another machine fails looking like a broken remote
  shell rather than a wrong prefix.
- **Setting `local = False` when you are local loses the fence**, silently. The
  default is True so that forgetting the flag yields more confinement than
  needed, never less.
- **A timeout is a result, not an exception**: `exit_code` 124, the shell's own,
  with output saying so. Raising would make your failure the model's problem
  rather than a tool result it can read and retry.

Only *running* the command is delegated. File access is not, and deliberately:
the shell backend is also the filesystem for every unrouted path, so handing over
"the shell" would hand over `/derived` with it.

## The rest

| Port | What it is | Replace it when |
|---|---|---|
| `DefinitionStore` | A request's own skills and subagents, by id | Callers upload definitions and you hold them somewhere |
| `SkillRepository` | Skills: names, and the files each is made of | Your catalogue is not a directory |
| `AgentRepository`, `SubagentRepository` | Parsed definitions, by name | Same |
| `ToolRepository` | Workspace tools, imported | Rarely — a tool is Python that gets imported, so an implementation must stage to disk first |
| `ThreadStore` | The checkpointer, seen as "something that forgets a thread" | You keep graph state somewhere durable |
| `SessionDirs` | The *rules* about session directories — create exclusively, mark used, list, remove | Rarely; this is a primitive, not a place |

`SessionDirs` and `SessionRoot` are the two easiest to confuse. That one is the
rules about session directories; this one is where the directory is.

## What you cannot replace

**The backend.** It is not "where files live": it wraps every shell command in
`sandbox-exec` or Landlock, it is what refuses a host path, and its route table
is what makes `/data` read-only legal at all — deepagents refuses read-only rules
outright on a backend that executes unless every rule is route-scoped. A
deployment-supplied backend would take all three on while the system prompt still
promises them.

Object storage reaches a session as a mount (`SessionRoot`), or by being copied
in and out (`FileStore` in, `SessionStore` out). Both work today. Routing `/data`
to a store-backed backend would break a promise the prompt makes to the model in
a table — *"nothing in the workspace is out of the shell's reach"* — leaving the
agent able to read its inputs and unable to run anything over them.

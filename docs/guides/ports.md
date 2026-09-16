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

**As a setting**, which only the session store accepts. The difference matters
more than it looks: a constructor argument only reaches the construction site you
control, and `kingfisher run` builds its own instance with nowhere to point it. A
setting is read inside `Kingfisher.__init__`, so every entry point inherits it.

```
KINGFISHER_SESSION_STORE_FACTORY=mycompany.stores:build_sessions
```

It names `module:name` — something **callable with no arguments** that returns
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

Every port on this page ships its contract as runnable checks, and so does the
backend. Import them and point them at your adapter:

```python
from kingfisher import SESSION_STORE_CONTRACT

@pytest.mark.parametrize("check", SESSION_STORE_CONTRACT, ids=lambda c: c.__name__)
def test_my_store_keeps_the_contract(check):
    check(lambda: S3SessionStore(bucket="kept", prefix="sessions/"))
```

No test framework comes with them — the checks are plain functions that raise
`AssertionError` — so unittest or a loop works as well as pytest.

Two of them do more than read, and that is the ports rather than the kits:
`SESSION_ROOT_CONTRACT` creates directories inside what your provider yields,
because that is what kingfisher does with it, and `COMMAND_RUNNER_CONTRACT` runs
commands — one of them waits a second for a timeout. Run those where you would
run an integration test.

They are worth running even if your adapter looks obviously correct. A store that
answers `True` for every id — which is what a bucket reporting a prefix as
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
  all four methods. Import it from `kingfisher`: a caller tells a hostile id
  from a broken store by that class.

Verified with `SESSION_STORE_CONTRACT` — twelve checks.

## `SessionRoot` — where a session's directory is, for one turn

`hold(session_id)` returns a context manager giving a `Path`. This is the port
for a deployment whose session tree exists only while a turn runs — a tmpfs, a
mount made per turn, a volume attached on demand.

Verified with `SESSION_ROOT_CONTRACT` — six checks. The contract is subtle in
four ways, and the checks cover the two where being wrong is a security or
correctness failure rather than an inconvenience:

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
directory — so the directory you yield need not exist yet, and the kit does not
ask that it does. `LocalSessionRoot` yields a path it has not made.

The two the kit is really for: **two session ids must not resolve to one
directory** (every path would be legal, and each session would read the other's
files as its own), and **an exception inside the block must propagate** — a
context manager whose `__exit__` returns true reports a failed turn as a
successful one, with whatever you mounted still mounted.

### What a root that really is per-turn has to answer

`LocalSessionRoot` yields a directory that survives between turns, and parts of
the turn path lean on that without saying so. A root that genuinely releases what
it held — a mount made and dropped around each turn — meets the following, and
none of it fails loudly.

**`/data` does not come back.** What the store is handed after a turn is
`/derived`, `/memory`, the transcript and the pinned agent. `/data` is left out
because it came from the caller and, on a directory that persists, it is still
sitting there; on a fresh one, turn two opens with an empty `/data` and the
caller's inputs gone. Carry `data/**` yourself, or require that callers re-supply
it every turn.

**A deletion does not stick.** `save` merges rather than mirrors, so a name the
store once held it holds still, and what it holds is written back into a tree
that starts empty — an agent that deletes `derived/draft.md` finds it there again
on the next turn. A store behind a per-turn root has to mirror instead: diff what
it holds against what it was handed, and drop the difference. Do not reach for
the merge rule to fix it. That is what lets a caller send only what changed, and
`SESSION_STORE_CONTRACT` holds you to it.

**`sessions()` and `reap` see nothing.** Both walk `<workspace>/sessions/`, which
a custom root need never use. An id the disk has never seen still resolves,
because the store's `knows` answers for it, but listing and sweeping do not — so
retention moves to the store along with the files, and `session_ttl_s` stops
deciding anything. `kingfisher sessions` and `kingfisher reap` are those two
calls with a terminal in front of them, so both report an empty workspace here
however much it is holding.

Deleting one by id is the exception, because it is handed the id rather than
looking for it: `delete_session` forgets the store's copy whether or not the
workspace holds a directory, so an id you delete stops resolving. Whatever your
root keeps between turns of its own accord is yours to remove.

## `CommandRunner` — what runs a shell command

`run(command, timeout=None)` returning a `CommandResult`. For a deployment that
runs commands somewhere else, or as another user, or with resource limits.

Supplied as a **callable taking the session directory**, not an instance:
`Kingfisher(cfg, runner=lambda session_dir: MyRunner(session_dir))`. A runner is
built for one turn — kingfisher's own Landlock fence is, because its policy is
generated from the session — and a shared instance could not know which session
it was running for.

Verified with `COMMAND_RUNNER_CONTRACT` — five checks, which run commands.
Three things to know:

- **`local` decides whether the fence is applied.** The command arrives already
  confined when `local` is True, which is the default and what you get by not
  saying. A runner that ships the command elsewhere must set `local = False`,
  because the confinement names paths on *this* host — a `sandbox-exec -f
  /Users/.../shell.sb` sent to another machine fails looking like a broken remote
  shell rather than a wrong prefix.
- **Setting `local = False` when you are local loses the fence**, silently. The
  default is True so that forgetting the flag yields more confinement than
  needed, never less.
- **A replaced backend carries your runner, or drops it.** The runner reaches the
  shell through the backend, so kingfisher hands it to the factory under
  [`backend`](#backend--the-filesystem-the-agent-runs-against) rather than applying
  it itself. Write your factory with the `runner` keyword and pass it on. A factory
  that omits it silently runs every command under kingfisher's own fence instead of
  yours, and the backend that comes back is perfectly well-formed — nothing at
  runtime can tell. This is the one thing the two parameters share, and the reason
  `BackendFactory` is typed.
- **A timeout is a result, not an exception**: `exit_code` 124, the shell's own,
  with output saying so. Raising would make your failure the model's problem
  rather than a tool result it can read and retry. This is the one the kit
  exists for: every timeout API in Python raises, `subprocess.run(timeout=...)`
  included, so the obvious implementation gets it wrong and nothing else would
  say so.

`CommandResult` is exported from `kingfisher` — you return one of these, so you
need it. It carries `output`, `exit_code` and `truncated`; `exit_code` is not
optional, because a caller deciding whether a command worked has nothing to do
with `None` but guess.

Only *running* the command is delegated. File access is not, and deliberately:
the shell backend is also the filesystem for every unrouted path, so handing over
"the shell" would hand over `/derived` with it.

## The rest

| Port | What it is | Replace it when |
|---|---|---|
| `ThreadStore` | The checkpointer, seen as "something that forgets a thread" | You keep graph state somewhere durable |
| `SessionDirs` | The *rules* about session directories — create exclusively, mark used, list, remove | Rarely; this is a primitive, not a place |

`SessionDirs` and `SessionRoot` are the two easiest to confuse. That one is the
rules about session directories; this one is where the directory is.

The definition repositories in `domain/ports.py` — one per kind, from
`SkillRepository` to `MiddlewareRepository` — are not on this list, because they
are not for replacing. They are what kingfisher builds from a catalogue's
directories. Definitions kept somewhere else are staged into directories first, and
the `KINGFISHER_*_DIR` settings in [configuration](configuration.md) say where each
kind is read from.

## `backend` — the filesystem the agent runs against

Not a port, and not optional. Every `Kingfisher` names one:

```python
from kingfisher import Kingfisher, default_backend

kingfisher = Kingfisher(cfg, backend=default_backend)
```

Most deployments write exactly that and read no further. `default_backend` is the
backend kingfisher used to build for you without asking, unchanged — naming it
costs you nothing and buys the rest of this section a reader.

**Why it is the one thing on this page you cannot leave out.** The backend is the
sandbox. It wraps every shell command in `sandbox-exec` or Landlock. It refuses a
host path handed to a file tool. Its route table is what makes `/data` read-only
legal at all — deepagents refuses read-only rules outright on a backend that
executes unless every rule sits under a route. And it is the filesystem for every
unrouted path, which is why the model can be told, in a table it reads every turn,
that *nothing in the workspace is out of the shell's reach*.

None of which makes the old silence unsafe: the default was always the strict
option and still is, and requiring the parameter makes no deployment safer on its
own. What it does is make sure nobody wires kingfisher without finding out there
is a boundary here at all.

**Try a mount before replacing it.** Object storage reaches a session as a mount
(`SessionRoot`), or by being copied in and out (`SessionStore`). Both work today
and cost you none of the four jobs above.

**Replace it when your callers may not share storage.** That is the case a mount
does not cover, and the reason this seam is open. A mount is established once,
outside the process, before any session exists — so a session created at runtime
cannot be given one of its own, and every session ends up on one mount separated
by a path prefix and nothing else. A deployment that forbids one caller's session
from reaching another's needs the separation in the wiring instead.

**Build on the default rather than from nothing.** Call it and change the one
thing you came to change, and host-path refusal, the route table and the
confinement all survive without your thinking about them:

```python
def my_filesystem(cfg, session_dir, *, catalogue=None, runner=None):
    mine = default_backend(cfg, session_dir, catalogue=catalogue, runner=runner)
    return CompositeBackend(
        default=MySandbox(session=session_dir.name),
        routes=mine.routes,
    )

kingfisher = Kingfisher(cfg, backend=my_filesystem)
```

Starting from nothing instead is allowed and is yours to get right — which is a
thing to do deliberately, not to discover. Two of the four contract checks below
run on every backend kingfisher resolves and will tell you about the two failures
that otherwise report nothing.

**Take both keyword arguments even if you ignore one.** `catalogue` is what lets a
session see the skills your bundles ship; `runner` is the `CommandRunner` you
wired, and a factory that quietly drops it gets a backend that is perfectly
well-formed and runs its commands somewhere you did not choose. Nothing at runtime
can see that mistake, which is why `BackendFactory` is a typed protocol rather than
a line of prose — write the signature out and your type checker catches it.

**A factory, and only a factory.** A backend is rooted at a session, so one
instance shared between sessions is one filesystem for every caller — usually the
thing you are replacing the backend to avoid. It is called per turn with the
session it is for, and whatever you return is what that turn's agent runs against.
Passing an instance is a `TypeError`.

**A pre-built graph counts as an answer.** `Kingfisher(cfg, graph=...)` already
carries the backend it was compiled on, so it takes no `backend` and refuses one
passed beside it. `run()` and `stream()` keep `backend=default_backend` in their
signatures, because they are conveniences over a *default* `Kingfisher` and that
is what makes the one-liner a one-liner.

There is no setting for this one. Build `Kingfisher` yourself; `kingfisher run`
builds its own with the default.

### Checking what you returned

**Two of the four run on their own**, against every backend kingfisher resolves,
and raise `ConfigError` rather than letting a turn find out. Both of them only
look — an `isinstance` and a scan of your routes, no I/O — so they cost nothing
and there is nothing to switch on:

**deepagents decides what a backend is with `isinstance` against its own abstract
base class**, not by the methods present. Implement every method of
`SandboxBackendProtocol` correctly while inheriting nothing and the shell tool is
dropped from the agent's roster — the model is told execution is unavailable if
it reaches for it, and nothing would have told you. Inherit `BaseSandbox`, which
implements the file operations in terms of `execute`, or register with the ABC.

**The paths kingfisher denies writes under must sit under something you route**,
or the graph will not build: deepagents refuses `permissions=` outright on a
backend that executes unless every rule is scoped to a route.

**The other two stay yours**, because they write files and run shell commands, and
a library should not do either at a build:

```python
from kingfisher import BACKEND_CONTRACT

@pytest.mark.parametrize("check", BACKEND_CONTRACT, ids=lambda c: c.__name__)
def test_my_backend_keeps_the_contract(check):
    check(lambda: my_filesystem(kingfishers_backend, session_dir))
```

Run all four. The automatic pair costs nothing twice, and your own suite is a
better place to read a failure than a turn is.

**The shell and the file tools have to be two views of one filesystem**, and this
is the one left that reports nothing on its own. A virtual path becomes a shell
path by dropping its leading slash; the prompt says so in a table the model reads
every turn. Route a path somewhere the shell cannot follow and the agent can read
its inputs and run nothing over them, with a confused model as the only symptom.

The last is ordinary: if you refuse host paths, refuse them with `HostPathError`,
because that is the type `HostPathGuard` turns into a correction the model can act
on. Refusing them at all is optional — inside a sandbox of your own, `/etc/passwd`
is a file, and refusing it would be refusing your own filesystem.

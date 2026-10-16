# Writing an adapter

Kingfisher reaches the world through the Protocols in
[`domain/ports.py`](../../src/kingfisher/domain/ports.py) — what runs a shell
command, and where each turn's record goes — and through the `sessions` every
deployment names. The Protocols have defaults; the sessions have none to fall back
on, and kingfisher's own, `LocalSessions`, works on one host with its own disk. This
page is for a deployment that needs one of them to be something else — a sandbox, a
bucket, another machine.

A different question from *what can I set?* — [`configuration.md`](configuration.md)
lists the variables, and `.env.example` argues each one where it is set. This
page is about writing the thing a setting names.

Satisfaction is by shape. A port is a Protocol, not a base class, so an adapter
implements the methods and inherits nothing — a test satisfies most of them with
a dict.

## The two ways to supply one

**As a constructor argument**, which every port accepts:

```python
kingfisher = Kingfisher(cfg, sessions=LocalSessions(runner=my_runner), run_events=my_sink)
```

**As a setting**, for the command line only. A constructor argument reaches the
construction site you control, and `kingfisher run`, `sessions`, `reap` and
`artifact` build their own instance with nowhere to point it. `KINGFISHER_SESSIONS`
is where they are told the sessions, and `KINGFISHER_PLUGINS` the rest, as a
`kingfisher.Plugins` whose fields are the keywords above:

```
KINGFISHER_SESSIONS=mycompany.sessions:build_sessions
KINGFISHER_PLUGINS=mycompany.plugins:build_plugins
```

It names `module:name` — something **callable with no arguments** that returns
the adapter. Zero arguments is the whole convention: kingfisher does not know
whether yours wants a bucket, a region, a DSN or a pool, so it asks for none of
them and your factory reads its own configuration. A class with a no-argument
`__init__` satisfies it as readily as a function.

Kingfisher checks the **name**, not the building. A spec that will not parse, a
module that will not import, an attribute that is not there, a result of the
wrong shape — each is a `ConfigError` naming the setting. A factory that raises
its own exception is left alone: that is your code failing at your job, its type
may be one your own error handling knows, and the traceback already says which
setting reached it.

## Checking what you wrote

The runner and the backend ship their contracts as runnable checks. Import them
and point them at your adapter:

```python
from kingfisher import SESSION_BACKENDS_CONTRACT

@pytest.mark.parametrize("check", SESSION_BACKENDS_CONTRACT, ids=lambda c: c.__name__)
def test_my_backends_keep_the_contract(check, tmp_path):
    check(lambda: (my_config(tmp_path), MySessions()))
```

No test framework comes with them — the checks are plain functions that raise
`AssertionError` — so unittest or a loop works as well as pytest. Most do more than
read, and that is the ports rather than the kits: a backend is checked by writing
files through it, and `COMMAND_RUNNER_CONTRACT` runs commands — one of them waits a
second for a timeout. Run those where you would run an integration test.

They are worth running even if your adapter looks obviously correct. Backends that
hand every session the same storage pass every functional test you are likely to
write, while each caller reads every other caller's files as their own.

## `CommandRunner` — what runs a shell command

`run(command, timeout=None)` returning a `CommandResult`. For a deployment that
runs commands somewhere else, or as another user, or with resource limits.

Supplied to kingfisher's own sessions as a **callable taking the session
directory**, not an instance: `LocalSessions(runner=lambda session_dir:
MyRunner(session_dir))`, passed as `sessions=`. A runner is built for one session —
kingfisher's own Landlock fence is, because its policy is generated from the session
— and a shared instance could not know which session it was running for. It goes on
the sessions rather than on `Kingfisher` because only they know where a
session is on this host; sessions of your own run their commands however
their `open` builds the backend.

Verified with `COMMAND_RUNNER_CONTRACT` — five checks, which run commands.
Things to know:

- **`local` decides whether the fence is applied.** The command arrives already
  confined when `local` is True, which is the default and what you get by not
  saying. A runner that ships the command elsewhere must set `local = False`,
  because the confinement names paths on *this* host — a `sandbox-exec -f
  /Users/.../shell.<digest>.sb` sent to another machine fails looking like a broken remote
  shell rather than a wrong prefix.
- **Setting `local = False` when you are local loses the fence**, silently. The
  default is True so that forgetting the flag yields more confinement than
  needed, never less.
- **A runner is handed the command and nothing else** — no working directory and no
  environment, local or not. Its shell starts wherever the runner starts it, and
  `$TMPDIR` and `$KINGFISHER_SKILLS` are whatever they are there. So the agent is not
  told what kingfisher tells it about its own shell, which says all three; it is
  told, neutrally, that the shell may not see the session's files. Say what yours
  sees with `shell_prompt=` beside the runner: see
  [`ShellPrompt`](#shellprompt--what-the-agent-is-told-about-your-shell).
- **A subclass of `LocalSessions` keeps the runner.** `super().open(...)` builds
  the backend with the runner the instance was given, so an `open` that adjusts what
  it built still runs its commands where you chose. See
  [`sessions`](#sessions--the-filesystem-the-agent-runs-against).
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
"the shell" would hand over `/outputs` with it.

## `ShellPrompt` — what the agent is told about your shell

Kingfisher can say how a shell sees the session's files only of the shell it runs
itself: started in the session root, so `/inputs/data.csv` is `inputs/data.csv`
there, with `$TMPDIR` the scratchpad and `$KINGFISHER_SKILLS` the catalogue. Any
other shell is described neutrally — it may not see a file-tool path under that name
or any other, so check before relying on one — until it says what is true of it:

```python
from kingfisher import LocalSessions, ShellPrompt

SANDBOX = ShellPrompt(
    prompt="""## The shell

The shell (`execute`) runs in a sandbox holding this session at `/workspace`, so
`/{{inputs}}/<name>` is `/workspace/{{inputs}}/<name>` there.""",
    execute=(
        "- The shell holds this session at `/workspace`: `/{{inputs}}/data.csv` is "
        "`/workspace/{{inputs}}/data.csv`. Use the optional timeout to override the default."
    ),
)

backends = LocalSessions(runner=lambda session_dir: MyRunner(session_dir), shell_prompt=SANDBOX)
```

A shell of your own carries it instead — the object your backend runs `execute` on,
which is the composite's `default` where you return a `CompositeBackend`:

```python
class MySandbox(BaseSandbox):
    shell_prompt = SANDBOX
```

Things to know:

- **Three parts, because three readers see different ones.** `prompt` is the agent's
  section on the shell, in place of kingfisher's. `skills` follows the skills section,
  so it is shown only where skills are on. `execute` replaces deepagents' "Use absolute
  paths…" line in the tool's description, and is the only part a subagent sees: a
  subagent is built without the system prompt. Kingfisher's own are
  `src/kingfisher/prompts/shell_here.md`, `shell_here_skills.md` and
  `shell_here_execute.md`.
- **A part left `None` is said the neutral way**, claiming only what holds wherever a
  shell runs — for `execute`, deepagents' own line stays — and `""` says nothing there.
- **`{{inputs}}`, `{{outputs}}`, `{{memory}}`, `{{scratchpad}}` and `{{tool_results}}`
  are filled from `Config.layout`**, as kingfisher's own wording is, so renaming a
  folder cannot leave your sentence naming the old one. Any other `{{name}}` is refused
  when the `ShellPrompt` is made.
- **It is read off the shell, not off your `LocalSessions` subclass.** A subclass
  inherits everything the class has, so one that swapped in a sandbox would have
  inherited kingfisher's wording and its agents been told of a shell they do not have.
  A sandbox that says nothing is described neutrally.
- **Kingfisher's own shell describes itself**, so `shell_prompt=` without a runner is
  refused. Standing instructions for every agent go in `INSTRUCTIONS.md` beside the
  definitions, or `instructions=` on `Kingfisher`.
- **A miss is corrected only on kingfisher's own shell.** A command that fails on a
  file-tool path there is answered with the path's spelling in the shell, worked out
  from the session root it starts in; no other shell is known to start there.
- **deepagents' list of this host's paths is kept only for a shell on this host.**
  deepagents adds a "Shell paths vs. virtual paths" section giving each routed folder's
  path here. It stays for kingfisher's own shell and for a runner whose `local` is
  true, and is removed for a runner that runs elsewhere and for a shell of your own,
  so your `ShellPrompt` is the only thing the agent is told about where files are.

## `RunEvents` — where each turn's record goes

What a turn did — its start and end, every model call with its token counts, every
tool call — as one event at a time, while it happens:

```python
class ShipToCollector:
    def record(self, event):
        collector.send(dict(event))

kingfisher = Kingfisher(cfg, sessions=LocalSessions(), run_events=ShipToCollector())
```

Each event is a flat mapping. `event` names it — `run_start`, `run_end`,
`model_call`, `tool_start`, `tool_end`, `tool_error`, `model_error` — and `ts`,
`session_id`, `turn_id`, `model` and `endpoint` ride on every one. `model_call`
carries `input_tokens`, `output_tokens` and `cache_read`, which is what a bill is
totalled from.

**Wire nothing and they go to the `kingfisher.run` logger**, one JSON line per event
at `INFO`, with the mapping itself on the record as `run_event` for a handler that
ships structured logs. Python's default configuration drops `INFO`, so a
deployment that wants them either configures logging or passes a sink — and
`kingfisher run` and `decide` keep none unless given `--log FILE`, which appends one
JSON line per event.

**Not session state.** They used to be a file in the session's `.harness`, deleted
with it. They outlive the session now, because a log is most wanted for the turn
that went wrong, and keeping them is your retention policy rather than
kingfisher's. `session_id` and `turn_id` are how one session's are found again.

**A sink that raises does not fail the turn.** The failure is logged as a warning
and the turn goes on — so a sink that is failing is heard from only in your logs.
It is called once per event, and not always from the thread running the turn: tool
calls a model makes together run at once, on an executor's threads, and on the
async path langchain hands each event to a worker thread. So the events of tool
calls made together can interleave, though each call's start comes before its end,
and a sink that keeps state between events wants a lock.

## The rest

**A checkpointer every session shares** is passed as `threads=` and is langgraph's
own type, so there is no protocol of ours for it to satisfy. A factory passed as
`threads=` instead is called with a session's id at the start of each of its turns,
and what it returns is released when the turn ends. Deleting a session asks
it to delete that session's thread — `delete_thread`, or `adelete_thread` on the async
path where the saver has one and on kingfisher's thread pool where it does not — and
`reap` asks it to `list` its threads where it can, to find any no session owns.

Definitions are not covered here, because how they are read is not for replacing:
kingfisher reads each kind from a directory. Definitions kept somewhere else are staged
into one first, and `KINGFISHER_DEFINITIONS_DIR` in [configuration](configuration.md)
says which.

## `sessions` — the filesystem the agent runs against

Not optional. Every `Kingfisher` names them:

```python
from kingfisher import Kingfisher, LocalSessions

kingfisher = Kingfisher(cfg, sessions=LocalSessions())
```

Most deployments write exactly that and read no further. `LocalSessions` opens the
backend kingfisher used to build for you without asking, unchanged — naming it
costs you nothing and buys the rest of this section a reader.

**Why it is the one thing on this page you cannot leave out.** The backend is the
sandbox. It wraps every shell command in `sandbox-exec` or Landlock. It refuses a
host path handed to a file tool. Its route table is what makes `/inputs` read-only
legal at all — deepagents refuses read-only rules outright on a backend that
executes unless every rule sits under a route. And it is the filesystem for every
unrouted path, which is why the model can be told, in a table it reads every turn,
that *nothing in the workspace is out of the shell's reach*.

None of which makes the old silence unsafe: the default was always the strict
option and still is, and requiring the parameter makes no deployment safer on its
own. What it does is make sure nobody wires kingfisher without finding out there
is a boundary here at all.

**What it is: a `Sessions`.** One object whose `open` is called per turn
with the id of the session it is for, and returns that session's backend — and which
answers the questions
only something that sees every session can: `sessions(cfg)` lists them with when
each was last used, `mark_used`, `size` and `delete` do what they say. `reap`,
`kingfisher sessions` and the session quota are answered by it. `LocalSessions`
keeps each session as a directory under `<workspace>/sessions`, named for its id, and
refuses with `UnknownSessionError` an id that cannot name one there — empty, starting
with `.`, or holding `/` or NUL. Which session a turn runs in is the request's to say:
any id names a session, the first turn naming one starts it, and a request naming
none runs in `default`. So `open` is handed ids nobody has used before, and makes
them; your own sessions are too. That is why a service must derive the ids
it sends itself and never forward a user's: an id is no longer something kingfisher
handed out, and naming one is reaching it, unless the access policy stops the
caller at the session's pinned agent.

**Try a mount before replacing it.** Sessions that must outlive the machine, or
never touch its disk, get durable or memory-backed storage mounted at
`<workspace>/sessions`, and cost you none of the four jobs above.

A symlink on `<workspace>/sessions` is the same move, lighter, and the macOS profile
follows it: its rules are built from the real path that link resolves to, so a session
on another volume is writable and its `/inputs` and `/.harness` are still refused. One
link deep is as far as that reaches. A *single* session that is itself a link lands
where the profile names nothing -- it is one static text per workspace, written before
any session exists -- and that session is then unusable, every command in it failing at
`cd` with a message about the shell. `kingfisher doctor` names one when it sees it, and
the Linux fences are unaffected either way, being built from the session directory
itself.

**Replace it when your callers may not share storage.** That is the case a mount
does not cover, and the reason this seam is open. A mount is established once,
outside the process, before any session exists — so a session created at runtime
cannot be given one of its own, and every session ends up on one mount separated
by a path prefix and nothing else. A deployment that forbids one caller's session
from reaching another's needs the separation in the wiring instead.

**Build on the default rather than from nothing.** Subclass `LocalSessions`,
override `open`, and change the one thing you came to change. Host-path
refusal, the route table, the confinement and the housekeeping all survive without
your thinking about them:

```python
from kingfisher import LocalSessions

class MySessions(LocalSessions):
    def open(self, cfg, session_id, /, *, catalogue=None):
        mine = super().open(cfg, session_id, catalogue=catalogue)
        return MyScoped(default=MySandbox(session=session_id), routes=mine.routes)

kingfisher = Kingfisher(cfg, sessions=MySessions())
```

Where `MyScoped` is a `CompositeBackend` of your own, hand it
`artifacts_root=mine.artifacts_root` beside the routes, or deepagents writes its
own files at the session root — see *deepagents' own files* below.

`backend_at(cfg, directory)` is the same thing for a directory of your choosing.
A plain function is refused with a `TypeError` that says to do this: it can build a
backend, and cannot say which sessions there are.

**The async path asks for each method's `a`-prefixed twin.** `Kingfisher.asession`,
`apending` and `aartifact` open a session with `aopen` and list them with
`asessions`; `adelete_session` deletes one with `adelete`; and a turn on `astream`
also records the session used with `amark_used` and checks its quota with `asize`.
`Sessions` carries all five, running the sync method on kingfisher's thread
pool, so a class that subclasses it — `LocalSessions` does — has them already.
Override one where it is a round trip you can await: the async path then holds no
thread while it waits. A class that does not subclass `Sessions` has to write
all five, because the type check asks for every method. The backend `open` returns is read through its own async methods,
`adownload_files` and the rest, which deepagents' `BackendProtocol` already has.

Starting from nothing instead is allowed and is yours to get right — which is a
thing to do deliberately, not to discover. Two of the six contract checks below
run on every backend kingfisher resolves and will tell you about the two failures
that otherwise report nothing.

**Take `catalogue` even if you ignore it.** It is what lets a session see the skills
your bundles ship, and an `open` that quietly drops it gets a backend that is
perfectly well-formed and sees none of them. Nothing at runtime can see that mistake,
which is why `Sessions` is a typed protocol rather than a line of prose —
write the signature out and your type checker catches it.

**What `open` returns is one session's, and only one.** A backend shared between
sessions is one filesystem for every caller — usually the thing you are replacing
the backend to avoid. Passing a backend itself is a `TypeError`, and
`two_sessions_are_kept_apart` in the kit below catches the subtler version: every
id handed the same storage.

**The turn lock is on the sessions.** They have `claim(cfg, session_id, /, *,
stale_after, now=None)`, `release(cfg, session_id, /)` and `held(cfg, session_id, /, *,
stale_after, now=None)`: a claim that fails while another is live, which a `write`
cannot be, because a `write` overwrites and two turns would both take it. One lock per
session, not one for the object: a turn in one session must not refuse a turn in
another. `LocalSessions` does it with `mkdir` under the session's `.harness`. Subclass
`Sessions` and `aclaim` and `arelease` run them on kingfisher's pool; override them
where your lock is a round trip you can await. Two turns in one session share a
conversation and the last write wins, so this is what refuses the second. The backend
`open` returns carries none of this.

**A pre-built graph names its sessions too.** `Kingfisher(cfg, graph=...)`
runs its agent on the backend the graph was compiled with, but kingfisher still
places a request's inputs, collects what a turn left and takes the turn lock through
the sessions you pass — so a graph is refused without them, and one built on
kingfisher's own passes `sessions=LocalSessions()`. Pass the ones that reach the
sessions your graph's backend keeps: nothing can check that from outside a compiled
graph. `run()` and `stream()` take `sessions=` too, and refuse to run without it,
with a graph or without.

**What you return is also what a workspace tool is handed.** A tool that asks for
it reaches this backend's file methods directly — behind the turn's permissions,
and without `execute`; [`tools.md`](tools.md#or-the-tool-is-handed-the-sessions-filesystem)
is the tool's side of it. So every file method of `BackendProtocol` may be called
on yours, `delete` and the two batch ones included, whichever of them the
built-in tools happen to use. A pre-built graph is the exception: kingfisher
drives it with no context, and such a tool finds `runtime.context` is `None`.

**Kingfisher reaches the session through it too.** A request's `inputs` are placed
with `upload_files` under `/inputs/`, what a turn left is listed with `glob` under
`/outputs/` and `/memory/`, and `Kingfisher.artifact` fetches one with
`download_files`. Each tool result a turn records is saved with `upload_files` under
`/scratchpad/tool_results/`, behind the turn's permissions as a tool's own save is; at
the end of the turn that folder and deepagents' offload folder are listed with `ls`, and
a result file no row of the ledger names is removed with `delete`. So all of those
work on a backend that keeps the session somewhere other than the directory it was
handed. `/inputs` has to take that upload
while refusing the agent's own writes: `LocalSessions` routes it to
`InputsBackend`, which lifts the permission bits for kingfisher's upload alone, and
a backend of yours meets the same promise its own way.

**Open it once if you use it around a turn.** `Kingfisher.files_for(session_id)`
builds a session's backend the way a turn does, through your sessions,
and `run`, `stream`, `arun` and `astream` take it back
as `files=` and run the turn on it rather than opening another. Where each `open`
is a sandbox, that is one sandbox rather than two. `afiles_for` is the same for a
caller on an event loop, opening it with `aopen`. The request has to run in the
session they were opened for -- a request naming none runs in `default`. A session no
turn has made yet is made, so a caller can place its inputs before the first turn.
`files_for` does not ask who you are acting for, so hand what it returns only to code
entitled to that session; the turn still checks the caller it is given.

**And what it keeps about the session, under `/.harness`.** The agent the session is
pinned to, its conversation, and a turn paused at an approval gate are all written
and read through your backend — which means your agent's shell can reach them too,
unless your backend keeps it out. Nothing signs them, so what stands between the
agent and rewriting its own pinned agent is that fence, and it is yours to get
right: **`shell_denied`**, in the kit below, drives `execute` at `/.harness` and
`/inputs` and fails if the shell can write either. `kingfisher doctor` warns when
`KINGFISHER_SESSIONS` names sessions of yours, because it
cannot look inside them.

Nothing else is read from a directory on this host. A backend that keeps its
sessions somewhere else keeps all of them there.

**Saying a file is on this host: `host_path`.** A workspace tool that takes a `path`
is handed a real file to open — see [`tools.md`](tools.md). Kingfisher asks the
backend where that file lives here: it follows your routes, and a
`FilesystemBackend` answers from its own root, so a backend built from those needs
nothing more. A backend whose files are on this host by some other means — a
network mount, a cache — says so with an optional method on what your `open`
returns:

```python
class MountedFiles(MySandbox):
    def host_path(self, virtual):
        return Path("/mnt/sessions") / self.session_id / virtual.lstrip("/")
```

Return `None` for a path that is not on this host; the tool is refused with a
message saying to read through `runtime.context.backend`, which works anywhere.
What you return is taken at your word: it is handed to a tool running in
kingfisher's own process, outside every sandbox. So it must never name another
session's file — `a_host_path_stays_in_its_session` in the kit below writes a file
into two sessions and reads back what your answer names. The turn's rules still
apply first: a path the file tools may not read, `/.harness` among them, is
refused before your method is asked.

For the command line, name it with `KINGFISHER_SESSIONS`; without it,
`kingfisher sessions`, `reap` and `artifact` run on `LocalSessions`.

### Checking what you returned

**Two of the six run on their own**, against every backend kingfisher resolves,
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

**The other four stay yours.** Three write files and run shell commands, and a
library should not do either at a build. The fourth only looks, and finds a folder
in the wrong place rather than an agent that cannot work, which is not worth
refusing a turn over:

```python
from kingfisher import BACKEND_CONTRACT

@pytest.mark.parametrize("check", BACKEND_CONTRACT, ids=lambda c: c.__name__)
def test_my_backend_keeps_the_contract(check, cfg):
    check(lambda: MySessions().open(cfg, "a-session"))
```

Run all six. The automatic pair costs nothing twice, and your own suite is a
better place to read a failure than a turn is. And run
`SESSION_BACKENDS_CONTRACT`, above, against the object that makes them: that two
sessions are kept apart, that a session is there on the next turn, that the claim
is exclusive, that what is listed and deleted is what exists, that where you say
a file is on this host it is that session's file, that a session serves under
`/skills/` the skills kingfisher read, and that every async twin —
`aopen`, `asessions`, `adelete`, `aclaim`, `arelease`, `asize` and `amark_used` —
answers as its sync one does.

**A session has to serve under `/skills/` what kingfisher read.** Grants and the
read-deny rules come from kingfisher's own reading of the catalogue, each mount and
each subagent's skills, and the agent reads skills through your backend. Serve one
from anywhere else and it is either granted and missing, or readable however a request
narrowed, and nothing at runtime says which. `a_session_serves_the_skills_kingfisher_read`
lists each through one session and compares names and paths.

**What the agent is told about your shell has to be true of it**, and this is the
one left that reports nothing on its own. Your shell is described neutrally unless it
carries a [`ShellPrompt`](#shellprompt--what-the-agent-is-told-about-your-shell), and
whatever that says is taken at its word. Say a virtual path becomes a shell path by
dropping its leading slash, and route one somewhere the shell cannot follow, and the
agent can read its inputs and run nothing over them, with a confused model as the
only symptom.

**deepagents' own files have to land under `/scratchpad/`.** A tool result too
long for a message is saved to a file and the model handed a stub naming it, and
the summarizer saves the conversation it replaces. Both go under the composite's
`artifacts_root`, as `large_tool_results/` and `conversation_history/`, and the
prompt tells the agent that is `/scratchpad/`. The backend `backend_at` builds has
it set, and so does a subclass of its class that calls its `__init__`. A
`CompositeBackend` of your own defaults to `/` and puts both folders at the session
root: pass `artifacts_root="/scratchpad/"`. deepagents reads it only off a
`CompositeBackend`, so on anything else it is `/` whatever you set.
`offloads_in_the_scratchpad` reads it the same way.

**The shell may not write under `/.harness` or `/inputs`.** `shell_denied` writes a
file there through the backend, then has the shell try to overwrite it and to create
another beside it. `LocalSessions` passes on the strength of its sandbox; with the
sandbox off it fails on `/.harness`, which is exactly the deployment it is there
to catch.

The last is ordinary: if you refuse host paths, refuse them with `HostPathError`
(`from kingfisher import HostPathError`),
because that is the type `HostPathGuard` turns into a correction the model can act
on. Refusing them at all is optional — inside a sandbox of your own, `/etc/passwd`
is a file, and refusing it would be refusing your own filesystem.

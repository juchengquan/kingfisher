# A session is its backend

**Status:** proposed. **Slice 1 landed**: a request's data is placed, and a
turn's artifacts collected and fetched, through the session's backend. **Slice 3
landed**: the run log is `RunEvents`. **Slice 2 landed**: `/.harness` goes through
the backend, signed, with the startup rule, `kingfisher key`, the `doctor` check and
`shell_denied`. Two things were decided building it, recorded under *Settled in
review*: any supplied runner needs a key, and a paused checkpoint is no longer
written aside and renamed into place. Slices 4 and 5 are not built. Every question it raised was settled in review on 2026-09-30, and
the answers are under *Settled in review* with their reasons. It stays here until
its slices land or it is withdrawn.
**Date:** 2026-09-30.
**Occasion:** a question about whether a session is implemented with the backend.
It is only half implemented that way. The agent reaches a session only through
the backend a `BackendFactory` returns. Kingfisher reaches the same session through
a `Path`, and assumes the two are the same directory. With `default_backend` they
are. With a backend that runs somewhere else, they are two places, and nothing
notices.
**Reverses part of a decision.** *Wiring a store* in `decisions.md` settled that
storage reaches a session through `SessionRoot` (a mount) or `SessionStore` (copy
in and out), execution through `CommandRunner`, and that the backend is the
security boundary rather than where files live. This proposal makes the backend
where a session's files live as well, and removes `SessionRoot` and `SessionStore`.
What survives from that entry is listed under *What this reverses*.
**Breaking, on purpose, before 1.0.** Two ports, two settings and a `RunResult`
field go, and the two settings are refused rather than ignored if still set. A
signing key becomes required for every deployment that is not `default_backend`
under a sandbox kingfisher applies itself. A deployment that sets one has its
existing sessions refused on their next turn. Each is argued below. None would be
cheaper later.
**Cited by symbol, not by line**, for the reason
`2026-09-05-an-answer-that-is-not-prose.md` gives.

## The split today

Two readers of one session, and only one of them uses the backend.

**The agent's side goes through the backend.** `default_backend` builds a
`WorkspaceScopedBackend`, a deepagents `CompositeBackend`, rooted at the session
directory: `ConfinedLocalShellBackend` as the default (shell, `/derived`,
`/scratchpad`), and a `FilesystemBackend` per route in `routed_paths()` (`/data`,
`/memory`, `/.harness`, `/skills/...`). `Kingfisher(cfg, backend=...)` requires a
`BackendFactory`, called per turn with the session directory.

**Kingfisher's side goes to a `Path`.** `SessionRoot.hold` yields one, and every
harness operation on a session is `pathlib` or `shutil` against it:

| What | Symbol | Operation |
|---|---|---|
| Lay the session out | `ensure_session_layout`, `make_session_dirs`, `scaffold_memory` | `mkdir`, `write_text` |
| A caller's files into `/data` | `place_data`, `writable_data`, `protect_data` | `shutil.copy`, `chmod` |
| Outputs back to the caller | `collect_artifacts`, `RunResult.artifacts` | directory walk, then the caller opens the paths |
| The pinned agent | `remember_agent`, `agent_started_with` | `write_text`, `read_text` |
| The conversation | `write_transcript`, `read_transcript` | whole-file write and read |
| A paused turn | `write_paused_state`, `read_paused_state`, `write_pause_mark`, `read_pause_mark`, `clear_pause` | msgpack bytes, JSON |
| The run log | `JsonlRunLogger`, `RunResult.log_path` | append per event, then the caller opens the path |
| The turn lock | `claim_path`, `SessionDirs.create_exclusive` | atomic create |
| Idle time, size, listing, reap | `SessionDirs.mark_used`, `listing`, `remove_tree`, `session_bytes` | `utime`, `stat`, `rmtree` |
| Keeping files off-host | `restore_into`, `keep_from` via `SessionStore` | copy in, copy out |

**What goes wrong.** Give `Kingfisher` a factory whose backend is a remote sandbox.
`place_data` copies the caller's files into a local `/data` the sandbox never sees.
The agent writes `/derived/report.html` in the sandbox. `collect_artifacts` walks
the local `/derived`, finds nothing, and the turn reports success with no
artifacts. Had it found them, `RunResult.artifacts` would still hand the caller
paths to open on a disk that does not have the files. The pin, the transcript and
the lock all live locally, which holds until a second host serves the next turn.

`refuse_unusable_backend` does not catch it. `execution_support` asks whether the
backend runs commands, and `route_coverage` asks whether its routes cover the deny
scopes. Neither asks whether the backend is rooted where kingfisher reads, and a
remote backend passes both.

`_pin_agent_in` records an earlier case of this split: the pin was written to
`<workspace>/sessions/<id>` while the turn ran under a different `SessionRoot`, and
every turn re-resolved its agent from the catalogue. That was fixed by passing the
directory. The fix still assumes a directory.

## The proposal

**Every operation on a session's files goes through that session's backend,
including kingfisher's own.** A session is its backend, and the `Path` stops
being part of the harness's vocabulary. It survives only inside `default_backend`,
which needs one to root its `FilesystemBackend`s and confine its shell.

### What the harness calls

deepagents' `BackendProtocol` already has what the file operations need:
`upload_files(list[tuple[str, bytes]])`, `download_files(list[str])`, `ls`,
`glob`, `read`, `write` (creates or overwrites), `delete`. Kingfisher calls them
directly on the backend object. The deny rules on `/data/**` and `/.harness/**`
live in `FilesystemMiddleware` and not in the backend, so kingfisher's own calls
are not refused by the rules that refuse the agent.

| Today | Through the backend |
|---|---|
| `place_data` | `upload_files` under `/data/`, reporting replacements from `ls /data/` first |
| `collect_artifacts` | `glob` under `/derived/` and `/memory/`, names only |
| `remember_agent`, `agent_started_with` | `write`, `read` of `/.harness/agent.yaml`, signed |
| `write_transcript`, `read_transcript` | `write`, `read` of `/.harness/transcript.jsonl`, signed. It is already written whole each turn |
| paused state and mark | `upload_files`, `download_files` for the msgpack bytes, `write` and `read` for the mark, `delete` to clear, both signed |
| `ensure_session_layout` | `write` of a placeholder under each of `SESSION_DIRS` and `/.harness/`, plus the `AGENTS.md` scaffold |

A new module, `infrastructure/harness/session_files.py`, would own these. It
would be the one place that turns a layout name into a backend path, so
`service.py` stops importing eight path helpers and imports one object.

### Artifacts: names in the result, bytes on request

`RunResult.artifacts` keeps what it is, names relative to the session, and stops
being something to open. A caller fetches one through kingfisher:

    Kingfisher.artifact(session_id, name, *, source_ids=...) -> bytes
    kingfisher artifact --session ID NAME [--out PATH]

It downloads through that session's backend and is checked the way reading a
session already is (`Sessions.session`). A caller who cannot reach the session's
pinned agent gets `UnknownSessionError`, the same answer a wrong id gets. `name`
must fall under `ARTIFACT_DIRS`, so the call cannot reach `/.harness` or `/data`.

Copying every artifact at the end of every turn was considered and not chosen.
It moves bytes nobody asked for, and a turn that produces a large file pays for it
on each later turn. Carrying bytes in `RunResult` has the same cost, and puts it
in every stream event as well.

### The run log stops being session state

Nothing in the library reads `runlog.jsonl`. It reaches a caller only as
`RunResult.log_path`, a local `Path`. *(Not quite: the live driver in
`tests/integration/` read it back for its usage line. Slice 3 found that and gave
the driver a sink of its own to total from.)* It is also the one harness file written by
appending, which the backend cannot do, and the only one whose value is highest on
a turn that crashes before a buffer is flushed.

So it leaves the session. `JsonlRunLogger` becomes a `RunEvents` port the
deployment wires. The default sends each event to the `kingfisher.run` logger, and
a deployment that wants JSONL wires a sink writing it wherever it likes.
`RunResult.log_path` goes, and `RUNLOG` leaves `kingfisher.layout`. Events carry
the session id, so a deployment can still group them by session.

What this gives up: `reap` no longer deletes a session's log with the session.
That is correct for a log, which should outlive what it describes, and it is the
deployment's retention policy, not kingfisher's.

### The lock: the backend grows one method

`BackendProtocol` has no atomic create. `write` overwrites, so two turns can both
"take" a lock and both succeed. The lock stays correct only if the backend offers
an operation that fails when the name is held.

**Kingfisher defines `SessionBackend`, deepagents' protocol plus a claim:**

    class SessionBackend(Protocol):
        def claim(self, name: str, *, stale_after: float) -> bool: ...
        def release(self, name: str) -> None: ...

`claim` returns False when a live claim holds the name. It succeeds over one older
than `stale_after`, the rule `SessionDirs.create_exclusive` and `Session.claim`
implement together today. `default_backend` implements it with
`O_CREAT | O_EXCL` under `/.harness/claim`, which is what `LocalSessionDirs`
already does. A remote adapter implements it with whatever its storage offers,
such as a conditional put or a row lock.

A lock beside the backend rather than on it was considered and not chosen. It
asks less of an adapter author, but it gives a session two things that must be
wired to the same place, which is the split this proposal exists to close.

### Housekeeping: the factory grows a workspace side

A backend is built per turn, for one session, so it cannot list the others.
`kingfisher sessions`, `reap`, idle time and size need something that sees them
all. **The factory gains it:**

    class SessionBackends(Protocol):
        def __call__(self, cfg, session_id, /, *, catalogue=None, runner=None) -> SessionBackend: ...
        def sessions(self) -> tuple[tuple[str, float], ...]: ...   # (id, last used)
        def mark_used(self, session_id: str) -> None: ...
        def size(self, session_id: str) -> int: ...
        def delete(self, session_id: str) -> str | None: ...        # a reason on failure

That is `SessionDirs` keyed by session id rather than by `Path`, and it replaces
`SessionDirs`. `mark_used` stays a port for the reason `SessionDirs.mark_used`
gives: a turn writes inside a session, and an ordinary filesystem leaves the
session's own timestamp alone.

**The factory takes a session id rather than a directory.** Today
`BackendFactory.__call__` receives `session_dir: Path`, which is the assumption
this proposal removes. `default_backend` resolves the id to
`<workspace>/sessions/<id>` itself.

**The command line names it with a setting: `KINGFISHER_BACKEND_FACTORY`.** Every
command in `presentation/cli/__main__.py` builds `Kingfisher(...,
backend=default_backend)`. With a remote backend, `kingfisher sessions`, `reap`
and `artifact` would list, delete and fetch from a local `sessions/` that holds
nothing. The setting takes `module:name`, resolved the way
`KINGFISHER_SESSION_STORE_FACTORY` is today, and names a `SessionBackends`. Unset,
the commands use `default_backend`, as now. The library is unchanged: `Kingfisher`
still takes the factory as a required argument, and the setting only answers the
command line's question.

*Wiring a store* in `decisions.md` left this setting unopened: the command line
"would need one -- left until something wants it". Housekeeping a remote backend's sessions from
the command line is that thing. The name belongs to the backend, not to a store,
so it cannot be read as the copy-in, copy-out setting it replaces.

### Harness state stays in the backend, so it is defended twice

`/.harness` goes through the backend with everything else. A session is then
wholly in one place. That also means a remote shell can reach it, and the agent
has something to gain by rewriting it:

- **the pin**, to name another agent and run under that agent's grants on the
  next turn. The per-turn check in `Sessions._reaches_session` reads the pin to
  decide what the caller may reach, so a rewritten pin is a rewritten answer.
- **the transcript**, to put words in the caller's mouth for the next turn.
- **the paused checkpoint**, to change what a resumed turn does after a human
  approved something else.

Locally, `_harness_denial` in the macOS profile refuses shell writes under
`sessions/*/.harness`, and bubblewrap binds `.harness` read-only. Remotely,
nothing does. Two defences, and both are required.

**1. The backend must refuse the shell two places: `/.harness` and `/data`.**
Part of the contract. `default_backend` already meets it: the fences for
`/.harness`, and `protect_data`'s `chmod` for `/data`. A new check,
`shell_denied`, joins the kit a deployment runs against its own adapter. It
runs `execute` with a write attempt into each and asserts that each write failed
and each file is unchanged. It writes files and runs commands, so it sits beside
the other two that do and stays out of `refuse_unusable_backend`, which only
looks.

`/data` is in the contract even though nothing kingfisher trusts is read from it.
It is the caller's input, and the promise that the agent cannot change it holds on
every backend or it is not a promise. A documented exception for remote backends
was the alternative, and it would have made "read-only" mean something different
depending on where the shell runs.

**2. Kingfisher signs what it reads back, wherever defence 1 is not its own.** The
pin, the transcript, the paused state and its mark are written with an
HMAC-SHA256 over their bytes, keyed by `KINGFISHER_SESSION_KEY`, and verified on
every read. A mismatch refuses the turn with `SessionTamperedError` and is logged.
It is not repaired, because a session whose history was rewritten has no
trustworthy version to restore.

**When the key is required** is decided once, at startup, and fails closed:

| Key set? | Backend and confinement | Result |
|---|---|---|
| yes | anything | every harness file signed and verified |
| no | the factory *is* `default_backend`, and the confinement's mechanism is `sandbox-exec`, `bubblewrap` or `Landlock` | runs unsigned; `.harness` is protected by the fence |
| no | any other factory, including one that wraps `default_backend` | refused at startup |
| no | `KINGFISHER_SHELL_SANDBOX` is `off` or `external` | refused at startup |
| no | a supplied `CommandRunner` (its locality is not knowable at startup) | refused at startup |

The exemption holds because all three mechanisms kingfisher applies itself already
refuse the shell writes under `.harness`: `_harness_denial` in the macOS profile,
the read-only bind in `bubblewrap.py`, and `_session_writable` in `fence.py`, which
grants each session directory and never the session itself, because Landlock
walks up to the nearest grant. `external` is refused because kingfisher cannot see
what the runtime confines. A factory wrapping `default_backend` is refused because
identity is the only test kingfisher can make without trusting the wrapper, and
being unsure should mean a key.

The refusal names the setting and how to make one: `KINGFISHER_SESSION_KEY is not
set, and this deployment needs one because <which row>; generate one with
kingfisher key`. `kingfisher key` prints 32 random bytes, hex-encoded, and writes
nothing, so it cannot leave a key where the agent can read it. `kingfisher doctor`
reports which row a deployment is on, and refuses a key shorter than 32 bytes.

The signature goes in a sibling file (`transcript.jsonl.sig`), not inside the
document, so a reader of the transcript format is not handed a field that means
nothing to it. It records a key id beside the MAC. Rotation is not built (see
below), but the id means adding it later needs no change to what is on disk.

**Why both, when either sounds sufficient.** The deny is only as good as each
adapter, and the kit checks one command rather than every route a shell has to a
file. The signature cannot stop a rewrite. It turns one from an escalation into a
refused turn, and it holds on a backend whose author got the deny wrong.

The key is safe from the agent because it exists only in kingfisher's process.
`shell_env` builds the shell's environment from an allowlist, and this key is not
on it. A test asserts that it never appears in `shell_env`'s output.

## Settled in review

Decided on 2026-09-30, each with the alternative that lost.

**`SessionRoot` is removed.** The backend is the only answer to where a session's
files are. A deployment that mounted object storage as the session directory
mounts it at `<workspace>/sessions` instead, which `configuration.md` already
tells anyone moving sessions to do. Keeping it as `default_backend`'s root only was
the alternative, and it would have left a port that looks general and means
something to one implementation. `ports.md` loses its section.

**`SessionStore` is removed**, with `KINGFISHER_SESSION_STORE` and
`KINGFISHER_SESSION_STORE_FACTORY`, `LocalSessionStore`, `restore_into` and
`keep_from`. Keeping a session's files when the machine may not is what a durable
backend does. A deployment on ephemeral hosts with `default_backend` mounts
durable storage at `<workspace>/sessions`, or writes a backend. A shipped
`synced(backend, store)` wrapper was the alternative. It would have kept today's
deployments working unchanged, at the price of kingfisher carrying a second way
to answer the question this proposal makes the backend answer.

**The two store settings are refused, not ignored.** If either is still set,
configuration fails naming it, saying it was removed, and pointing at the mount
and at `KINGFISHER_BACKEND_FACTORY`. Ignoring it would let a deployment that relied
on it lose durable sessions on upgrade with nothing said, which is the silent
failure this repository keeps naming. `kingfisher doctor`'s advice for a
memory-backed workspace (`presentation/cli/health.py`), which tells it to set
`KINGFISHER_SESSION_STORE`, changes to the same two answers. Neither name is
reused for anything else.

**`KINGFISHER_BACKEND_FACTORY` is added**, for the command line only. See
*Housekeeping* above.

**Artifacts are fetched, not copied.** See *Artifacts* above.

**The run log is not session state.** See *The run log* above.

**`/data` is read-only to the shell on every backend**, as a contract term. See
defence 1.

**`KINGFISHER_SESSION_KEY` is required unless kingfisher's own fence protects
`.harness`**, by the table under defence 2. This was first settled as "always
required" and narrowed the same day. The objection to a local exemption was two
security paths, and a local deployment with the sandbox off having neither
defence. The table answers the second: without a key, sandbox off does not start.
The first is accepted, because the choice between the paths is made once, at
startup, and refuses whenever it cannot tell.

**The key has no default, and must never get one.** The only default available
is a key generated on first start and saved, and the place to save it is
`<workspace>/.kingfisher/`. The macOS profile lets the shell read the whole
workspace (see the comment above `_fence_for`: the confinement denies the home
and re-allows the workspace inside it), so the agent could read the key and sign
whatever pin it liked. A signature the agent can forge is worse than none,
because it looks like protection. On a remote backend, which is where the key is
needed, a generated key is wrong regardless: two hosts serving one session would
each generate their own and refuse each other's turns. `kingfisher key` covers
the convenience a default would have offered, without writing anything.

**Unsigned sessions are refused wherever a key is set.** An unsigned pin cannot
be told apart from one whose signature was deleted, so accepting unsigned files
reopens exactly the hole the signature closes. A one-time migration flag was the
alternative. It would have been the bypass for as long as anyone forgot to turn it
off. So a deployment that sets a key, at launch or later, has its existing
sessions refused on their next turn, and the release note says to reap them. A
deployment on the unsigned row never reads a signature, so nothing changes for it.

**Any supplied `CommandRunner` needs a key**, not only one that is not local. Decided
building slice 2: `runner` is a callable built per turn, so whether what it returns
is local cannot be known at startup, where the rule has to answer. Being unsure is
the case a key is for.

**A paused checkpoint is written in place.** It was staged and renamed, so a write
cut off halfway left the previous checkpoint whole. A backend has no rename. With a
key, half a checkpoint fails its signature and the turn is refused as tampered, which
is the wrong word for a crash but the right outcome: nothing half-written is
resumed. Without one, it fails to load.

**Key rotation is not built.** One key, and changing it refuses every live
session. Acceptable before 1.0. When it is wanted, the shape is one signing key
plus a list of keys still accepted for verification, selected by the key id each
signature already records.

## What this reverses, and what stands

From *Wiring a store*:

- **Reversed:** "the backend is not where files live". After this it is.
- **Reversed:** that storage reaches a session through `SessionRoot` or
  `SessionStore`. Both ports go.
- **Stands:** the backend is the security boundary. This adds to its job. The
  confinement, host-path refusal and route table stay where they are, and
  `refuse_unusable_backend` still runs on every backend resolved.
- **Stands:** a store as a workspace asset stays refused.
- **Stands:** the factory is a callable called per turn, so no two callers share a
  filesystem by accident. It is now called with an id instead of a path.

From *Sessions: what persists and where*: the transcript, the in-memory
checkpointer and the paused-state file are unchanged in what they hold. Only where
they are written from changes, and they are now signed. The run log leaves the
session.

## How it is tested

**A backend with no local directory, used throughout the suite's session tests.**
A dict-backed `SessionBackend` subclassing deepagents' `BaseSandbox`, so
`execution_support` recognises it, with `execute` run against a temporary
directory it never tells kingfisher about. Every test that places data, runs a
turn, fetches an artifact, pins, pauses and resumes runs against it as well as
against `default_backend`. The mutation test that matters: point any one of the
path helpers back at a local `Path` and a test goes red, where today the whole
suite would stay green.

**`shell_denied`**, mutation-tested twice: switch `_harness_denial` off and the
kit fails on `/.harness`; skip `protect_data` and it fails on `/data`.

**Signing**, by rewriting each signed file through the backend between two turns
and asserting the second refuses. Three cases: the file rewritten with its
signature left alone, the signature deleted (the unsigned case), and a signature
copied from another session's file. The third is what catches a MAC computed
over the bytes alone rather than bound to the session and file it signs.

**The key never reaches the shell**, by asserting it is absent from `shell_env`,
and by running `env` through `default_backend` and checking the output.

**When the key is required**, one test per row of the table. The unsigned row
starts and writes no `.sig` files. Each refused row fails at startup, not at the
first turn, with the row named in the message. The case that matters most is a
factory that wraps `default_backend` and returns its result unchanged: it must
still be refused, since a test that passes it would be testing what the wrapper
returns, not whether kingfisher can tell. Mutation: make the exemption test
`isinstance(backend, WorkspaceScopedBackend)` instead of factory identity, and
the wrapper test goes red.

**The old store settings are refused**, each one set on its own, with a message
naming it. **`KINGFISHER_BACKEND_FACTORY`** is tested by running `kingfisher
sessions` and `kingfisher artifact` against a factory returning the dict-backed
backend, and checking they see its sessions and not the local `sessions/`.

**`claim`**, by two threads racing on one session against both backends, with
exactly one winner, carried over from what `SessionBusyError` already measures.

**`Kingfisher.artifact`**, refusing a name outside `ARTIFACT_DIRS` (`/.harness/agent.yaml`,
`/data/x`, `../x`) and refusing a caller who cannot reach the session, with the
same error a wrong id gets.

## What this does not do

It does not ship a remote backend. Kingfisher stays out of owning every backing
store anyone asks for, the reason *Wiring a store* declined `ADAPTERS`. It makes a
remote backend one a deployment can write and have work.

It does not move the confinement. `ConfinedLocalShellBackend`, the fences and
`shell.sb` stay `default_backend`'s, rooted at a local path, because confining a
shell is only meaningful on the host that runs it. A remote backend's isolation is
its own, and `shell_denied` is how a deployment shows it covers the two paths
kingfisher depends on.

It does not change what the agent sees. Routes, virtual paths and `system.md` are
untouched.

## Slices, if this moves

One green commit each, a pull request per slice off `main`.

1. **Data in, artifacts out, through the backend.** `place_data` and
   `collect_artifacts` move to `session_files.py`, `Kingfisher.artifact` and
   `kingfisher artifact` arrive, and the dict-backed test backend with them.
   These are the two that break a remote backend visibly, and they need none of
   the rest. With `default_backend` the behaviour is identical.
2. **`/.harness` through the backend, signed.** Pin, transcript, paused state,
   `KINGFISHER_SESSION_KEY` and the startup rule deciding when it is required,
   `kingfisher key`, the `doctor` row report, unsigned sessions refused where a
   key is set, `shell_denied` in the kit. Signing lands with the move, never after
   it, because the move is what exposes these files to a remote shell.
3. **The run log becomes `RunEvents`.** `RunResult.log_path` and `RUNLOG` go.
   Independent of 2 and could land before it.
4. **`SessionBackend.claim`, the factory's workspace side, and
   `KINGFISHER_BACKEND_FACTORY`.** Replaces `SessionDirs` and `claim_path`. The
   factory takes a session id, and the command line can name one.
5. **`SessionRoot` and `SessionStore` removed**, with their `ports.md` sections.
   The two store settings become refusals, and `doctor`'s advice for a
   memory-backed workspace changes with them. `decisions.md` gains the entry
   recording the reversal, and this document is removed.

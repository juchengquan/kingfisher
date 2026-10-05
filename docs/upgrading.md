# Upgrading: a session is its backend

This note is for a deployment built on kingfisher `main` before #608. Between #608
and #617 a session stopped being a folder on the host and became whatever the
session's backend keeps. Most of what follows exists so that a backend which keeps
sessions somewhere else, such as a remote sandbox, works end to end. Some of it
changes what every deployment writes.

It also covers the earlier change to subagent bundles (#566, #568), because a
deployment from before #608 may well be from before that too. See *Subagent bundles*.

**If you run the default backend on one machine** and don't use `SessionStore`,
`SessionRoot`, `RunResult.log_path` or `RunResult.session_dir`, you probably need
only the first two items of the checklist.

**If you are already on #617 or later**, only the sections whose titles say *after
#617* or later apply to you.

## Session backends always given, after #639

Session backends are always passed now, so what kingfisher used to work out from a
session's directory on this host comes from them instead.

| Before | Now |
|---|---|
| `Kingfisher(cfg, graph=my_graph)` | `Kingfisher(cfg, graph=my_graph, backends=default_backends)`, or the session backends that reach where your graph's backend keeps a session. A graph without `backends=` is **refused**. |
| `run(..., graph=my_graph)`, `stream(..., graph=my_graph)` | Name `backends=` beside `graph=` here too. Without a graph, `backends` still defaults to `default_backends`. |
| `Kingfisher(cfg, backends=default_backends, runner=my_runner)` | `Kingfisher(cfg, backends=DefaultBackends(runner=my_runner))`. `runner=` is gone from `Kingfisher`; `my_runner` is still a callable given the session's directory. |
| `open(cfg, session_id, /, *, catalogue=None, runner=None)` and `aopen` on your `SessionBackends` | Drop `runner`. A subclass of `DefaultBackends` gets the runner it was built with from `super().open(...)`; session backends of your own decide how their commands run themselves. |
| A factory passed as `threads=`, called with the session's directory, `<workspace>/sessions/<id>` | Called with the session's id. A factory that kept a file per session on this host builds that path itself, from its own configuration. A checkpointer passed as `threads=` rather than a factory is unaffected. |

## Renamed after #617

The object that opens each session's backend was called a backend, and its setting
a backend factory. It is a `SessionBackends`, so the names say so now, and the
method that opens a session is named rather than being the object's `__call__`.

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=...)`, and `backend=` on `run()` and `stream()` | `backends=` |
| `default_backend`, `DefaultBackend` | `default_backends`, `DefaultBackends` |
| `__call__(cfg, session_id, /, *, catalogue=None, runner=None)` on your `SessionBackends`, called as `backends(cfg, session_id)` | `open(...)`, with the same arguments, called as `backends.open(cfg, session_id)` |
| `KINGFISHER_BACKEND_FACTORY` | `KINGFISHER_SESSION_BACKENDS_FACTORY`. The old name is **refused**: startup fails with `KINGFISHER_BACKEND_FACTORY was renamed KINGFISHER_SESSION_BACKENDS_FACTORY; set that instead`. |
| `Config.backend_factory`, `configured_backend()` | `Config.session_backends_factory`, `configured_backends()` |

## Async reads after #617

`Kingfisher` has `asession`, `apending`, `aartifact`, `adelete_session` and
`afiles_for`, the async twins of `session`, `pending`, `artifact`, `delete_session` and
`files_for`, for a caller already on an event loop. They reach a session through new methods on `SessionBackends` --
`aopen`, `asessions`, `adelete`, and for a turn on `astream`, `amark_used` and `asize`
-- and through `aclaim` on the backend `open` returns:

- **If your session backends subclass `DefaultBackends` or `SessionBackends`**, there
  is nothing to do. All five are inherited, each running its sync method on
  kingfisher's thread pool. Override one where it is a round trip you can await.
- **If yours subclass neither**, they are refused at construction until they have all
  five. Subclassing `SessionBackends` is the shortest fix.
- **The backend `open` returns needs `aclaim` and `arelease`** beside `claim` and
  `release`. One built on `SessionClaims`, as kingfisher's are, has them already.
- `SESSION_BACKENDS_CONTRACT` has seven more checks, one for each async twin:
  `aopen_reaches_the_session_open_does`, `asessions_lists_what_sessions_does`,
  `adelete_removes_the_session`, `aclaim_and_claim_exclude_each_other`,
  `arelease_gives_back_what_claim_took`, `asize_counts_what_size_does` and `amark_used_moves_the_session_on`, which waits a
  second so a coarse filesystem clock still sees the session move on.
- **A checkpointer passed as `threads=`** is deleted from with its own `adelete_thread`
  on the async path, or on kingfisher's thread pool where it has none or raises
  `NotImplementedError` as langgraph's base saver does. `arun(delete_session=True)`
  deletes this way too. `kingfisher.domain.ports.ThreadStore` is gone; `threads=` is
  typed `Any`, and is still a langgraph checkpointer.

## Checklist

1. **Remove `KINGFISHER_SESSION_STORE` and `KINGFISHER_SESSION_STORE_FACTORY`** if
   you set them. Kingfisher now refuses to start while either is set.
2. **Run `kingfisher doctor` and read its `session files` row.** Nothing signs what
   a session keeps under `/.harness`, so the row says whether anything it can see
   keeps the agent's shell from rewriting it. With your own backend, that is the
   backend's `shell_denied` check (see *For backend authors*).
3. **If you call `Kingfisher(...)` yourself**, check the constructor changes below.
4. **If you read `RunResult.session_dir` or `RunResult.log_path`**, switch to
   `artifacts` with `Kingfisher.artifact`, and to `RunEvents`.
5. **If you wrote your own backend**, it now has to be a `SessionBackends`. See
   *For backend authors*.
6. **If you copied the shipped tools**, decide whether to re-seed them (see
   *Workspace tools*).
7. **If a subagent has its own `tools/` or `skills/` folder, or writes `bundle`**,
   list what it takes from the folder (see *Subagent bundles*). Kingfisher refuses to
   start until you do.

## Settings

| Setting | What changed | What to do |
|---|---|---|
| `KINGFISHER_SESSION_STORE`, `KINGFISHER_SESSION_STORE_FACTORY` | **Removed and refused.** Startup fails with `KINGFISHER_SESSION_STORE was removed: a session's backend keeps it now…` | Unset them. For sessions that outlive the machine, mount durable storage at `<workspace>/sessions`, or use a backend that keeps sessions itself. |
| `KINGFISHER_SESSION_KEY` | **Gone.** It was on `main` for a day after #610, signing the pin, the conversation and a paused turn; nothing signs them now. | Unset it if you set it; `kingfisher doctor` reports it as read by nothing. |
| `KINGFISHER_SESSION_BACKENDS_FACTORY` | **New, for the command line only.** `module:name` of something callable with no arguments that returns your `SessionBackends`. | Set it (the name since *Renamed after #617*) if your session backends keep sessions anywhere but `<workspace>/sessions`, so `kingfisher sessions`, `reap`, `artifact` and `decide` see them. |

## Existing sessions

- **Sessions that a `SessionStore` kept** are not read any more. Copy any you need into
  `<workspace>/sessions/<id>/` before upgrading, or move them into whatever your
  backend now keeps.
- **A session written while a key was set** keeps `.sig` files beside its `.harness`
  files. Nothing reads them, and they go when the session is reaped.
- **The old run log**, `.harness/runlog.jsonl`, is no longer written or read. It's
  safe to delete.

## `Kingfisher(...)` and the one-liners

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=my_factory)` where `my_factory(cfg, session_dir, ...)` is a function | `backends=` takes a **`SessionBackends`**. To build on the default, subclass `DefaultBackends` and override `open(cfg, session_id, /, *, catalogue=None)`. A plain function is refused with a `TypeError` saying so. |
| `default_backend(cfg, session_dir)` called directly | `default_backends.open(cfg, session_id)` takes a session id now. For a folder of your choosing, call `backend_at(cfg, directory)`. |
| `dirs=`, `sessions=`, `session_root=` | **Removed.** A session's backend answers all three: where it is, what it holds, and the housekeeping. |
| `run_events=` | **New.** A sink with `record(event)`. With none given, events go to the `kingfisher.run` logger at INFO. |
| `delete_session(id, forget=...)`, `reap(..., forget=...)` | `forget=` is **removed**: there is no store to keep a copy in. |
| `run(...)` / `stream(...)` with `dirs=` | `dirs=` is **removed**; `run_events=` is added. |

## `RunResult`

- **`log_path` is removed.** What a turn did now goes to a `RunEvents` sink: one
  `record(event)` call per model call, tool call, start and end, each carrying
  `session_id` and `turn_id`. `kingfisher run` and `decide` keep no log file unless
  given `--log FILE`, which appends one JSON line per event.
- **`session_dir` is removed.** `artifacts` lists what a turn left, as names
  relative to the session (`derived/report.md`). Fetch one with
  `Kingfisher.artifact(session_id, name)` or `kingfisher artifact --session ID NAME`.
- **A whole `RunResult` now serialises to JSON**, because nothing in it is a host
  path.

## Public names

- **Removed from `kingfisher`:** `LocalSessionStore`, `SESSION_STORE_CONTRACT`,
  `SESSION_ROOT_CONTRACT`. The `SessionRoot`, `SessionStore` and `SessionDirs` ports
  are gone from `kingfisher.domain.ports`.
- **Added:** `DefaultBackends`, `backend_at`, `SESSION_BACKENDS_CONTRACT`,
  `ArtifactError`, and `Kingfisher.artifact` and
  `Kingfisher.pending` on the service.

## Workspace tools

- **A tool argument named `path` now resolves where the session's backend keeps the
  file.** Three behaviour changes follow:
  - `/skills/...` now reaches the real catalogue file. Before, it pointed at a file
    that didn't exist.
  - `/.harness/...`, and `/memory/...` when a request declined memory, are refused.
    A tool could previously be handed those files.
  - On a backend that doesn't keep the file on this host, the tool gets a readable
    refusal instead of `FileNotFoundError`. To work on any backend, a tool takes
    `runtime: ToolRuntime[ToolContext]` and reads through `runtime.context.backend`
    (see `docs/guides/tools.md`).
- **The six shipped file tools changed signature:** `line_count`, `csv_profile`,
  `csv_columns`, `log_levels`, `status_codes` and the redactor's `mask_secrets` take
  `file_path` and a runtime, and read through the backend. Copies already seeded into
  a workspace keep their old `path` form and keep working on the default backend.
  Re-seed them (`kingfisher seed`) if you move to a backend that keeps sessions
  elsewhere. Seeding overwrites a copy you edited, so carry any edits across first.
  If you call them directly, pass `file_path=` and a `ToolRuntime`.
- **A compiled delegate with a blank `name` or `description` is now refused** (#601).
  The other three readers already refused one.

## Subagent bundles

A subagent's own folder, `subagents/<name>/tools/` and `skills/`, used to grant
everything in it automatically, and an optional `bundle:` key could describe it.
Now the definition lists what it takes from the folder, and only that arrives:

```yaml
tools:
  - "*"                     # keep every catalogue tool, if it had them before
  - name: mask_secrets
    source: bundled         # subagents/<name>/tools/
skills:
  - name: redaction
    source: bundled         # subagents/<name>/skills/
```

- **List every file in the folder.** An unlisted file stops the catalogue loading
  rather than being silently dropped, so you find out on the first start.
- **Add `"*"` if the definition had no `tools:` line.** That used to mean every
  catalogue tool *plus* the folder. A list that names only bundled entries now means
  the folder alone.
- **Remove `bundle:`.** It's refused in YAML and in compiled (`build`) definitions.
- **A compiled subagent can't keep a `skills/` folder.** A compiled graph is never told
  about skills, so the folder is refused. Move the skill to the shared catalogue, or
  read it inside the graph.
- **A portable `SUBAGENTS` entry** (no `build`, often from an installed package) moves
  what it carried out of `bundle` and into the plain fields: `"tools": [the tool
  objects]` and `"skills": an absolute path`. Names aren't accepted there.
- **Agents** may write `source: shared` but not `source: bundled`, since an agent has
  no folder of its own.

`kingfisher doctor` reports any mismatch on its `bundled entries` row before startup
does. `docs/guides/formats.md`, under *Tools and skills of its own*, has the details.

## Command line

- **New:** `kingfisher artifact --session ID NAME [--out PATH]` fetches a file a turn
  produced.
- **Changed:** `sessions`, `reap`, `artifact` and `decide` run on
  `KINGFISHER_SESSION_BACKENDS_FACTORY` when it's set. `decide --session ID` with no decisions
  now finds a session a backend keeps elsewhere, and asks as the caller `--as` names:
  in a workspace with source ids it needs `--as`, and a caller who can't reach the
  session is told it doesn't exist (`Kingfisher.pending` takes `source_ids=` for the
  same check). `doctor` gains a `session key` row,
  and its advice for a memory-backed workspace points at a durable mount or
  `KINGFISHER_SESSION_BACKENDS_FACTORY`.

## For backend authors

A custom backend is now a **`SessionBackends`**: one object that opens a session's
backend per turn, and also answers for every session.

- `open(cfg, session_id, /, *, catalogue=None)` returns that
  session's backend, creating the session if it's new.
- `sessions(cfg)` returns `(session_id, last_used)` pairs. `mark_used(cfg, id)`,
  `size(cfg, id)` and `delete(cfg, id)` do what they say.
- **The backend you return holds the turn lock:** `claim(name, *, stale_after)`
  (which must fail while a live claim holds the name), `release(name)` and
  `held(name, *, stale_after)`. `SessionClaims` implements these over a local folder.
- **Kingfisher reads and writes the session through it:** it places `/data` with
  `upload_files`, lists `/derived` and `/memory` with `glob`, fetches artifacts with
  `download_files`, and reads and writes `/.harness` for the pin, conversation and
  pauses. `/data` has to accept that upload while staying read-only to the agent.
- **Optional:** `host_path(virtual) -> Path | None`, if your files are on this host
  by some means Kingfisher can't see, such as a network mount. It must never name
  another session's file.
- **Check it** with `BACKEND_CONTRACT` (now five checks, including `shell_denied`:
  the shell may not write `/.harness` or `/data`) and the new
  `SESSION_BACKENDS_CONTRACT` (sessions kept apart, persistence across turns, an
  exclusive claim, listing and deleting, and `host_path` staying in its session).

`docs/guides/ports.md` has the details. `docs/decisions.md`, under *A session is its
backend* and *A tool's path is the backend's path*, explains why.

## Errors you may see after upgrading

| Message | Cause | Fix |
|---|---|---|
| `KINGFISHER_SESSION_STORE was removed: …` | The old store setting is still set | Unset it; see *Settings* |
| `TypeError: backends= takes a SessionBackends …` | A plain factory function, or one backend, passed as `backends=` | Subclass `DefaultBackends` |
| `TypeError: … missing 1 required keyword-only argument: 'backends'` | `Kingfisher(...)` called without `backends=`, with or without a graph | Pass `backends=default_backends`, or session backends of your own; see *Session backends always given* |
| `KINGFISHER_BACKEND_FACTORY was renamed …` | The setting's old name is still set | Rename it; see *Renamed after #617* |
| `… is not kept on this host by this session's backend …` | A `path` tool on a backend without local files | Read through `ToolContext`, or give the backend a `host_path` |
| `subagent '…': …/tools/ holds …, which tools: does not list` | A file in a subagent's folder the definition doesn't list | Add it as `{name: …, source: bundled}`, or move it out; see *Subagent bundles* |
| `… lists entries as source: bundled and owns no folder to take them from` | The definition or its folder was renamed, so they no longer pair | Make the folder name and the `name:` match |
| `'bundle' is not a field of this format` | A YAML or compiled definition still writes `bundle:` | List the entries with `source: bundled` instead |
| `'bundle' -- a portable entry carries its own under the plain fields` | A portable `SUBAGENTS` entry still writes `bundle` | Move its contents to `tools` and `skills` |

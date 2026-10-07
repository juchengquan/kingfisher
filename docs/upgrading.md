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
only the first two items of the checklist, and the sections titled *after #667* and
*after #668*, whose renames reach every deployment.

**If you are already on #617 or later**, only the sections whose titles say *after
#617* or later apply to you.

## One session per workspace, after #671

A workspace can hold exactly one session (#672). Nothing changes unless you set
`KINGFISHER_SESSION_ID`; set it, and the workspace holds that one session, laid out
in `sessions/` itself rather than in `sessions/<id>/`.

- **A request naming no session runs in that one**, and its result carries the
  configured id. A request naming any other id is refused as an unissued one is.
  `delete_session` and `reap` empty `sessions/` rather than removing it, and the next
  turn starts a fresh conversation under the same id.
- **A workspace is used one way or the other.** Turning the setting on for a
  workspace whose `sessions/` holds sessions of their own is refused at startup, and
  so is turning it off for one laid out by it. Clear `sessions/`, or use another
  workspace.
- **Session backends of your own** are handed the same `cfg`, and a request naming
  no id runs under `cfg.session_id`. List that id from `sessions(cfg)` if a first turn
  may name it, or the turn is refused. `SESSION_BACKENDS_CONTRACT` opens several ids,
  so it cannot pass against backends serving this mode.
- **On macOS, a shell in a shared workspace can no longer make `sessions/.harness`.**
  Its presence is how a workspace holding one session is told apart, so a shell able
  to make it could lock the workspace. Nothing legitimate wrote there.

## A caller's files named for `inputs/`, after #668

The names for the files a caller hands a turn follow the folder they land in. The
old names are gone rather than kept beside the new ones, so each of these fails
until it is changed.

| Before | Now |
|---|---|
| `kingfisher run --data PATH`, repeatable | `kingfisher run --input PATH`, repeatable. `--data` is refused as an unrecognised argument. |
| `Request(..., data=(path, ...))`, and reading `request.data` | `Request(..., inputs=(path, ...))` and `request.inputs`. `data=` raises `TypeError`. |
| A `RunEvent` of kind `data_placed`, which `kingfisher run` prints as `[data_placed] ...` | `inputs_placed`, with the same text. A consumer switching on `data_placed` never sees one again. |
| `place_data`, `DataPlacement` and `DataError` in `kingfisher.infrastructure.session_files`; `DataBackend` in `kingfisher.infrastructure.harness.backend`; `protect_data` and `writable_data` in `kingfisher.infrastructure.workspace` | `place_inputs`, `InputsPlacement`, `InputsError`, `InputsBackend`, `protect_inputs` and `writable_inputs`, in the same modules. None was ever exported from `kingfisher`. |

- **If you catch `DataError`** around a turn, for a file that is missing, named twice
  or refused by the backend, catch `InputsError`. It is still a `ValueError`.

## Session folders named for which way files go, after #667

A session's `data/` is `inputs/` and its `derived/` is `outputs/`, on disk and in
the paths the agent is given. `memory/` keeps its name. The workspace layout is 3.

| Before | Now |
|---|---|
| `sessions/<id>/data/`, which the agent reads as `/data/<name>` | `sessions/<id>/inputs/`, read as `/inputs/<name>` |
| `sessions/<id>/derived/`, which the agent writes as `/derived/<name>` | `sessions/<id>/outputs/`, written as `/outputs/<name>` |
| Artifact names such as `derived/report.md`, in `RunResult.artifacts` and for `Kingfisher.artifact` and `kingfisher artifact` | `outputs/report.md`. A name under `derived/` is refused as not an artifact. |
| Agent and subagent prompts, skills and tool descriptions that say `/data/...` or `/derived/...` | Say `/inputs/...` and `/outputs/...`. Nothing translates the old paths: a read under `/data/` finds nothing, and a file written under `/derived/` is not returned to the caller. |

- **Delete what an older workspace holds before starting on it.** A workspace with
  sessions in it from layout 2 or earlier is refused at startup, and the message
  lists exactly what to delete: everything in `sessions/`, and for a workspace from
  layout 1, whichever of `.kingfisher/agents`, `runs`, `claims` and `tmp` are still
  there. The marker is brought up to date on the next start. Or point
  `KINGFISHER_WORKSPACE` at a new workspace. There is no migration, so copy anything
  you want to keep out of a session's `derived/` first.
- **If you wrote your own backend**, route `/inputs/` where you routed `/data/`.
  `route_coverage` in `BACKEND_CONTRACT` names a route your backend leaves out, and
  `shell_denied` now checks that the shell cannot write under `/inputs`.
- **What stayed for this change:** `kingfisher run --data`, `Request.data`, the
  `data_placed` event, and `protect_data`, `writable_data`, `place_data`,
  `DataBackend`, `DataError` and `DataPlacement`. They are renamed in the section
  above, *A caller's files named for `inputs/`*.

## Memory guidelines that say memory lasts its session, after #666

With memory on, the model is given guidelines for using it after kingfisher's own
memory section. They are kingfisher's now (#667), `prompts/memory_guidelines.md`,
where they were deepagents' `MEMORY_SYSTEM_PROMPT`: the same template, except that
five lines which promised memory to future conversations say later turns of this
conversation, because `/memory` is deleted with its session. Kingfisher's own memory
section has said the same since #663. Nothing has to change, and two things follow:

- **A deepagents upgrade no longer changes what the model is told about memory.**
  Kingfisher's copy is not re-synced from the library's.
- **A middleware of yours whose class is named `MemoryMiddleware`** still takes the
  memory slot, as it took deepagents' before, so the model gets what yours sends and
  not kingfisher's guidelines.

## Definition folders made only where they are read, after #665

Each kind of definition gets its folder where it is read from, and nowhere else
(#666). A kind moved with `KINGFISHER_SKILLS_DIR` or one of its siblings used to get
an empty folder in the workspace too, which looked like the place to put a definition
and was never read.

| Before | Now |
|---|---|
| `ensure_layout(workspace, authored=...)` made `agents`, `middlewares`, `skills`, `subagents` and `tools` in the workspace | It still does without `catalogue_roots=`. Pass `catalogue_roots=paths.catalogue_roots`, as the README now does, and each is made where it is read from. A `Config` has the same property, and `seed` passes it already. |
| `Kingfisher(cfg, ...)` made all five in the workspace | Makes each where `cfg` reads it from, and none when given `catalogue=`: whoever staged that catalogue made its folders. |
| `LAYOUT_DIRS` in `kingfisher.layout` named the five kinds beside `sessions` and `.kingfisher` | Names only `sessions` and `.kingfisher`, which are always in the workspace. |
| `STARTER_AGENT` in `kingfisher.infrastructure.workspace` | `starter_agent(agents_dir)`, which names the directory it is given. Neither was exported from `kingfisher`. |

- **A folder an earlier start made in the workspace stays**, empty or not. If a
  setting moves that kind elsewhere, nothing reads it, so delete it.
- **Messages name where a kind is read from.** A workspace with no agents says to
  write one in the directory agents are read from, not in `agents/`. The help for
  `run --agent` and the refusal of an unknown `source:` no longer call a kind's
  folder the workspace's.

## Session ids refused unless they are one path segment, after #664

`DefaultBackends` refuses a session id that is not one path segment (empty, `.`,
`..`, or holding `/` or NUL) wherever it builds a path from one: `open`,
`mark_used`, `size` and `delete` (#665). The refusal is the `UnknownSessionError` an
id nobody issued gets. Such an id used to act on whatever path it formed:
`files_for("..")` laid a session out in the workspace's own folder, an absolute id
laid one out wherever it pointed, and `DefaultBackends.delete(cfg, "")` removed every
session in the workspace and reported success.

- **Every id kingfisher issued passes**, as does every folder under `sessions/`.
- **Session backends of your own are not checked for you**, unless they subclass
  `DefaultBackends` and call through to it. Kingfisher hands them only ids it issued
  or they listed, except `Kingfisher.session_size`, which passes on the id it is given.

## `files_for` opens only a session a turn issued, after #663

`Kingfisher.files_for(id)` and `afiles_for` refuse an id your session backends don't
list, with the `UnknownSessionError` a turn naming it gets, and open nothing (#664).
They used to open it through `open`, which makes a session it is asked for, so an id
the caller made up became a session a later turn accepted.

- **If you called `files_for` to start a session under an id of your choosing**, run
  the session's first turn with no `session_id` and no `files=`, then call
  `files_for` with the `session_id` its result carries. A session's id is only ever
  one kingfisher issued.

## Host paths refused anywhere under the workspace, after #658

A file tool handed a host path is refused with `HostPathError` wherever under the
workspace it points (#659). It used to be refused only inside the turn's own session.
Another session's file, a skill, `models.yaml` or an agent definition named by its
host path was accepted wherever the workspace sits outside the host roots kingfisher
lists -- at `/workspace`, which the `Dockerfile` sets, for one -- and a write to it
was mirrored inside the session rather than reaching the path named.

- **A path inside the session** is refused as before, with the virtual path to use.
- **Any other path under the workspace** gets the general refusal, because it has no
  virtual path to offer. An agent or tool of yours that handed the file tools
  absolute paths into the workspace should use virtual paths, or the shell for host
  paths.

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
| `Kingfisher(cfg)` with no `backends=` | **Refused** by Python: `backends` has no default on the constructor (#657). Pass `backends=default_backends`. Only `run()` and `stream()` still fill it in, and only without a graph. |

Every session's backend is now checked when it is opened, beside a pre-built graph
too: it has to run commands as deepagents recognises them, and route every path the
agent is given. A backend that fails used to get as far as `claim` and stop there
with an `AttributeError` naming neither it nor your session backends; now opening the
session raises a `ConfigError` that says which check it failed.

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
3. **If you call `Kingfisher(...)` yourself**, pass `backends=` — it is required now —
   and check the constructor changes in *Session backends always given* and below.
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
  relative to the session (`outputs/report.md`). Fetch one with
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
  session is told it doesn't exist. `artifact` takes `--as` the same way, and
  `Kingfisher.pending` and `Kingfisher.artifact` take `source_ids=` for the same check.
  `doctor` gains a `session files` row, saying whether anything keeps the agent's
  shell out of `.harness`, and reports `KINGFISHER_SESSION_KEY` as read by nothing. Its
  advice for a memory-backed workspace points at a durable mount or
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
- **Kingfisher reads and writes the session through it:** it places `/inputs` with
  `upload_files`, lists `/outputs` and `/memory` with `glob`, fetches artifacts with
  `download_files`, and reads and writes `/.harness` for the pin, conversation and
  pauses. `/inputs` has to accept that upload while staying read-only to the agent.
- **Optional:** `host_path(virtual) -> Path | None`, if your files are on this host
  by some means Kingfisher can't see, such as a network mount. It must never name
  another session's file.
- **Check it** with `BACKEND_CONTRACT` (now five checks, including `shell_denied`:
  the shell may not write `/.harness` or `/inputs`) and the new
  `SESSION_BACKENDS_CONTRACT` (sessions kept apart, persistence across turns, an
  exclusive claim, listing and deleting, and `host_path` staying in its session).

`docs/guides/ports.md` has the details. `docs/decisions.md`, under *A session is its
backend* and *A tool's path is the backend's path*, explains why.

## Errors you may see after upgrading

| Message | Cause | Fix |
|---|---|---|
| `… was laid out by kingfisher layout 2, and this is layout 3 …` | The workspace still holds sessions from before `data/` and `derived/` were renamed | Delete what the message lists, or use a new workspace; see *Session folders named for which way files go* |
| `UnknownSessionError: no session '…'; omit session_id to start one`, from `files_for` | An id no turn issued, such as one made up to start a session, or one that is not a single path segment | Run the session's first turn without `files=`, then open the id its result carries; see *`files_for` opens only a session a turn issued* |
| `HostPathError: '…' is a host path, and file tools take virtual paths rooted at the workspace — …` | A file tool given an absolute path under the workspace but outside this session, such as another session's file or `models.yaml` | Use a virtual path, or the shell for host paths; see *Host paths refused anywhere under the workspace* |
| `… holds one session laid out directly in it -- .harness/ is there -- …` | `KINGFISHER_SESSION_ID` is unset on a workspace that was run with it | Set it to that session's id, clear `sessions/`, or use another workspace; see *One session per workspace* |
| `KINGFISHER_SESSION_ID is set, and … holds sessions of their own -- …` | The setting was turned on for a workspace that already holds sessions | Clear `sessions/`, unset it, or use another workspace; see *One session per workspace* |
| `KINGFISHER_SESSION_STORE was removed: …` | The old store setting is still set | Unset it; see *Settings* |
| `TypeError: backends= takes a SessionBackends …` | A plain factory function, or one backend, passed as `backends=` | Subclass `DefaultBackends` |
| `TypeError: … missing 1 required keyword-only argument: 'backends'` | `Kingfisher(...)` called without `backends=`, with or without a graph | Pass `backends=default_backends`, or session backends of your own; see *Session backends always given* |
| `a pre-built graph needs session backends named beside it …` | `run()` or `stream()` given `graph=` without `backends=` | Name `backends=` beside the graph |
| `ConfigError: … is not recognised by deepagents as running commands …` or `… routes nothing covering …` | A session backend your `open` returned has no shell, or leaves a path unrouted | Subclass `SandboxBackendProtocol`, or route the path; see *For backend authors* |
| `KINGFISHER_BACKEND_FACTORY was renamed …` | The setting's old name is still set | Rename it; see *Renamed after #617* |
| `… is not kept on this host by this session's backend …` | A `path` tool on a backend without local files | Read through `ToolContext`, or give the backend a `host_path` |
| `subagent '…': …/tools/ holds …, which tools: does not list` | A file in a subagent's folder the definition doesn't list | Add it as `{name: …, source: bundled}`, or move it out; see *Subagent bundles* |
| `… lists entries as source: bundled and owns no folder to take them from` | The definition or its folder was renamed, so they no longer pair | Make the folder name and the `name:` match |
| `'bundle' is not a field of this format` | A YAML or compiled definition still writes `bundle:` | List the entries with `source: bundled` instead |
| `'bundle' -- a portable entry carries its own under the plain fields` | A portable `SUBAGENTS` entry still writes `bundle` | Move its contents to `tools` and `skills` |

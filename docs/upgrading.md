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
only the first three items of the checklist.

## Checklist

1. **Remove `KINGFISHER_SESSION_STORE` and `KINGFISHER_SESSION_STORE_FACTORY`** if
   you set them. Kingfisher now refuses to start while either is set.
2. **Decide whether you need `KINGFISHER_SESSION_KEY`.** Run `kingfisher doctor`; its
   `session key` row says. If it's needed, generate one with `kingfisher key`. Store it
   where you keep secrets, not in the workspace, and give every host that serves the
   same sessions the same key.
3. **Expect existing sessions to be refused if you set a key.** Their stored files
   aren't signed. Reap them (`kingfisher reap --older-than 0`) or let them expire.
4. **If you call `Kingfisher(...)` yourself**, check the constructor changes below.
5. **If you read `RunResult.session_dir` or `RunResult.log_path`**, switch to
   `artifacts` with `Kingfisher.artifact`, and to `RunEvents`.
6. **If you wrote your own backend**, it now has to be a `SessionBackends`. See
   *For backend authors*.
7. **If you copied the shipped tools**, decide whether to re-seed them (see
   *Workspace tools*).
8. **If a subagent has its own `tools/` or `skills/` folder, or writes `bundle`**,
   list what it takes from the folder (see *Subagent bundles*). Kingfisher refuses to
   start until you do.

## Settings

| Setting | What changed | What to do |
|---|---|---|
| `KINGFISHER_SESSION_STORE`, `KINGFISHER_SESSION_STORE_FACTORY` | **Removed and refused.** Startup fails with `KINGFISHER_SESSION_STORE was removed: a session's backend keeps it now…` | Unset them. For sessions that outlive the machine, mount durable storage at `<workspace>/sessions`, or use a backend that keeps sessions itself. |
| `KINGFISHER_SESSION_KEY` | **New, and required for most custom setups.** It signs the pinned agent, the conversation and a paused turn. | Required unless the backend is `default_backend` itself **and** the shell runs under Kingfisher's own sandbox (sandbox-exec, bubblewrap or Landlock). Required for a custom backend (including a subclass of `DefaultBackend`), a pre-built `graph=`, a supplied `runner=`, or `KINGFISHER_SHELL_SANDBOX` set to `off` or `external`. At least 32 bytes; `kingfisher key` prints one. |
| `KINGFISHER_BACKEND_FACTORY` | **New, for the command line only.** `module:name` of something callable with no arguments that returns your `SessionBackends`. | Set it if your backend keeps sessions anywhere but `<workspace>/sessions`, so `kingfisher sessions`, `reap`, `artifact` and `decide` see them. |

## Existing sessions

- **Sessions that a `SessionStore` kept** are not read any more. Copy any you need into
  `<workspace>/sessions/<id>/` before upgrading, or move them into whatever your
  backend now keeps.
- **Once a key is set, sessions written without one are refused** on their next turn
  with `SessionTamperedError: … is not signed`. An unsigned file can't be told apart
  from one whose signature was deleted, so there's no migration flag.
- **The old run log**, `.harness/runlog.jsonl`, is no longer written or read. It's
  safe to delete.

## `Kingfisher(...)` and the one-liners

| Before | Now |
|---|---|
| `Kingfisher(cfg, backend=my_factory)` where `my_factory(cfg, session_dir, ...)` is a function | `backend=` takes a **`SessionBackends`**. To build on the default, subclass `DefaultBackend` and override `__call__(cfg, session_id, /, *, catalogue=None, runner=None)`. A plain function is refused with a `TypeError` saying so. |
| `default_backend(cfg, session_dir)` called directly | `default_backend(cfg, session_id)` takes a session id now. For a folder of your choosing, call `backend_at(cfg, directory)`. |
| `dirs=`, `sessions=`, `session_root=` | **Removed.** A session's backend answers all three: where it is, what it holds, and the housekeeping. |
| `run_events=` | **New.** A sink with `record(event)`. With none given, events go to the `kingfisher.run` logger at INFO. |
| `delete_session(id, forget=...)`, `reap(..., forget=...)` | `forget=` is **removed**: there is no store to keep a copy in. |
| `run(...)` / `stream(...)` with `dirs=` | `dirs=` is **removed**; `run_events=` is added. |

## `RunResult`

- **`log_path` is removed.** What a turn did now goes to a `RunEvents` sink: one
  `record(event)` call per model call, tool call, start and end, each carrying
  `session_id` and `turn_id`. Note that `kingfisher run` configures no logging, so a
  CLI user no longer gets a per-session log file.
- **`session_dir` is removed.** `artifacts` lists what a turn left, as names
  relative to the session (`derived/report.md`). Fetch one with
  `Kingfisher.artifact(session_id, name)` or `kingfisher artifact --session ID NAME`.
- **A whole `RunResult` now serialises to JSON**, because nothing in it is a host
  path.

## Public names

- **Removed from `kingfisher`:** `LocalSessionStore`, `SESSION_STORE_CONTRACT`,
  `SESSION_ROOT_CONTRACT`. The `SessionRoot`, `SessionStore` and `SessionDirs` ports
  are gone from `kingfisher.domain.ports`.
- **Added:** `DefaultBackend`, `backend_at`, `SESSION_BACKENDS_CONTRACT`,
  `ArtifactError`, `SessionTamperedError`, and `Kingfisher.artifact` and
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

- **New:** `kingfisher key` prints a session key. `kingfisher artifact --session ID
  NAME [--out PATH]` fetches a file a turn produced.
- **Changed:** `sessions`, `reap`, `artifact` and `decide` run on
  `KINGFISHER_BACKEND_FACTORY` when it's set. `decide --session ID` with no decisions
  now finds a session a backend keeps elsewhere. `doctor` gains a `session key` row,
  and its advice for a memory-backed workspace points at a durable mount or
  `KINGFISHER_BACKEND_FACTORY`.

## For backend authors

A custom backend is now a **`SessionBackends`**: one object, called per turn with a
session id, that also answers for every session.

- `__call__(cfg, session_id, /, *, catalogue=None, runner=None)` returns that
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
| `KINGFISHER_SESSION_KEY is not set, and this deployment needs one because …` | A setup that needs a key, with none set | `kingfisher key`, then set it |
| `KINGFISHER_SESSION_KEY is N bytes; it must be at least 32` | Key too short | Generate one with `kingfisher key` |
| `TypeError: backend has to answer for every session …` | A plain factory function passed as `backend=` | Subclass `DefaultBackend` |
| `SessionTamperedError: … is not signed` | A session from before the key was set | Reap it |
| `SessionTamperedError: … is not what kingfisher wrote there` | A stored session file was changed | The session can't be trusted; start a new one |
| `… is not kept on this host by this session's backend …` | A `path` tool on a backend without local files | Read through `ToolContext`, or give the backend a `host_path` |
| `subagent '…': …/tools/ holds …, which tools: does not list` | A file in a subagent's folder the definition doesn't list | Add it as `{name: …, source: bundled}`, or move it out; see *Subagent bundles* |
| `… lists entries as source: bundled and owns no folder to take them from` | The definition or its folder was renamed, so they no longer pair | Make the folder name and the `name:` match |
| `'bundle' is not a field of this format` | A YAML or compiled definition still writes `bundle:` | List the entries with `source: bundled` instead |
| `'bundle' -- a portable entry carries its own under the plain fields` | A portable `SUBAGENTS` entry still writes `bundle` | Move its contents to `tools` and `skills` |

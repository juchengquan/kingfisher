# Session-scoped workspaces and a system skill catalogue

**Status:** agreed design, not yet implemented
**Date:** 2026-08-16

Turning kingfisher from a personal agent that owns one durable project directory
into something an API can put in front of many callers, without inventing a
second architecture to maintain alongside the first.

## The shape

Two changes, and they are independent of each other:

1. **Skills and subagents move out of the workspace.** A system catalogue on the
   host is shared by every session, plus per-request uploads fetched by id from
   a catalogue service.
2. **Everything else becomes session-scoped.** `/data`, `/derived`, `/memory`
   and `/runs` live under a per-session directory, materialised on session
   creation and handed back as a manifest when the run ends.

The mechanical core is one line. `LocalShellBackend(root_dir=…)` currently roots
at the workspace; it roots at the session directory instead. Every virtual path
then resolves inside the session for free, and the agent never learns that
session ids exist.

## Layout

```
<workspace>/sessions/<session_id>/        <- backend root
├── data/                 materialised once, read-only
├── derived/              written; returned in the manifest
├── memory/               written; returned in the manifest
├── runs/<turn>/          per-turn scratch
└── skills/uploaded/      fetched by id, this session only

<catalogue_dir>/                          <- host, outside any workspace
└── <skill-name>/SKILL.md
```

Routes:

| Virtual path | Backend | Mode |
|---|---|---|
| `/data/` | `<session>/data` | read-only |
| `/skills/system/` | `<catalogue_dir>` | read-only |
| `/skills/uploaded/` | falls through to the session root | read-write |
| everything else | `<session>/…` (the shell backend) | read-write |

`CompositeBackend` matches the longest route prefix first, so
`/skills/uploaded/` needs no route of its own — it lands in the session
directory through the default backend.

`SkillsMiddleware` takes both as sources, which is a facility it already has:

```python
sources = [("/skills/system/", "System"), ("/skills/uploaded/", "Uploaded")]
```

## Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **One model, not two.** Session-scoped everywhere; local personal use becomes a pinned session id. | Two modes means a fork in backend construction, layout, git bookkeeping and sweep, plus both tested forever. They drift. |
| 2 | **Writes come back as a manifest.** `RunResult` carries the paths changed under `/memory` and `/derived`, relative to the session root. The caller persists them. | Kingfisher stays honestly stateless. Returning contents inline balloons the response and forces a copy even when the filesystem is shared. |
| 3 | **The catalogue is a host directory**, `KINGFISHER_SKILLS_DIR` / `KINGFISHER_SUBAGENTS_DIR`, defaulting to package-bundled. Parsed once per process, reloadable explicitly. | Bundled-only needs a release to add a skill. Fetch-at-boot makes the process unstartable when the catalogue service is down. |
| 4 | **A name collision between a system skill and an upload is a `CapabilityError`.** | deepagents' default is silent override by the later source, which lets tenant content stand in for vetted content. `build_agent` already refuses unknown names rather than quietly doing less; this is the same failure pointing the other way. |
| 5 | **Provision by id, activate by name.** `Request` grows `skill_refs` / `subagent_refs` carrying ids; `capabilities.skills` keeps selecting by name. | deepagents requires a skill's frontmatter `name` to equal its directory name, and the model picks skills from the prompt by name and description. An opaque id cannot be the identity without the model seeing `skl_abc123`. |
| 6 | **A `SkillStore` Protocol in the domain, injected at the entrypoint.** | The same pattern `domain/session.py` already uses for `ThreadStore`: state the requirement without importing a client. An HTTP client inside kingfisher would point a dependency at your service. |
| 7 | **The session directory is the backend root**; the agent never sees a session id. | `/data` keeps meaning `/data`, so the system prompt stays byte-identical across sessions and prompt caching still hits. Cross-session traversal becomes impossible rather than merely discouraged. |
| 8 | **Stateless processes, stateful service.** Shared checkpointer, session dirs on shared storage. | "Stateless" here means no process affinity, not no state — a materialised `/data` is state. `checkpointing.py` is already written as a one-function swap. |
| 9 | **`sweep()` comes off the request path.** Explicit session delete, plus a TTL janitor as backstop. | Today's count-based sweep runs per request and keeps the newest N by mtime. Under shared storage that deletes *other tenants'* live sessions. |
| 10 | **`/data` is materialised once, at session creation.** | It is read-only by design, so there is nothing to reconcile mid-session. A caller wanting different data starts a new session. |

## Corrections to the original sketch

- **`/reports` no longer exists.** PR #8 removed it; `LAYOUT_DIRS` is now
  `data, derived, skills, subagents, memory, runs`. The durable destination is
  `/derived` — *"anything a run should outlive goes to `/derived`, whatever it
  is called."* It was missing from the original list and is the one that most
  needs the manifest.
- **`/runs` is already session-scoped** (`runs/<session_id>/<turn>/`). Under the
  new root it collapses to `/runs/<turn>/`, since the session is implicit.
- **"Loaded once" is not the win.** A whole agent rebuild is ~8ms; the catalogue
  scan is a fraction of that. The reason to centralise is *provenance* — one
  reviewed, versioned set every tenant shares instead of an unauditable copy per
  workspace. Caching is incidental. Where "loaded once" genuinely pays is
  uploads, whose ids are stable and content immutable.

## A side effect worth having

Routing `/skills/system/` outside the workspace puts it beyond `execute`'s
reach. `_skill_denials` currently admits its permissions are *"a real boundary
only for a request that did not activate the shell"*, because filesystem
permissions apply to file tools and the shell bypasses them. Once the catalogue
is not under the shell's root, a non-activated skill is unreachable by any
means.

## Sequenced plans

Each produces working, testable software on its own.

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | Session-rooted backend and layout. Local behaviour preserved by pinning a session id. | — |
| **2** | System catalogue: skills and subagents read from host directories, `/skills/system/` routed out. | 1 |
| **3** | Upload provisioning: `SkillStore` port, `skill_refs` on `Request`, collision rejection. | 2 |
| **4** | Manifest: `RunResult` reports what changed under `/memory` and `/derived`. | 1 |
| **5** | Lifecycle: `sweep()` off the request path, explicit delete, TTL janitor. | 1 |
| **6** | Shared checkpointer and shared-storage validation. | 1 |

Phase 1 is load-bearing for everything. Phases 4, 5 and 6 are independent of 2
and 3 and can be reordered against them freely.

## Still undecided

These did not come up in the design session and each could change a phase:

- **Tenancy.** Is a workspace per-tenant, or is one workspace shared with
  sessions as the only boundary? Decision 7 makes sessions isolated from each
  other, which may be sufficient — but nothing yet says who may activate which
  system skills. `Capabilities.intersect` exists for a service to clamp with;
  what does the clamping.
- **Shared storage.** `LocalShellBackend` runs real subprocesses against a real
  filesystem. Whether the chosen shared store supports that (NFS/EFS yes;
  S3-fuse, questionable) needs proving before phase 6 rather than after.
- **Concurrency within one session.** `allocate_turn` is atomic via `mkdir`, but
  nothing serialises two simultaneous requests for the same `session_id` against
  one checkpointer thread.
- **Manifest granularity.** Changed-path detection by mtime, by content hash, or
  by recording writes as they happen. Affects whether phase 4 is cheap.
- **Migration.** Existing workspaces have `data/`, `memory/` and `runs/` at the
  root, not under `sessions/<id>/`. Whether they are migrated, or simply
  abandoned, is a call nobody has made.

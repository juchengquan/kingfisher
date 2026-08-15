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
| **6** | Sqlite configured for more than one process; storage guarantee pinned. | 1 |

Phase 6 was specified as "move the checkpointer to a shared store", on the
reading that sqlite is single-writer and would serialise many users. Measured,
that was wrong in both directions and the phase changed shape.

Within one process sqlite is not a bottleneck at all: sixteen threads, 320
checkpoint writes, 57ms, no errors. Against a model call of seconds, it is not
worth a database.

Across processes it was broken, but by kingfisher rather than by sqlite. The
connection was opened at defaults — no busy timeout, no WAL — so a process that
found the file locked failed instead of waiting, and did so inside `setup()`,
before serving anything. Measured through the real service: **3 of 30 processes
crashed, intermittently**. With `busy_timeout` set *before* the WAL pragma, and
the pragma tolerated when another process wins the race to set it: **0 of 30**.

So a shared database is not what phase 6 needed; it is what a deployment needs
when it outgrows one host, because sqlite's locking over a network filesystem is
not dependable. That seam already exists — `Kingfisher(threads=…)` takes any
saver — and is the whole of the change when the day comes.

Phase 1 is load-bearing for everything. Phases 4, 5 and 6 are independent of 2
and 3 and can be reordered against them freely.

## Tenancy (decided 2026-08-16, after phase 4)

Three things were true and none of them was written down:

- **`Capabilities.intersect` had no production call sites.** It is implemented
  and tested, and both `Request` and `Capabilities` state that a service clamps
  with it before a request runs. Nothing did. Every request got everything the
  deployment wired.
- **`session_id` is caller-supplied and is the only key to anything.** It names
  `<workspace>/sessions/<id>` — holding `/data`, `/derived`, `/memory` — and the
  checkpointer thread that *is* the conversation. At `uuid4().hex[:12]` it
  carried 48 bits, which is fine for avoiding collisions and far too little for
  something that grants access.
- **A skill's `allowed-tools` is prompt text, not enforcement.** deepagents
  renders it into the skills listing and binds nothing. What binds tools is
  kingfisher's own `ToolAllowlist`.

| # | Decision | Why |
|---|---|---|
| T1 | **The boundary is outside kingfisher, with one guard inside.** No tenant concept in `Request` or `Config`. | `Capabilities` already says authorisation is not the request's job, and a tenant field would make kingfisher decide who may see what. But the contract that a service derives session ids was unwritten and failed silently, which is the asymmetry worth fixing. |
| T2 | **Session ids are issued, not accepted.** Full `uuid4().hex`; a supplied id may resume an existing session but never create one. | Kingfisher cannot tell a service-derived id from a forwarded one — but it does know which sessions exist. Splitting resume from create makes the id a bearer credential, and 128 bits makes guessing infeasible. A service that forwards user input now gets "no such session" instead of a silent cross-tenant read. |
| T3 | **`Kingfisher(grants=…)` clamps tools and catalogue names; uploaded definitions authorise themselves.** | A closed allowlist cannot cover uploads — their names come from their own frontmatter and are unknowable when grants are set, so clamping them would silently break phase 3. An upload is text the caller could have put in `task`, and `allowed-tools` grants nothing, so the tool clamp is the boundary that matters. |
| T4 | **One *process*, many sessions.** Per-tenant `Kingfisher` objects inside it are cheap enough to be an ordinary choice, not a fallback. | Everything shared is deployment-authored — catalogue, `PROMPT.md`, grants — and nothing caller-written reaches workspace level, which phase 3 secured by landing uploads in the session. |

T4 was first written as "one instance, many sessions", dismissing per-tenant
instances as costing "an instance and a workspace each". Measured, that was
wrong, and the correction changes what the decision is about:

| | time | memory |
|---|---|---|
| resolving `Kingfisher` (the deepagents import) | 1310 ms | +115 MB |
| first instance | 2 ms | +1 MB |
| each further instance *in the same process* | 1.1 ms | +0.16 MB |

The cost is the **process**, not the instance. Twenty tenants as twenty
processes is 2.3 GB and 26s of startup; as twenty objects in one process it is
118 MB and 1.3s. So process count follows *concurrency*, not tenancy — and a
per-tenant instance, which buys a private catalogue, `PROMPT.md` and grants,
costs 160 KB. At a few hundred it stays fine; past that each holds an open
sqlite handle (`service.py`, eager `build_checkpointer`) and wants pooling.

**Concurrency is a separate matter, and not yet available.** `stream()` is
synchronous, so a process serves one request at a time. deepagents itself is
ready — the graph has `astream`, every backend method has an async twin,
`LocalShellBackend.aexecute` uses `asyncio.to_thread`, the async file paths run
through `_get_backend_and_key` so the host-path guard still applies, and
`AsyncSqliteSaver` exists. Kingfisher is not: sqlite is single-writer, so async
would move the bottleneck rather than remove it, and ~67ms of blocking
orchestration per turn (git 31.5ms, agent build ~30ms, `collect_artifacts` and
`protect_data` growing with session size) would hold the loop for every other
caller. Async belongs after phase 6's shared checkpointer, not before it.

**Two consequences for phase 5.**

Retention counts sessions globally, so one busy caller evicts another's session.
Under T4 that is no longer only an ops concern: reaping has to be explicit,
not a global newest-N.

`pre_run_commit` has to leave the request path, and the reason is correctness
rather than its 31.5ms. It commits the *workspace* repo on every turn, so under
T4 concurrent turns race on `.git/index.lock` — measured at **seven of eight
concurrent commits failing**, silently, since a failure returns `None`. And
after phase 1 the tracked tier is `skills`, `subagents`, `PROMPT.md` and
`.gitignore`, all deployment-authored and none of them modifiable by a run. So
it spends most of a turn's blocking time producing a restore point for content
that cannot change, and fails to produce it whenever two callers overlap.

## Still undecided

These did not come up in the design session and each could change a phase:

- ~~**Tenancy.**~~ Answered above (T1–T4).
- **Quotas.** Nothing bounds what one caller can consume — sessions opened,
  disk used, turns run. T4 puts every caller in one process, so a single caller
  can starve the others. Out of scope for the six phases, but it is the next
  thing a real deployment would hit.
- ~~**Shared storage.**~~ The guarantee kingfisher's correctness actually rests
  on is narrower than "does a shell work here": `allocate_turn` is atomic only
  because `mkdir` fails on an existing name. Verified exclusive across eight
  processes claiming 200 names, zero double-claims, and pinned by a test in
  `test_workspace.py`. A store that cannot honour that would silently let two
  turns share a directory — the defect the turn tier exists to fix. That is the
  question to ask of NFS, EFS or S3-fuse, and it is now the one a test asks.
- **Concurrency within one session.** `allocate_turn` is atomic via `mkdir`, but
  nothing serialises two simultaneous requests for the same `session_id` against
  one checkpointer thread.
- **Manifest granularity.** Changed-path detection by mtime, by content hash, or
  by recording writes as they happen. Affects whether phase 4 is cheap.
- **Migration.** Existing workspaces have `data/`, `memory/` and `runs/` at the
  root, not under `sessions/<id>/`. Whether they are migrated, or simply
  abandoned, is a call nobody has made.

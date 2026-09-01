# Nothing at rest on this machine

**Status:** partly built -- six of twenty-three decisions, and one of them
reversed. It stays here because the half that gives the document its title is
untouched: nothing yet keeps a session's working files off local disk. Several
claims are still unmeasured and are marked; the constraint that drives it arrived
on 2026-08-23, after most of the exploration, and rearranged it.
**Date:** 2026-08-21
**Re-checked:** 2026-08-31, when this became the only design document left. Two
rows of *What the constraint touches* had drifted and are corrected below.
`build_backend` has since been split -- *A folder handed in, a command handed
off*, 2026-08-26 -- which was the groundwork N6 and N7 need, so the next step
here no longer starts by moving that function.

**Audited 2026-09-01, decision by decision, and "not implemented" was too
simple.** Six of the twenty-three are built, and they are exactly the ones N2
asked to be built first: the parts that need no memory-backed filesystem. One is
contradicted by what was learned building its neighbours. See *What has been
built* below before planning anything from this document -- the parts that
remain are the mirage-shaped half, and they are genuinely untouched.

A deployment must not keep data on the machine it runs on. Session files,
results, the agent's notes — none of it may live on local disk. Where it *does*
live is the deployment's business, reached through a door kingfisher does not
look behind. A local directory is an acceptable implementation of that door; it
is not an acceptable assumption.

This began as *"should kingfisher use [mirage](https://docs.mirage.strukto.ai/home/introduction)
as its filesystem"* and spent most of its length answering the wrong question.
The constraint is the answer to why, and it reverses several conclusions reached
before it was known. Those reversals are recorded rather than tidied away.

## What has been built

*Added 2026-09-01. Their decisions now also live in `docs/decisions.md`, under
*Sessions: what persists and where*, because that is where a settled one belongs.
They are left in the table below rather than deleted, so the argument for each
stays next to the ones that still depend on it.*

| # | State | Evidence |
|---|---|---|
| N13 | **built** | `domain/transcript.py` -- a session's history as records kingfisher owns. `test_nothing_stored_belongs_to_a_framework`. |
| N14 | **built** | The transcript keeps tool calls and results. `test_what_the_agent_did_is_kept_not_only_what_it_said`. |
| N15 | **built** | The checkpointer is `InMemorySaver` and the transcript is what persists. The three things per-session sqlite bought were re-measured and all survive by another route. |
| N16 | **holds, by construction** | The saver is built per turn -- `build_session_checkpointer(session_dir)` at `service.py:1249` -- and released when the turn ends, so nothing carries a turn's graph state forward and `TodoListMiddleware`'s does not survive either. Traced rather than inferred, but not separately asserted by a test: a deployment injecting a persistent `threads` factory would take this back without anything saying so. |
| N20 | **built, and wider than asked** | `workspace_fs.py` reads filesystem type, size, cgroup limit and swap; `presentation/cli/health.py` reports them. The decision asked for arithmetic; what exists also names the swap case, where an oversized tmpfs is paged out rather than failing -- data at rest, arrived at silently. |
| N22 | **built** | `run_dir` and `log_path` are typed `Path` so `json.dumps` raises, and `service/payloads.py` is the single place that omits them. |
| **N11** | **contradicted** | It argued the quota could be metered on the tool-call hook, since kingfisher wraps every call. `_refuse_if_over_budget` now records why that fails: **`execute` writes without any file tool seeing it**, so a turn already running can exceed the bound and only a filesystem quota underneath could stop it. The check is between turns "and never during one". N19 leaned on N11 becoming load-bearing; it cannot, as written. |

Everything else -- N1 to N10, N12, N17 to N19, N21, N23 -- is untouched. There is
no `mirage` anywhere in the tree, no store port in `domain/ports.py`, and
`session_max_bytes` is still `int | None = None`, so N19's "stops being optional"
has not happened.

## What the constraint touches

More than the filesystem. Kingfisher writes to local disk in four places:

| what | where | what it holds |
|---|---|---|
| session files | `sessions/<id>/{data,derived,memory,runs}` | uploads, results, the agent's notes |
| **conversation history** | sqlite — `checkpoint_db_path` | **every message to and from the model** |
| run log | `log_path(state_dir, session_id)` | per-event record, token usage — reaches the caller on `Result.log_path` |
| uploads | definitions unpacked into the session | skills and subagents a request brought |

The second row is the one most worth naming. It is the conversation itself,
usually the most sensitive thing in the system, and today it is a sqlite file on
the host. Any design that addresses only the first row has solved a third of the
problem and should not claim otherwise.

*Corrected 2026-08-31.* The table named `session_db_path` beside
`checkpoint_db_path`; there is no such symbol, and `checkpoint_db_path` in
`infrastructure/harness/checkpointing.py` is the whole of it. The row's point is
unchanged and the sqlite file is still on the host.

*Corrected 2026-08-31.* The run log was described as "read by nothing but a
test". It is not: `service.py` puts it on `Result.log_path`, so it reaches the
caller in-process. The service then goes out of its way to keep it off the wire
-- `payloads.py` types `run_dir` and `log_path` as `Path` "precisely so
`json.dumps` raises on them". That is this document's own constraint already
being enforced at one boundary, by hand, in one place. It strengthens the case
rather than weakening it: a host path that must not leave the machine is today
guarded by a deliberate `TypeError`, and nothing generalises that.

## What is already right

Files coming *in* already go through a door. `domain/ports.py`:

> **`FileStore`** — *"A remote caller has no host paths, so `Request.inputs` and
> `data` cannot express what they want — they name an id instead, and a store the
> deployment wired resolves it. Kingfisher never receives bytes over its own wire
> and never holds them."*

That is exactly the principle this document needs, already committed to and
already load-bearing. `DefinitionStore` is its twin for a request's own skills
and subagents.

**Files going out do not work that way.** `artifacts(session_dir)` returns a
list of relative paths and the caller opens them off the host. So the system is
store-shaped on the way in and disk-shaped on the way out, and the constraint
lands entirely on the side that is disk-shaped.

## The change, stated plainly

| | today | under the constraint |
|---|---|---|
| files in | a store the deployment wires | unchanged — already right |
| working files during a turn | directories on the host | memory |
| results and the agent's notes | paths on the host | a store, symmetric with the input side |
| conversation history | sqlite on the host | kingfisher's own transcript records, through the same store |
| run log | a path on the host | the same treatment |

Two independent pieces: **a door for outputs**, and **memory instead of
directories**. They can be built in that order, and the first is worth having on
its own.

## Decisions

| # | Decision | Why |
|---|---|---|
| N1 | **A read *and* write store port, wired by the deployment.** | Write-only would mirror `FileStore` more neatly and cannot do the job: `/memory` has to be *read back* when the next turn starts. One port covers fetching what a session had and saving what it produced. Mirage stays strictly behind it, so where the bytes go is the deployment's choice and "a local directory counts as remote" is true by construction. |
| N2 | **The port is built first, and without mirage.** | It is the missing half of a symmetry this codebase already committed to, and it is useful whether or not any filesystem changes. Once it exists, whether working files live in memory or in directories is a separate and reversible decision — which is what makes a `0.0.5` dependency survivable. |
| N3 | **Persistence does not go through a mirage mount.** | Mounting S3 as `/derived` is mirage's actual pitch, and it welds the deployment's storage choice to mirage's supported-resource list. It also puts a `0.0.5` library directly in the path of the data. A problem with the filesystem library should be a problem with the filesystem, not with somebody's results. |
| N4 | **Working files live in memory, one workspace per *turn*.** | The backend is rebuilt every turn on purpose, and the reason is measured: *"a cached one would serve a stale view of a directory the user can edit between turns... 9.2ms median... Against a turn of 1.5-1.9s that is 0.6%."* Holding one live per session instead would grow memory with `KINGFISHER_SESSION_TTL_S` (seven days), lose every live session on restart, and **pin a session to one process** — so `kingfisher-service` could no longer be replicated. That is a decision about how kingfisher scales, not about a filesystem library. |
| N5 | **Memory is filled from the store at turn start and written back as it changes.** | Under N4 a memory-backed workspace begins empty, so the previous turn's files must be read in. This was criticised earlier as reimplementing the operating system's file cache — **and that criticism does not survive the constraint.** The alternative to memory is not a local directory, because local directories are the thing being avoided. The copy is from a *store*, and there is no cheaper way to read from one. |
| N6 | **Mirage is the *default inside* `WorkspaceScopedBackend`, never `backend=` directly.** | Settled by reading deepagents. `filesystem.py:1667` raises `NotImplementedError` when permissions are set on an execution-capable backend whose rules are not route-scoped, and the scoping check opens `if not isinstance(backend, CompositeBackend): return False`. `LangchainWorkspace` supports execution and is not a composite, so passing it directly makes `permissions=` illegal — and `permissions=` is how a request's capabilities are enforced. Mirage's own documented example is exactly the shape that cannot be copied; it works there because it passes no permissions, and kingfisher's list is never empty (`agent.py:932`). |
| N7 | **Every route gets a small kingfisher adapter that joins its prefix back on.** | Routing strips the prefix — `composite.py:_route_for_path` turns `/data/x.csv` into `/x.csv` for the route's backend — so a route needs a backend rooted at that folder, and mirage documents no scoped view of a sub-mount. Cheap and already proven here: `skills_backend` returns a `ReadOnlyStoreBackend` satisfying the same protocol over a repository. |
| N8 | **Tools you write receive a handle, through a factory at the registration site.** | `TOOLS = [needs_files(line_count)]`, with `def line_count(path, files)`. The alternative — `files: Annotated[Files, InjectedToolArg]`, which `langchain_core` supports — puts a framework import into every tool module. The factory keeps the property `line_count`'s own docstring celebrates: an ordinary function a test can call directly, with no decorator and no `.invoke`. |
| N9 | **`Files` is kingfisher's protocol — `open`, `read_bytes`, `write` — never mirage's `Workspace`.** | Annotating with mirage's type pushes a `0.0.5` dependency past kingfisher's boundary onto every deployment that writes a tool, and welds the implementation into everyone's signatures. `open` returning a file-like is also what keeps `line_count` counting 2 GB without holding it. |
| N10 | **Kingfisher keeps writing the system prompt.** `build_system_prompt` is not used. | The prompt is the cached prefix — `agent.py:941` describes the memory block even when memory is denied, *"because it is the cached prefix and must not vary per request."* A prompt generated from the mount configuration varies with exactly the thing that must not vary, and cannot carry what the page is for: where scratch goes, why `$TMPDIR` and never a literal `/tmp`, what survives a turn. |
| N11 | **The quota is metered on the tool-call hook and refuses during a turn.** | `session_bytes`' docstring says *"there is nothing to intercept mid-turn"* — that is about **events**, not polling, and kingfisher already wraps every tool call. **This needs no mirage; crediting it to mirage was an error in the first draft.** Under memory-backed files it becomes necessary rather than merely better: overshooting a disk is recoverable, and overshooting memory kills the process. |
| N12 | **The mirage type is never called `Workspace` in kingfisher's own code.** | Kingfisher already means the deployment root by that word — `KINGFISHER_WORKSPACE`, `workspace_fs.py`, `cfg.workspace`. Under N4 a kingfisher workspace *contains* sessions and each turn *has* a mirage one, so the same word would sit at two levels of one hierarchy. `test_kind_vocabulary.py` exists to catch this. |
| N13 | **Conversation history becomes kingfisher's own message records, not langgraph checkpoints.** `langgraph-checkpoint-sqlite` and `aiosqlite` are dropped. | A checkpoint preserves resumable *graph* state — pending writes, channel versions, position in the graph — and kingfisher does not resume a graph. `service.py:1075` passes `{"thread_id": session_id}` with **no `checkpoint_id`**, and there is no `interrupt()` anywhere: a turn runs to completion or fails, and the next turn continues a *conversation*. So the machinery is paid for and unused, and with it two direct dependencies. What the domain actually means by a session's history is a transcript, and this repo already works this way elsewhere — `domain/` owns `RunEvent` and `RunResult` while the harness translates. |
| N14 | **The transcript keeps tool calls and results, not only human and assistant text.** | "Flattened" has a cheap reading that discards everything but the final answer, and it would make the agent forget its own work: the next turn would see *"summarise /data/x.csv"* → *"Done, 40 rows"* with no record that `csv_profile` ran or what it returned, so it re-does things and cannot refer to what it did. Portability does not require that loss — it requires not storing *LangChain's classes*. Roles and tool calls are what every provider's wire format already carries, so a plain record of them is as portable as a bare transcript and does not lie about what happened. |
| N15 | **`InMemorySaver` backs the turn; the transcript is what persists.** | It ships in `langgraph-checkpoint` 4.2.0, already present transitively, so no dependency is added to remove two. The framework gets the checkpointer it expects for the length of a turn, and what crosses the store boundary is kingfisher's records. Note what is given up deliberately: sqlite committed each checkpoint durably, so a crash mid-turn now loses that turn's history — the same trade already accepted for files, with the same mitigation of saving at tool-call boundaries. Sqlite also served *"one session... served by more than one process over its life"*; session claims serialise that instead, and already do. |
| N16 | **`TodoListMiddleware`'s state does not survive a turn.** | It keeps a todo list in graph state rather than in messages, so a transcript loses it between turns. Recorded as a decision rather than discovered as a bug: a plan is per-turn working state, and a turn that needs to carry one across should write it to `/memory`, which is the thing that exists for exactly that. |
| N17 | **Every session folder is a real file on tmpfs — none is served from the store on demand.** | The file tools go through a backend and could be served from the store with no filesystem at all; `skills_backend` already does exactly that over a repository, and it would remove the load-at-start, the scan-for-changes and the memory metering in one move. Rejected because **scripts wander the whole session**: a skill's script opens `/data`, writes to `/derived`, and reads `/memory`, and only a real file can be opened. Splitting by who touches what would mean some folders a script can open and some it cannot — a third view to explain in a prompt that already spent six paragraphs on two. The costs (U1, U2, U7) are the price of the shell keeping the reach `system.md` promises it: *"nothing in the workspace is out of the shell's reach."* |
| N18 | **One long-lived container serves every session.** Not one per turn. | The container is how the service is deployed, not a sandbox wrapped round each turn; a container per turn would pay startup on every request and reload every session's files from the store each time, which is most of the reason for having a container at all. It also keeps the property `checkpointing.py` relies on — *"one session is still served by more than one process over its life, and a resumed turn may land anywhere"* — which a container per *session* would break by pinning. What it costs is that memory is shared: see N19. |
| N19 | **`KINGFISHER_SESSION_MAX_BYTES` stops being optional, and the sum of the quotas must fit the tmpfs.** | Under N18 every session's files live in one memory-backed mount whose size is fixed for the whole mount, so a single session that fills it starves every other session in the container. Today the quota is **unset by default** — `.env.example` has it commented out, and `_optional_int` treats unset as unbounded. That is survivable when the backing store is a disk and fatal when it is shared memory. So the quota becomes required, N11's mid-turn metering becomes load-bearing rather than an improvement, and `reap` stops being disk housekeeping and becomes how memory is reclaimed — which makes its schedule a correctness concern rather than a tidiness one. |
| N20 | **`kingfisher doctor` checks the tmpfs arithmetic.** | Doctor exists to catch *"everything that stands between this install and a run"*, and a tmpfs larger than the container's memory limit is exactly that shape: silent, catastrophic, and cheap to detect. It can read the mount's size, the cgroup limit and whether swap is enabled, and refuse before a model call is spent. Without it the failure is either 203 MB quietly on a swap file — the one thing this document exists to prevent, arrived at with no error — or a container killed mid-turn. Both are things a deployment discovers at the worst moment and neither is visible in code review. |
| N21 | **No admission control in the prototype.** A maximum number of live sessions is deferred. | Under load the right answer is a cap and a fixed slice each, so `sessions × slice + process < limit` holds by arithmetic checked at the door rather than by hope. But kingfisher has no concept of a maximum live session count, and inventing one to prove a filesystem design is scope the measurement did not force. The measurement forced N19 and N20; a full capacity model is what this becomes when a second session competing for space is a real problem rather than an imagined one. |
| N22 | **The run log is scratch, and `log_path` is documented as in-process only.** | Nothing in kingfisher reads it: `read_usage()` has exactly one caller and it is `tests/unit/test_runlog.py`. So it lives on the tmpfs and goes with the container, and making it durable would be work in service of a reader who does not exist. What is worth writing down is the part that is **already true**: `RunResult.log_path` hands the caller a filesystem path, which only helps a caller in the same process. Over HTTP a remote caller receives a path to a file on a machine it has never seen. That is a defect today, before any of this, and it should be said rather than inherited. |
| N23 | **Later, the run log stops being a file.** Not in the prototype. | `stream()` already sends the caller every event as it happens; the log is a written copy of the same information. Emitting rather than writing removes a file instead of relocating one, and fixes the remote-caller problem properly rather than by persistence. Deferred because it changes what `RunResult` promises, which is public API, and that does not belong in a prototype about where files live. |

## What mirage is, measured

`mirage-ai==0.0.5` installed into a throwaway venv, scripts written into a RAM
mount and executed. Not read from the documentation, which was wrong or silent
on three of these.

| | |
|---|---|
| package | `mirage-ai` **0.0.5**, plus `pydantic-monty` **0.0.19** and a spawned runtime binary |
| shape | a library. `LangchainWorkspace(ws)` implements deepagents' `SandboxBackendProtocol` |
| resources | Disk, RAM, and ~40 remote |
| shell | its own bash — a tree-sitter parser and custom executor, no `/bin/bash` |

### Scripts stored in memory do run

Under **both** runtimes. Mirage reads the source out of the mount and hands it
to the interpreter, so even `local` — which cannot see the mount — executes a
script living in one. `/skills` as a memory-backed mount is not a problem.

### But a script cannot both import a package and read the files

| runtime | script in RAM runs | reads the mount | third-party import |
|---|---|---|---|
| **monty** (default) | ✅ | ✅ | ❌ `ModuleNotFoundError` |
| **local** | ✅ | ❌ `FileNotFoundError` | ✅ |

Measured, not inferred. `wasi` needs packages baked into a custom CPython build;
`sandlock` is Linux-only and also blind to the mounts. Only a sandbox runtime
(Docker, smolvm, E2B) reconciles them, by putting a real filesystem behind a real
interpreter — **and no sandbox is available in this deployment.**

So skills that ship code needing real packages cannot run. The `pdf` skill's own
reference documents `import pypdfium2` and `from PIL import Image` — a compiled
extension and an imaging library. This is a consequence of the constraint, not a
preference: third-party code reading real files *is* local disk access.

### `MountMode.EXEC` is undocumented, and is not a capability boundary

A `WRITE` root refuses execution with `python3: root mount '/' is not in EXEC
mode`, exit 126. It looked like a boundary kingfisher cannot express —
`/skills` executable-but-read-only, `/data` writable-but-not-executable, so
vetted scripts run and agent-authored code does not. **Tested, and it is not
one:**

- **`EXEC` implies write.** A directory mounted `EXEC` accepted
  `echo 'print(2)' > /skills/sneak.py`, and the file landed on host disk.
- **`EXEC` is not scoped to its mount.** A script on a `WRITE` mount is refused
  when no mount is `EXEC`, and runs as soon as *any other* mount is `EXEC`.

Recorded for a second reason: the same `WRITE` root refusing alone and
permitting beside an `EXEC` sibling is behaviour the documentation does not
mention, which is what `0.0.5` looks like in practice rather than in argument.

### Monty is a subprocess

`pydantic-monty-runtime` describes itself as *"the monty CLI binary — spawned as
worker subprocesses"*. So "no subshell" means no `/bin/bash`; it does not mean
no processes. Arguments that leaned on "nothing to confine" — including the
claim that `confinement.py` simply disappears — are weaker than they looked. It
is still a far better process to confine than `subprocess.run(shell=True)`: one
known binary, invoked by kingfisher, with its own sandbox.

## Exposing the tree as a real path

Everything above assumes the memory-backed files are reachable only through
mirage's own command surface. Mirage can also register them with the kernel, so
`/data/report.pdf` is a genuine path an ordinary program can `open()` — with the
bytes still only in memory.

That would dissolve three problems at once: no temp file is needed for an
imported tool (U4), the `local` runtime can see the files *and* import packages
so the bind stops binding, and the `pdf` skill works. It is the difference
between a compromised version of this design and a good one.

Read from the source, because the published documentation is silent on all
three:

| backend | a real OS path? | needs |
|---|---|---|
| **VFS** (default) | ❌ | nothing. *"the mount lives only inside mirage's own filesystem... with nothing registered with the kernel"* — this is what every experiment above used |
| **FUSE** | ✅ | macFUSE, fuse3 or WinFsp — a driver on every machine |
| **FSKIT** | ⚠️ reads only, partially | macOS 15.4+, no kernel extension |

FSKIT looked like the way to avoid a driver on macOS. Its own docstring rules it
out for anything that writes:

> the macFUSE FSKit shim **flushes pages a file did not already have (a new
> file, or truncate-then-write) as NUL bytes**, a limit pinned in
> `integ/fuse/truth_fskit.json`

Creating a new file — an agent writing a result — can silently write zeros.
Reads have a matching caveat: files whose size is not known in advance *"will
read as empty"*.

Two things follow. The good version of this design needs a kernel driver
everywhere it runs, including every developer's machine. And at `0.0.5` the
source is the specification: `EXEC` is undocumented, its semantics are
inconsistent, and this corruption limit lives in a docstring.

## The container changes the question

If kingfisher runs inside a Linux container, three things change at once, and
together they undo much of the reasoning above.

**The sandbox exists after all.** Every earlier decision took "no sandbox
available" as fixed, and that is what ruled out the `local` runtime and with it
every skill that ships real code. A container *is* the boundary.
`confinement.py` already says so: `EXTERNAL` means *"the runtime already
confines this process — a container mounting only the workspace."* So `local`
becomes acceptable, third-party packages come back, and `confinement.py` is
switched off by configuration rather than deleted.

**FUSE stops needing a kernel extension.** `fuse3` is a package inside the
container image, not macFUSE on somebody's laptop. It still typically wants
`/dev/fuse` and elevated privileges, which is worth confirming against whatever
runs the container.

**And tmpfs does the whole job with no library at all.**

```
docker run --tmpfs /workspace:size=1g,noswap ...
```

Files live in RAM. They are real paths, so any program opens them and any
package reads them. Nothing is written to the disk. And the size limit is
**enforced by the kernel**, returning `ENOSPC` — which is the one thing
`RAMResource()` cannot do at any price, since it takes no arguments and exposes
no accounting (U7).

Swap is the caveat and it is the whole promise: a tmpfs page can be swapped to
disk under memory pressure, which is data at rest by another route. `noswap`
(Linux 6.4+) or a swapless host closes it; `ramfs` also does, at the cost of
having no size limit at all.

So under a container, the constraint is satisfied by a mount flag. What mirage
would still add is the thing set aside at the very first question: **S3, Drive,
Postgres and the rest mounted as paths.** That is real, kingfisher has no story
for it, and it is a much smaller and more reversible change than replacing the
filesystem.

## The tmpfs rule, measured

Run against Docker 29.5.3, alpine, cgroup v2. The capacity model rests on this
and it is not what the obvious reading predicts.

| memory folder vs container limit | swap | result |
|---|---|---|
| **larger** | on | **203 MB silently swapped to disk.** No error, the write succeeded |
| **larger** | off | **`Killed`.** The container dies, taking every session in it |
| **smaller** | off | clean `No space left on device`, nothing swapped, container survives |

> **Rule: the tmpfs must be smaller than the container's memory limit, and swap
> must be off.**

Both halves are load-bearing. With swap on and a tmpfs larger than the limit,
the kernel pages tmpfs out — which is *data at rest on the machine*, arrived at
silently, and is the single thing this whole document exists to prevent. With
swap off it becomes an OOM kill instead, which is loud but takes every session
in the container.

Two supporting measurements:

- **Deleting frees the memory.** 200 MB written moved `memory.current` from
  1 MB to 202 MB; deleting the file returned it to 3 MB. So `reap` genuinely
  reclaims, and N19's capacity model has something real underneath it.
- **tmpfs counts 1:1 against the container's memory limit.** So "smaller" is not
  enough — the tmpfs plus the process's own working set must fit, and the
  process was using memory before any file existed.

## What the constraint invalidated

Recorded because the reasoning was sound before the constraint and is wrong
after it, and a reader deserves to see which is which.

- **Copying results out to local disk.** The earlier design flushed `/derived`
  and `/memory` to the host. That is writing data at rest on the machine. N1
  replaces it.
- **Materialising a file so an imported tool can `open()` it.** The earlier
  design wrote bytes to a temp file for the length of a call. Whether a
  transient file satisfies the constraint is **U4 below**; if it does not,
  imported path-taking tools cannot be supported at all.
- **The service running disk-backed mounts while the library runs memory.** That
  was the answer to memory being unbounded. Under the constraint the service
  cannot use disk either, so the memory quota (N11) has to carry the whole
  weight.
- **"Only scratch needs to be in memory."** Argued four times from four
  directions, and correct only while local directories were permitted for
  everything else.

## What breaks at more than one instance

Out of scope: the prototype is single-instance. Written down because the thing
that breaks is not the thing anyone expects, and because two decisions here
would be shaped differently if it were in scope.

**Load balancing is not the problem. The claim is.**

```python
self._claims: Path = self.cfg.state_dir / "claims"     # service.py:498
```

A claim is taken with `create_exclusive` — an atomic mkdir only one caller can
win. That is mutual exclusion among processes *sharing a filesystem*. Give three
pods their own tmpfs and there are three claims directories: two pods each claim
the same session successfully, and both run turns against it. That is the
failure `claim_stale_after` was written after — *"a second caller took the
session while the first turn was still running"* — promoted from a narrow race
to the normal case.

**Everything else already survives.** N5 has each turn read its state from the
store and write back during the turn, so an instance holds nothing another needs.
The transcript is in the store (N13). `reap` is already documented as a janitor
on its own schedule rather than something a request does. The claim is the only
thing pinned to a machine.

So the shape, when it matters, is a **claim port** in the same style as
`FileStore` — Redis, Postgres, or a cloud lock — with session affinity as an
optimisation that is never relied upon. Affinity may make it faster; it must not
make it correct, or every rollout becomes a correctness event.

**And one consequence for N18.** It justified a long-lived container partly
because a session's files stay warm between turns. That holds only while the
same instance serves the next turn. Without affinity, every turn reloads from
the store anyway — which was the main argument *against* a container per turn.
At more than one instance the gap between those two options is much smaller than
it looks here.

## Still to settle

| # | Claim | How |
|---|---|---|
| U1 | The per-call listing that finds changed files is cheap enough | **The prototype's first measurement.** A turn with many calls, and a `/derived` with many files. If it is not cheap, save at turn end instead and accept the wider loss window |
| U2 | Filling memory from the store at turn start is cheap enough | **The prototype's second measurement**, and under N18 it is paid only on a cold start rather than every turn — which is most of why N18 chose a long-lived container. A session holding a 40 MB upload is the case to try |
| U3 | ~~Routes work over a memory mount~~ **Moot.** tmpfs is an ordinary filesystem, so the backend is unchanged — `FilesystemBackend` over directories that happen to be in memory | Dropped with mirage |
| U4 | ~~Does a transient temp file satisfy the constraint?~~ **Moot under a container.** With tmpfs or FUSE the path is real and memory-backed, so nothing is materialised | Confirm the container can mount tmpfs, or get `/dev/fuse` |
| U5 | ~~The conversation history has somewhere to go~~ **Settled by N13–N16.** What remains is the write pattern: dumping the whole transcript at every save point is O(history) per save, and quadratic over a long session where sqlite appended one row per checkpoint | Design. Track which records have been written and append only what is new |
| U6 | ~~The run log has somewhere to go~~ **Settled by N22, N23.** It needs nowhere to go — nothing reads it | Decided |
| U7 | ~~Memory can be metered per session~~ **Settled by measurement.** A full tmpfs returns `ENOSPC`, which kingfisher can catch and refuse, provided the tmpfs is smaller than the container limit and swap is off | Measured. **tmpfs solves this and `RAMResource` cannot.** A sized tmpfs is enforced by the kernel; `RAMResource()` takes **no arguments** — no size limit, no eviction, no way to query bytes held. `Limit` in the API bounds a command *result*, not storage. So the meter is kingfisher's, and inherits U1's cost |
| U8 | ~~Two pre-1.0 packages under every turn~~ **Moot for the prototype**, and the design *removes* two dependencies rather than adding any | Returns only if mirage does |

## The prototype

**Single instance. Filesystem only. The transcript rewrite is not in it.**

The two are independent, and running both at once means a failure could be
either. They also have opposite risk profiles: rewriting a checkpointer is
fiddly but its risk is *"did I serialise this correctly"*, which care and tests
resolve. The filesystem piece is the reverse — little to get wrong, and two
unmeasured costs (U1, U2) plus a whole design resting on a container behaving as
measured. A prototype should attack the unknowns, not the known-fiddly.

So the sqlite database rides along as **just another file in the session**. It is
what "treat the conversation as files" meant before portability came up, it needs
no code, and it is a harder test of the store port than a transcript would be: a
port that can carry a live database — three files, WAL sidecars and all — across
a container restart can carry anything.

One caveat that comes with it: copy the session at a **quiet moment, between
turns**. A live sqlite database copied file-by-file can be captured torn. Between
turns nothing is writing, which sidesteps it without needing the backup API.

### What it has to prove

> Start a session. Take two turns. **Kill the container.** Bring it back. Take a
> third turn that refers to something from the first — and then look at the host
> filesystem and find nothing.

If that passes, the design is real. If it does not, nothing further matters.

### What is deliberately not in it

- **The transcript records (N13–N16).** Next, once the filesystem holds.
- **Admission control (N21).** Deferred until a second session competing for
  space is a real problem.
- **Multi-instance.** The claim is a local directory and does not survive it;
  see above. Single instance is the whole scope.
- **Mirage.** tmpfs supplies memory-backed real files, so the backend stays
  `FilesystemBackend` over directories that happen to be in memory. N6, N7 and
  N12 are dormant — kept because the reasoning is worth having if the
  S3-as-a-path question comes back.

## The order to build in

1. **Run in a Linux container**, `KINGFISHER_SHELL_SANDBOX=external`, with the
   session tree on a tmpfs. A deployment decision, no code. The sizing rule is
   not optional: **tmpfs + the process's own memory < the container limit, swap
   off.** Measured above; the alternatives are silent swapping or a dead
   container.
2. **`kingfisher doctor` checks that arithmetic (N20)**, and
   `KINGFISHER_SESSION_MAX_BYTES` becomes required (N19). Small, and it is what
   stops the deployment discovering either failure at the worst moment.
3. **The store port (N1, N2).** The prototype's real subject. A container's
   tmpfs is gone when the container is, so everything a session must keep goes
   through here.
4. **Then the transcript records (N13–N16)**, dropping `langgraph-checkpoint-sqlite`
   and `aiosqlite`.
5. **Then, if ever, mirage** — judged only on whether S3, Drive and Postgres as
   mounted paths are worth it. Nothing above depends on the answer.

## Recommendation

**Containerise, use tmpfs, build the store port. Do not adopt mirage for the
filesystem.**

The constraint is real and it does make memory-backed working files necessary —
that part of this document stands, and it reversed an earlier draft that had
argued the opposite four times. What changed at the end is *how* memory-backed
files are obtained. Inside a Linux container, a sized tmpfs gives:

- files in memory, never written to the machine's disk
- **real paths**, so any program opens them and any package reads them
- a **kernel-enforced size limit**, which `RAMResource()` cannot express at all
- no library, no driver, no version risk

Against that, mirage's virtual filesystem costs two pre-1.0 packages under every
turn, needs a kernel driver to reach parity on the one property that matters
(a real path), rules out third-party code without one, and — measured here —
has undocumented mode semantics and a documented data-corruption limit on the
one backend that avoids the driver.

What mirage would still be good for is the question this exploration opened
with and then set aside: **mounting S3, Drive, GitHub and Postgres as paths.**
Kingfisher has no story for that, a session can only see what a caller uploaded,
and the runtime bind that wrecks everything above does not apply — reading data
through the file tools never involves running code. That is one optional mount,
not every path in every session, and it is where the version risk is containable.

So the honest summary of twenty-odd questions: the constraint is best met by a
container flag and a port kingfisher was already half-way to having, and mirage
is worth revisiting for the capability it adds rather than the one it replaces.

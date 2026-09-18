# A mount kingfisher can name

**Status:** proposed, nothing built. Moved here on 2026-09-18 from the untracked
`plan/` it was parked in, which had asked to be resolved either way.
**Date:** 2026-09-10.
**Half its premise went on the way**, and the argument below is left as it was
written rather than edited around the hole -- see *What changed under this* before
reading it, because two of the symbols it cites no longer resolve.
**Occasion:** the question of whether a LangChain/deepagents filesystem backend
could be built on FUSE. The conclusion is that FUSE is not a filesystem
replacement and should never be argued for as one; it is the one mechanism that
turns something which is *not* a filesystem into a path, and that is the only
job worth giving it here.
**Cited by symbol, not by line**, for the reason
`docs/design/2026-09-05-an-answer-that-is-not-prose.md` gives: a document
written to sit unbuilt outlives its line numbers. A `grep` for the name lands;
a line number is a claim with a shelf life.

## What changed under this

*Added 2026-09-18, on moving the file into `docs/`. This is the check the index page
asks for -- a proposal is only true on the day it was written -- and it found one of
the two jobs below already gone.*

**The second job is dead.** *A store-backed catalogue whose skills can be run* rests
on a store-backed skills catalogue existing, and #502 removed it: `SkillRepository`
now promises a `root: Path` outright, because *"deepagents reads skills off a
filesystem and the shell runs a skill's scripts from where they sit"*. `StoreBackend`
and `skills_backend` are both gone, so the two symbols this document cites for that
job do not resolve -- in a document that cites by symbol so that a `grep` lands.
`decisions.md` records the removal under *The catalogue*. The section *`/skills` needs
no code* below is about a problem that no longer exists.

**The first job stands, and is the whole of what is left.** S3, Drive and Postgres as
paths the agent can read, which kingfisher still has no story for.

**Every measurement stands.** The `noexec` table, `CAP_SYS_ADMIN`, the bounding-set
drop and a dead mount answering `ENOTCONN` are facts about Linux and Docker rather
than about this codebase, and nothing here has moved under them. They are the part of
this document worth keeping whatever becomes of the design.

## The question this answers, and the one it does not

`docs/decisions.md`, under *Sessions: what persists and where*, closes the
mirage exploration by naming what was left over:

> What it would still be good for is the question that exploration opened with
> and set aside: S3, Drive and Postgres mounted as paths, which kingfisher has
> no story for and which is a far smaller change than replacing the filesystem.

That is this. Two jobs, and they are the same mechanism seen from two ends:

**A remote store the agent can read as paths.** Today a store reaches the file
tools through `skills_backend`'s `StoreBackend` and reaches the shell not at
all.

**A store-backed catalogue whose skills can be *run*.** `shell_env` sets
`KINGFISHER_SKILLS` only when `catalogue_root` returns a directory, and says
why: a skill's scripts are executed by the shell, and *"a store has no path for
the shell to reach"*.

What this is **not** is a replacement for the workspace filesystem. That was
proposed, measured and refused under *Containerise and use a sized tmpfs*, and
nothing measured since disturbs it.

## What was measured, on 2026-09-10

The mirage entry in `docs/findings.md` asks to be re-measured before it decides
anything. It has been. Docker 29.5.3, `python:3.12-slim`, `fusepy` over
`libfuse2`, a tmpfs at `/workspace` sized like `compose.yaml`'s.

### Execution under `noexec` is narrower than the compose comment says

`compose.yaml` keeps Docker's default `noexec` and records that `sh script.sh`
and `python script.py` still work *"because the interpreter reads the file
rather than exec'ing it"*. That holds. What it does not name is the ceiling
that will actually be hit:

| | `noexec` | `exec` |
|---|---|---|
| `./s.sh`, shebang, `chmod +x` | denied | runs |
| `sh s.sh`, `python s.py` | **runs** | runs |
| a copied ELF binary | denied | runs |
| `dlopen` a `.so` | `failed to map segment from shared object` | runs |
| a venv's `bin/python` | **runs** | runs |
| a venv's `bin/pip` | denied | runs |

`noexec` blocks `mmap(PROT_EXEC)`, not only `execve`. So a session-local venv
holding a compiled wheel -- numpy, pandas, pyarrow -- installs cleanly and dies
on import. The venv's own `python` survives because `bin/python3` is a symlink
and the kernel checks the target's mount, which is the image's; that also makes
`python -m pip` work where `./bin/pip` does not, which is the workaround worth
knowing before anyone reaches for the `exec` flag.

### FUSE costs `CAP_SYS_ADMIN`, and the capability costs the barrier

| container | `/dev/fuse` | mount |
|---|---|---|
| as kingfisher ships | absent | `fuse: device not found` |
| `--device /dev/fuse` | present | `fusermount: mount failed: Operation not permitted` |
| `+ --cap-add SYS_ADMIN` | present | mounts |

The device alone is not enough. And the capability is not free, which is the
finding that should decide anything decided here:

    without SYS_ADMIN:  mount -o remount,exec /workspace  ->  permission denied
    with SYS_ADMIN:     mount -o remount,exec /workspace  ->  silent success
                        tmpfs /workspace rw,nosuid,nodev,relatime   <- noexec gone
                        ./x.sh -> BINARY-RAN

One command from the agent's own shell, and a fence does not take it back.
Landlock governs filesystem access by path; `mount` is gated by the capability,
not by the ruleset. So this holds under `auto` as well as under `external` --
`compose.yaml` names the mode, and neither value of it stands behind `noexec`.

### The drop works, and the obvious way to write it does not

Mounting while the capability is held and dropping it afterwards keeps the
mount serving -- reads and `execve` both -- while taking `mount` away from
every child. But only a **bounding-set** drop does anything:

| | `CapBnd` | `/workspace` after | own binary |
|---|---|---|---|
| no drop | `a82425fb` | `noexec` gone | `BINARY-RAN` |
| dropped from permitted+effective | `a82425fb` | `noexec` gone | `BINARY-RAN` |
| dropped from **bounding** | `a80425fb` | `noexec` kept | `Permission denied` |

The middle row is the whole risk of this design. The process runs as uid 0, so
every `execve` refills the permitted set from the bounding set -- a shell
spawned after a permitted-only drop has `CAP_SYS_ADMIN` back. Code that
genuinely drops the capability and a barrier that is gone anyway, and no
difference visible from either the source or `CapBnd`.

`prctl(PR_CAPBSET_DROP, CAP_SYS_ADMIN)` through `ctypes` returns 0 and holds,
so this needs no re-exec and no dependency. It protects *children*, which is
the threat: the agent's shell arrives by `execve`. A forked-without-exec child
would still inherit it.

### A dead mount reads as an absent one

Kill the driver and the mount stays in `/proc/mounts` and answers `ENOTCONN`:

    ls, cat, os.listdir  ->  Transport endpoint is not connected (errno 107)
    os.path.isdir        ->  False
    os.path.exists       ->  False

The I/O calls fail loudly. The *predicates* do not: `os.path` swallows `OSError`
and answers no. That is silent-emptiness arriving through the idiom this
codebase reaches for everywhere -- `_require_layout` tests `is_dir()`,
`resolve_definitions` decides create-versus-must-exist on a directory existing,
`workspace/backing.py` tests `is_file()`. A health check written the obvious way
reports "not configured" for a mount that died thirty seconds ago.

And after the drop there is no cure: `fusermount -u` is refused, remounting is
refused. A mountpoint that dies is poisoned for the life of the process.

## Item 3, and why it is not here

The exploration opened wanting a third thing: session paths served from memory
as real paths, executable. It is dropped, because tmpfs already is that. Both
are RAM, both die with the container, both are real paths any program can open,
and `findings.md` already measured that a tmpfs counts 1:1 against the cgroup
limit and gives the memory back on delete. Files held in Python instead would
count against the same limit with object overhead a page does not have.

FUSE does not make memory faster, more private, or more ephemeral. If running
binaries out of a session is wanted, that is the `exec` flag and the barrier the
compose comment describes -- a one-line decision with its own trade-off, and not
this one.

## The design

**Existing drivers, as subprocesses.** `s3fs`, `gcsfuse`, `rclone`. Kingfisher
writing its own FUSE server over `BackendProtocol` was considered and refused
for the reason `skills/backend.py` already gives about that protocol: 18
members, beta, *"every one we implemented would be ours to keep in step with an
upstream that is still moving"*. A Python server under every syscall also brings
a hang mode nothing here has today, where a process both serving a mount and
reading it can deadlock.

**A deployment names a mount the way it names a store.** An environment variable
holding `module:name`, a zero-argument factory. The precedent is *Wiring a
store*, and its reasoning transfers without change: kingfisher does not know
whether a mount wants a bucket, a DSN or a pool, so it asks for none of them,
and *"inventing a URL grammar for stores it knows nothing about is the version
that ages worst"*. Environment variables rather than a workspace file, because
`confinement.writable_roots` returns the whole workspace and *"a file the agent
could edit is not a boundary"*. Naming one twice is refused at startup rather
than resolved by precedence. And it earns a runnable contract in
`kingfisher.testing` beside `SESSION_STORE_CONTRACT`, because *"a setting
inviting somebody to write an implementation without a way to check it is worse
than no setting"*.

**The sequence lives in `Kingfisher.__init__`, gated on the setting.** Call the
factories, probe every mount with something that *raises*, then drop the
bounding capability. Same home as `store_named` for the same reason -- *"a
setting resolved inside `Kingfisher.__init__` is inherited by every entry point
at once"* -- and gated so that a deployment which has not asked for mounts never
has a capability touched by importing a library.

The ordering is not a convenience. Mounts must exist before `resolve_definitions`
runs, because a mountpoint that has not come up is an empty directory, and a
supplied catalogue that is merely empty is the silent start that module already
refuses.

**`/skills` needs no code.** `KINGFISHER_SKILLS_DIR` already relocates the
catalogue; point it at a mounted path and `build_backend` roots the `/skills/`
route there, `shell_env` sets `KINGFISHER_SKILLS` to it, and
`confinement.readable_roots` grants it to the fenced shell. That is the whole of
the second job.

**Everything else arrives as a `/mounts/` family**, shaped like
`BUNDLED_SKILLS_ROUTE`: one entry in `layout.ROUTES` with `family=True`, members
generated per configuration. Explicitly not per-session -- after the drop there
is one mounting window, so a mount is process-wide and shared by every session,
and no per-caller route may sit on shared storage.

**Read-only, at both enforcement points.** A `deny_write_under` rule for the
file tools and a sandbox rule for the shell, the pattern `/harness` and
`/skills` already use because one is bypassable. The decisive argument is the
quota rather than tenancy: `session_bytes` counts a session directory, a mount
is outside it, and a writable mount is a run able to spend unbounded storage
that nothing can see. That is the shape *A session owns what it costs* had to
fix once already.

**The fenced shell reads mounts, and granted roots become a table.** Not a
hypothesis about some other host: the image now ships `--extra fence`, and
`compose.yaml`'s own comment points a multi-caller deployment at `auto`, so a
fenced shell in the container is a deployment kingfisher expects. A mount the
fence never grants is one the shell cannot read in exactly that deployment.

`readable_roots` special-cases `skills` as a named parameter today; a second
such path would make it two. `_fence_for` already warns what that costs -- a
path added to one fence branch and not the other fences the shell differently
depending on which mechanism the host happens to have, and no suite catches it,
because a run only ever exercises its own kernel's. The same move
`layout.ROUTES` made, for the same reason.

## When a mount dies

Nothing, at runtime. There is no cure available in-process, so there is no
machinery worth building for one: `ENOTCONN` reaches the model through
`WorkspaceToolErrors` as an ordinary tool error it can read and route around,
which is what that middleware exists for.

What is enforced is the start: every mount is probed with a call that raises,
and a mount that is not really there refuses the process. **A probe never uses
`exists()` or `is_dir()`** -- that rule is the whole of what the `ENOTCONN`
measurement bought, and it belongs in a comment where the probe is written, not
here.

## How it is tested

A `tests/linux/` shelf, against a real mount, with a guard modelled on
`test_a_fence_was_exercised` that turns the job red if the shelf skipped
everything. That file already exists because *"`tests/linux/` held real escape
tests that had never run anywhere"*, and this lands in exactly that position:
nothing here can run on macOS, so every guarantee is verified by one Linux job
or by nothing.

Two things the tests have to get right:

**The drop runs in a spawned child.** `PR_CAPBSET_DROP` cannot be undone, so a
test calling it directly poisons every test after it -- and poisons them
silently, because what breaks is a capability nothing else asserts on.

**The mutation test is the middle row of the capability table.** A test that
only asserts the mount serves would pass against a permitted-only drop, which is
the implementation that ships the hole. It has to spawn a child and confirm the
child can no longer remount `/workspace` executable.

## Open, and deliberately not decided yet

**macOS is absent entirely** -- no macFUSE, no `/dev/fuse`, no capabilities.
Whether the no-op path is silent, warned or refused is unresolved, and it is the
path every developer on this project will be on.

**Whether `doctor` reports mounts.** It already refuses unsafe tmpfs
arrangements rather than letting a deployment find out, and this has two things
worth the same treatment: a mount that is not there, and a container holding
`CAP_SYS_ADMIN` after startup.

**What the agent is told.** A route the model is never told about is dead
weight; `system.md` teaches virtual paths and would need to teach this one.

## What this does not do

It does not put `/data` or `/memory` on a mount, which is the fullest reading of
the first job. A mount is shared by every session and separated only by a path
prefix, so a per-caller route on shared storage needs a tenancy argument that
has not been made.

That argument is now *available*, which it was not a day ago: `compose.yaml`
sets `KINGFISHER_SHELL_SANDBOX` rather than the image, its comment records that
`external` says nothing about one session reading another's inside the same
container, and the image installs `--extra fence` so `auto` has `sandlock` to
reach for. A deployment on Landlock ABI 6 therefore has a fenced shell in the
container. Whether that is enough to put a per-caller route on shared storage is
the next thing to argue if `/mounts/` turns out to be too little -- and it is an
argument about `policy_for` granting a per-session subtree of a shared mount,
which is a different shape from anything the fence grants today.

It does not give kingfisher per-operation policy over the shell -- the in-turn
quota that *A session owns what it costs* records as impossible, and which a
kingfisher-owned FUSE server is the one route to. That was weighed and set
aside with the driver-subprocess choice; reopening it means reopening that.

## Slices, if this moves

Each independent, each green, one pull request off `main`.

1. The findings, into `docs/findings.md`, superseding the mirage entry's account
   of what FUSE costs. Depends on nothing and is worth landing even if the rest
   never does.
2. The named factory port and its contract in `kingfisher.testing`, with no
   mounting and no capabilities -- resolution, refusal on a bad name, refusal on
   a name given twice.
3. The mount-probe-drop sequence in `Kingfisher.__init__`, and the
   `tests/linux/` shelf with its guard and its mutation test.
4. The `/mounts/` route family, its deny rules, and granted roots moving into a
   table in `confinement`.
5. `doctor`, and what the agent is told.

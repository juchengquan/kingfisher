# A fence for the shell, and the doors it does not cover

**Status:** built. Steps 1-3 merged -- #261, #257, #264 -- and the fence is
verified against a kernel rather than reasoned about. What building it changed
is recorded at the end rather than edited into the decisions above. The
measurements are real and are marked; everything else is marked too.
**Date:** 2026-08-25

Kingfisher has no shell confinement on Linux. `confinement.py:315` checks for
macOS and `sandbox-exec`, and on anything else returns unconfined with a
warning — so `KINGFISHER_SHELL_SANDBOX=auto` and `off` do the same thing there.
`external` is the only honest setting, and it means *"the container is the
fence"*.

That was true while a container held one tenant. It stopped being true when it
held several.

## What is actually broken

Measured in the prototype container, not reasoned about:

```
tenant A writes /derived/secret.txt  →  "TENANT-A-PRIVATE"

through tenant B's FILE TOOLS:  refused
through tenant B's SHELL:       cat ../<A>/derived/secret.txt
                                → 'TENANT-A-PRIVATE\n', exit_code=0
```

The file tools are isolated — rooted at a session, `..` refused — and that half
of the claim holds. `execute` is not, and `confinement.py` says so in its own
opening lines: *"`virtual_mode` roots the file tools at a session. It does
nothing to `execute`."*

**And the shell is not the only door.** A registered tool, asked for a path:

```
line_count('/workspace/sessions/<other-tenant>/secret.txt')
  → 'secret.txt: 1 line(s)'
```

Tools run **inside kingfisher's process**, as root. A tenant cannot *add* a
tool — the deployment wires those — but the model chooses the argument, so an
agent that takes a prompt injection can ask a legitimate tool for an
illegitimate path. `line_count`, `csv_profile` and `mask_secrets` all take one,
and they are the ones this repository ships.

| door | isolated today | fixable by a subprocess fence |
|---|---|---|
| file tools | **yes** | n/a |
| `execute` | no | yes |
| registered tools | no | **no** — in-process |

## The mechanism, measured

`sandlock` **0.8.6** — Landlock, seccomp and seccomp user notification. A
separate package from `github.com/multikernel/sandlock`; mirage merely mentions
it. On Python 3.12 it has **no dependencies at all**.

Landlock is a filesystem access control, not a namespace, which is why it needs
none of what bubblewrap needed:

| | result |
|---|---|
| fences a subprocess under Docker's **default seccomp** | ✅ works |
| capabilities required | **none** |
| parent stays unfenced | ✅ kingfisher keeps working |
| survives `exec`, reaches grandchildren | ✅ `sh → sh → cat` denied |
| cost | 0.2ms → **0.8ms** per command |

Every escape attempted failed, and the control confirms the fence is what
stopped them — each of these **succeeds unfenced in the same container**:

```
read another session directly     denied
relative path after cd            denied
symlink pointing into the target  denied
/proc/self/root/...               denied
re-confine wider from inside      denied   (rulesets only ever narrow)
bind-mount it into an allowed path  fails  ← with SYS_ADMIN available
mount a tmpfs over its own path     fails  ← with SYS_ADMIN available
unshare -m a new mount namespace    fails  ← with SYS_ADMIN available
```

Those last three matter more than they look. Landlock denies the path
resolution that `mount(2)` needs, so **a fenced process cannot spend
`SYS_ADMIN` even when the container has it** — which is what makes the FUSE
question (below) survivable at all.

The alternatives were measured and are worse. **bubblewrap** works only with
`--security-opt seccomp=unconfined`: you would disable the syscall filter to
gain path isolation, in a box shared by tenants. **Per-session Unix users** via
`setpriv` works — B denied, cannot climb back to root — but it is *deny by
ownership*: every file, forever, including ones the agent creates. Landlock is
*deny by default*, which is the direction a security mechanism should fail in.

## Decisions

| # | Decision | Why |
|---|---|---|
| S1 | **Landlock, through `sandlock`, is the Linux implementation of `Confinement`.** | It is the only one of three measured options that needs no privileges, no relaxed seccomp and no ownership bookkeeping, and the only one that is deny-by-default. `0.8.6` with zero dependencies at one seam is a very different proposition from the `0.0.5` this repository already declined. |
| S2 | **`Confinement` changes from "wrap a command string" to "how to run a command".** | `wrap(command) -> str` fits `sandbox-exec`, which is a command prefix. `confine()` is a callable applied between `fork` and `exec` — it cannot be spelled as a prefix. The abstraction has to carry a `preexec` as well as a `wrap`, and the macOS path keeps using the prefix. This is the one change that touches existing working code. |
| S3 | **The policy is generated from the session, never written by a deployment.** | The first policy this author wrote **failed open**: it granted `/tmp` writable, mirage mounted itself under `/tmp`, and the fence covered nothing — while every read succeeded, so it looked like it worked. `Sandbox` has forty fields. A hand-written policy that is wrong is indistinguishable from one that is right until someone reads another tenant's file. |
| S4 | **Filesystem only in the first version. Network is a separate decision.** | `confine()` accepts filesystem paths and **rejects** network, resource, seccomp and environment settings — *"rejected rather than silently ignored"*, which is the right behaviour and is how this was found. Network filtering needs sandlock's supervised mode, which takes over launching the process; that is a second change to S2, not a bigger policy. |
| S5 | **Registered tools are fenced by checking their arguments, not by Landlock.** | They run in kingfisher's process; a fence there would confine kingfisher. The shape already exists — `reject_host_path` refuses a host path handed to a file tool — and becomes: an absolute path in any tool argument must be inside this session or in a named allowlist. It is a weaker mechanism than the kernel, and it covers the door that the kernel cannot. |
| S6 | **A Landlock ABI below what sandlock wants is reported, not silently degraded.** | sandlock's full ruleset wants ABI 6 (Linux 6.12+). `allow_degraded` is accepted and gives *"weaker protection"*, which neither its documentation nor this testing pins down. A fence that quietly becomes weaker on a different node is worse than one that says so: `kingfisher doctor` reports the ABI and refuses a degraded fence unless a deployment asks for it by name. |
| S7 | **The fence is defence in depth. It is not the tenancy boundary.** | It closes `execute` and leaves the network open, and S5's argument check is not a kernel. On EKS a **pod per tenant** is the boundary — kernel-enforced, `SYS_ADMIN` scoped to one tenant, mounts naturally private — and it is cheap there in a way "a container per tenant" was not on one Docker host. This plan makes a shared pod *safer*; it does not make it *safe*. |

## What this does not cover

**The network is wide open.** A fenced shell that cannot read tenant A's file
can still open any socket — measured, both loopback and outbound. This matters
because `confinement.py` already names the risk: *"`http_fetch` is a registered
tool, so reading and sending are one turn apart for anything that gets an
injection into a document."* Closing the filesystem door while the network door
stands open is a real improvement and is not the job finished.

**Registered tools are covered by a check, not by the kernel.** S5 catches a
path argument. It does not catch a tool that builds a path from two arguments,
reads an environment variable, or opens a socket. In a `SYS_ADMIN` container an
unfenced in-process tool is not only a data leak but an escape surface.

**Nothing here fences the interpreter or the model's own reach.** Out of scope
and worth naming so nobody assumes otherwise.

## FUSE, and why it is in this document

The container was chosen *"for not ruining the macOS"* — that is, to have Linux
FUSE rather than macFUSE, which on Apple Silicon needs a kernel extension and
reduced security mode. Measured: the container's kernel is Linux 6.12 in Docker
Desktop's VM; Darwin is never involved.

And FUSE does deliver the thing nothing else did. mirage's in-memory workspace,
mounted:

```
written into the RAM workspace, via mirage : 'IN MEMORY ONLY'
mounted at                                  : /mnt/ws
plain open() through the mountpoint         : 'IN MEMORY ONLY'
third-party package (yaml) reads it         : 'IN MEMORY ONLY'   ← breaks the bind
fenced, its own /data                       : exit=0
fenced, another tenant                      : Permission denied
```

Every earlier measurement said a script could import a package *or* see the
mounts. Through FUSE it does both, and Landlock fences the result per path.

**The price is `SYS_ADMIN`, and there is no way round it.** Measured: relaxing
seccomp does not help; only the capability does. Two consequences:

- It is why S1 matters more than it looks. A fenced process cannot use
  `SYS_ADMIN` — measured — so the capability's blast radius is whatever is
  *un*fenced. Which is the tools door, again.
- **`mirage`'s sandlock runtime does not exist in `0.0.5`.** `runtimes=['sandlock']`
  raises `unknown runtime`, with `sandbox`, `fuse` and `monty` extras installed
  and the package present. Its own docs also say sandlock *"does not see
  Mirage's virtual workspace mounts"* without FUSE — so even shipped, it is the
  same bind rather than an escape from it. Anything built here uses sandlock
  **directly**.

## On EKS

`--device` has no Kubernetes equivalent. Three routes: `privileged: true`
(worse than today in a shared pod), a **FUSE device plugin** advertising
`/dev/fuse` as a schedulable resource (the right one), or a `hostPath` — which
mounts the file and still fails, the same way `mknod` did here: the file
appears, the device cgroup keeps the door shut.

Two things to check on the cluster rather than assume:

- **The node kernel decides S6.** `kubectl get nodes -o wide`. Docker Desktop
  is on 6.12, which is exactly what sandlock wants; EKS nodes are commonly on
  6.1, which is not.
- **Pod Security Admission will refuse this.** `restricted` forbids added
  capabilities; `baseline` forbids `SYS_ADMIN`. The namespace holding every
  tenant would need the `privileged` policy.

Swap is the one thing that gets easier: Kubernetes nodes have it disabled, so
the silent paging measured earlier does not arise. The `doctor` check stays, as
what tells you the assumption still holds.

## Still to settle

| # | Question | How |
|---|---|---|
| F1 | What exactly `allow_degraded` gives up on a 6.1 kernel | Read sandlock's source; there is no other statement of it |
| F2 | Whether `sandlock` is maintained | It is a security fence. `0.8.6` with no dependencies says what shape it is, not whether anyone is looking after it |
| F3 | Whether S5's argument check can be written without a list of which parameters are paths | `#245` made tools *state* that their path is a host path, in prose. Nothing is machine-readable |
| F4 | What the supervised mode costs, if network filtering is wanted | It launches the process; S2 would change again |
| F5 | Whether tenants may ever supply their own tools | If yes, S5 is not enough and nothing short of a process boundary is |

## The order to build in

1. **`kingfisher doctor` reports the truth about confinement.** It currently
   says nothing useful on Linux. One check: what is fencing `execute`, and on
   what kernel. This is small, ships alone, and tells every existing deployment
   where it stands.
2. **S2 — `Confinement` learns to carry a `preexec`.** No behaviour change;
   macOS keeps its prefix. This is the seam everything else needs.
3. **S1 + S3 — the Linux fence, with a generated policy.** Session directory
   writable, the shared catalogue readable, nothing else. Held to the escape
   list above as tests.
4. **S5 — the tool argument check.** Separate, because it is a different
   mechanism covering a different door, and because F3 is unresolved.
5. **Network, or not.** S4 says decide it separately, and F4 prices it.

## Recommendation

**Build 1–3. Treat 4 as required before this is called tenant isolation, and
5 as a separate decision.**

And say plainly what it buys, because the honest version is narrower than "a
proper lock": it closes the door that is currently open and demonstrably
leaking, on the platform where kingfisher has no fence at all. It does not make
a shared pod safe for mutually distrusting tenants — S7 — and on EKS the
boundary that does is a pod per tenant, which that platform makes cheap.

If tenants are mutually distrusting, do S7 *first* and treat 1–5 as depth. If
they are one organisation's, 1–3 is the fix and the rest is judgement.


## What building it changed

The escape list held. In a container on Linux 6.12, ABI 6, with `SYS_ADMIN`
available, every case in the table above is denied, and the four that need no
capability are confirmed to *succeed unfenced* -- so a denial can be told from a
typo. What follows is what the container knew that reading did not.

**`Sandbox.run` does not work under Docker's default seccomp.** `sandlock`'s own
quick-start example returns `sandlock_create failed`; it needs more than
Landlock. `confine` in the same container works both directions. So the fence
goes on between fork and exec and kingfisher owns the process launch -- which
answers O5 of `2026-08-26-a-folder-handed-in-a-command-handed-off.md` by
measurement rather than by preference, and is why `preexec_fn`'s hazard in a
threaded program is named in the class instead of discovered later.

**A policy naming a path that does not exist fences nothing.** `/lib64` is
absent on arm64 Debian, and naming it made `sandlock_create` fail outright: the
fence never built, every command returned an empty result, and **every escape
test passed** -- because a command that cannot run reads another tenant's file no
better than a fenced one does. The test that caught it was the one asserting the
session stays *usable*. A security suite without that test is a suite that
passes hardest when it is most broken.

**A fence that fails to apply is nearly silent.** `subprocess` discards the
child's exception and reports "Exception occurred in preexec_fn." with no
detail. It fails closed -- the child is dead, so there is no unfenced run -- but
the message now says which side failed rather than inventing a reason it was not
given.

**S3 was right for a reason its author supplied twice.** The hand-written policy
that failed open is in the decision. The generated one then failed *closed* on a
platform nobody had run it on. Both are the same mistake -- a path list is a
claim about every machine this will ever run on -- and only one of them was
visible without a container.

**S2 is superseded and S1 is not.** `Confinement` never learned a `preexec`:
`LocalShellBackend.execute` is 110 lines around a `subprocess.run` with no hook.
The seam moved up to `CommandRunner` (#257), and a deployment can now supply one
of its own (#263) -- so "run this tenant's commands in this tenant's pod", which
S7 names as the boundary that actually works, is one implementation of one
method rather than a rewrite.

**One gap this work added.** `kingfisher doctor` takes only a `Config`, so it
cannot see an injected runner and reports the built-in path only. Same shape as
the problem `elsewhere` solved for containers, and unsolved here.

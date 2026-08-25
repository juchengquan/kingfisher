# A folder handed in, a command handed off

**Status:** built. All four slices merged -- #256, #258, #257, #259 -- and what
building them changed is recorded at the end rather than edited into the
decisions above. Measurements are marked as such; everything else is a decision
or an open question.
**Date:** 2026-08-26

Two pieces of work are queued against the same code and neither can start
cleanly: the Landlock fence, which changes how a command runs, and an
out-of-tree memory-backed filesystem, which changes where a session's files
live. Both currently mean editing `build_backend`, because that one function
decides both.

This splits it into two seams. They are not symmetric, and most of the design
here is about why.

## What a deployment can replace today

`Kingfisher.__init__` already takes `dirs`, `threads`, `definitions`, `files`,
`sessions`, `catalogue`, `grants` and `middleware`. It does **not** take a
backend. `build_agent` does, but that is the harness.

So a deployment wanting a different filesystem has exactly one route -- inject a
whole `graph` -- and that route costs everything:

```
with pytest.raises(ValueError, match="pre-built graph"):
    run(Request(task="go", capabilities=Capabilities(builtin_tools=("read_file",))), ...)
```

*"It was built elsewhere, so the restrictions were never applied to it.
Refusing beats running with more access than the caller asked for."*

Custom filesystem **or** per-request capabilities. Never both. The backend is
the one collaborator that was never made a port.

## The two seams are not alike

Measured, not assumed.

**The filesystem seam touches no framework at all.** `FilesystemBackend` does
`Path(root_dir).resolve()` once at construction, then per access
`(cwd / requested).resolve()` followed by `.relative_to(cwd)`. That is the
entire relationship: open files, check containment. It cannot distinguish a
plain directory from a tmpfs, a FUSE mount or a mounted volume, because all
three resolve to a real path and support ordinary file operations.

**The execution seam does.** `LocalShellBackend` **is** a `FilesystemBackend`:

```
MRO:            LocalShellBackend -> FilesystemBackend -> SandboxBackendProtocol -> ...
defines itself: execute, id
inherits:       agrep delete download_files edit glob grep ls read upload_files write
```

That object sits in the composite's **default** slot, which serves execution
*and* every path not matching a route -- `/derived`, `/runs`, the transcript.
"Supply the shell" therefore means "supply the unrouted filesystem too".

One seam is a folder. The other is a method on an object the framework owns.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **Two seams, injected separately: a folder for files, a runner for commands.** | They vary independently. tmpfs + unfenced shell, tmpfs + Landlock, an out-of-tree memory filesystem + Landlock, a store with no shell at all -- every combination is one somebody has asked for. A single injection point makes each choice restate the other. |
| D2 | **The file seam hands back a directory. Kingfisher keeps the routes.** | Every route is `FilesystemBackend(root_dir=str(...))` and the shell is rooted at their *parent*, so `/data/x.csv` through a tool and `data/x.csv` through `cat` are one tree seen twice. An object-store backend behind a route would leave `execute` reading an empty directory, and nothing in the code would notice. |
| D3 | **No framework type appears in the file seam.** | Measured above: deepagents resolves and checks containment, nothing more. A provider returns a `Path` and never imports deepagents. The one rule this imposes: **a symlink pointing out of the tree is refused**, so a provider cannot compose a session out of links to shared content. |
| D4 | **A provided tree lives for one turn, as a bracket: enter, restore, run, keep, exit.** | A backend is built per request -- *"Built per request because capabilities narrow it, because it reads workspace content that can change between turns"* -- and `open_session_for` is called per request too. A mount held *between* turns assumes the process that made it is still there next turn, which is the assumption the session store exists to remove. A bracket is also the only form that guarantees an unmount after a failed turn; a leaked mount per turn in a shared container is the problem the fence plan is trying to close, arriving through the door we just built. |
| D5 | **Injection is on `Kingfisher.__init__`, beside `sessions`. Not on `build_agent`.** | The bracket has to be wider than the backend builder: `restore_into` writes into the tree before the turn and `keep_from` reads it after. `build_backend` receives a path it does not have to reason about. |
| D6 | **Only *running a command* is delegated. Kingfisher keeps the shell object and its ten file operations.** | Handing over the default slot hands over the unrouted file operations, which D2 and D3 just placed with kingfisher. `ConfinedShell` keeps subclassing `LocalShellBackend` and delegates `execute` to an injected runner. |
| D7 | **The runner's contract is kingfisher's own: command and timeout in, output and exit code out.** | Using the framework's response type would leak the framework into every implementation -- exactly what D6 exists to prevent. `ConfinedShell` converts. This also makes "run the command in the tenant's own pod" a one-method implementation rather than a thirteen-method one. |
| D8 | **No config name for choosing a file provider.** | With a memory-backed filesystem deliberately out of the shipped set, the list has one entry. `ADAPTERS` is this codebase's precedent for a curated list -- *"Adding a row is a kingfisher release"* -- and the alternative, an import string from an environment variable, is arbitrary code execution configured by whoever controls the environment. A strange thing to add in the same quarter as a Landlock fence. |
| D9 | **No provider promise that the shell can see the tree.** | Proposed and withdrawn: no test can fail it. A FUSE mount is shell-visible and so is a directory, so the check could only be exercised by inventing a broken provider. The honest form is `kingfisher doctor` writing a file through the file tools and reading it back through the shell -- a claim about the running system, not a promise a plug-in makes about itself. Separate work. |
| D10 | **A turn ends in one teardown block that persists and then releases the tree.** | `stream()` is a generator ending in `yield self._finished(...)`, and `_finished` is where files reach the store -- so a caller who stops reading early persists *nothing*, and today loses that turn when the session moves machines. Placing the save "after the graph loop, before the final yield" does not fix it: a generator only advances when someone pulls, so both happen in the same `next()`. Persist and release therefore share one block that runs on exhaustion, on exception, and on collection of an abandoned generator. Two consequences: **a failed turn now persists partial work** (a behaviour change, and the right one if the store is the truth), and **persistence must be idempotent**, because the final event carries the artifact list and so the happy path saves while building it. |
| D11 | **The teardown needs no new injection points.** | It calls exactly the two seams this document already defines -- the store, which is already a port, and the tree provider. The turn gains a defined end; the sockets hang off it rather than multiplying. |
| D12 | **A provider hands back an empty directory. Kingfisher creates the layout inside it.** | The alternative makes an out-of-tree provider import kingfisher's session layout and track its changes -- a breaking change in the one place furthest from this repository and slowest to notice. "A writable directory" is a contract that does not move. |
| D13 | **Both lists of session folder names live in `domain/layout.py`; one function creates them.** | The split already exists and is already right: *"Every name and tier here is policy... None of it creates anything -- making the layout real is `infrastructure.workspace_fs`."* What is missing is that only half the names are written down. `SESSION_DIRS` is `data, derived, memory, runs`; `build_backend` separately makes `data` and `memory` **again**, plus `.home` and `skills/uploaded`, which are in no list. Nothing today creates all six in one pass. |
| D14 | **The plumbing names get their own list rather than joining `SESSION_DIRS`.** | That tuple means *"the names the agent addresses"* -- `backend.py` says so where it excludes `.home`: *"this is plumbing"* -- and `test_workspace.py` asserts on that meaning. Widening it to "everything that must exist" would make one name mean two things. Two adjacent lists in one file keep the distinction without keeping the distance. |

## What this does not change

`build_agent(backend=...)` stays as it is. It is the low-level escape hatch and
`_backend_for` already documents its terms: *"A ready-made backend is taken as
it is, catalogue included."* Nothing here makes it worse, and O3 asks whether it
should survive longer term.

The two directories `build_backend` makes that are **not** per-session -- the
workspace scratch and the shared skills folder -- stay where they are, made
where they are made now. Only what lives *inside* a session moves, and after
D13 that is six names in one place rather than four in one and two in another.

`KINGFISHER_SHELL_SANDBOX` keeps its three values. `MODES = (AUTO, EXTERNAL,
OFF)` and the dispatch that reads it are the selector the execution seam
already has; Landlock is a branch inside `AUTO` on Linux, not a new mechanism.

## Still to settle

| # | Question | How |
|---|---|---|
| O1 | Whether `sessions()`, `reap` and `session_bytes` should ask the store rather than the workspace | They read `sessions_root(workspace)`, so a provider putting trees elsewhere gets an inventory reporting nothing and a janitor with nothing to sweep. Harmless for a tree not meant to outlive the turn, wrong for one that is -- and in both cases the store is what a caller should be asking |
| O2 | Whether a caller holding an unexhausted generator open indefinitely is worth defending against | D10 releases the tree when the generator is collected, which is the collector's timing, not ours. A `stream()` that must be closed explicitly would be exact and would change every caller |
| O3 | What a mount and unmount per turn costs | Unmeasured. Only bites an out-of-tree FUSE provider; the earlier measurement mounted once and stayed |
| O4 | Whether `build_agent(backend=...)` should survive once both seams exist | It is the remaining way to bypass every decision here |
| O5 | Whether the default runner uses a command prefix or launches its own process | `LocalShellBackend.execute` is 110 lines of truncation, timeout and exit-code shaping around a `subprocess.run(..., shell=True)` with no hook. A prefix keeps that; launching costs 110 copied lines and their drift. Now a question inside kingfisher rather than part of a contract |

## The order to build in

Three slices, each green on its own, each a PR off `main`.

1. **One place makes a session's folders.** The two plumbing names join
   `domain/layout.py` as a second list beside `SESSION_DIRS`; the function that
   already creates a session's layout creates both; `build_backend` creates
   nothing. Useful alone, and it removes a duplicate creation rather than adding
   an abstraction. **One behaviour changes and is deliberate:** that function
   also scaffolds `memory/AGENTS.md` when absent or empty, and it runs per turn
   once the tree is ephemeral rather than once per session -- so an agent that
   empties that file gets the scaffold back next turn. Today the layout is made
   at session creation and `build_backend` re-makes two of the folders every
   turn as insurance; an ephemeral tree has no insurance to inherit, so creation
   moves into the turn.
2. **The turn gets an end.** D10: one teardown that persists and then releases,
   with persistence made idempotent. Proved by a test that abandons a stream
   after the first event and finds the work in the store. This is worth landing
   even if nothing else here is, because the loss it prevents exists today.
3. **The file seam.** The port, the local implementation, the tree bracket
   hanging off D10's teardown, and injection on `Kingfisher.__init__`. The
   layout function from slice 1 runs inside the bracket, immediately after the
   provider hands the directory over. Proved by a test provider that records its
   enter and exit and fails a turn between them.
4. **The runner seam.** Split `execute` out of `ConfinedShell` behind
   kingfisher's own result type, with the default runner preserving today's
   behaviour exactly. Proved by a recording runner.

**This replaces step 2 of the fence plan.** That plan said *"`Confinement`
learns to carry a `preexec`"*, which cannot be done through `LocalShellBackend`
without copying its `execute`. The runner seam is that step, done differently,
and the Landlock fence becomes one runner behind it rather than a change to the
`Confinement` type. S2 in `2026-08-25-a-fence-for-the-shell.md` should be read
against this file.

## Recommendation

Build 1 through 4 in order. Slices 1 and 2 each stand alone.

Land PR #254 first -- it is green, and it moves the persistence work these
seams are shaped around.

Slice 2 is the one to land regardless of what happens to the rest, and it is
not a refactor. Abandon a stream today and the turn is never written to the
store, so a session that moves to another machine loses it silently. That bug
exists now; the ephemeral tree would only make it louder.

The other three buy no speed and no safety on their own. What they buy is that
two queued pieces of work -- a fence and a filesystem -- stop being edits to the
same function, so they can be built, tested and reverted independently by people
who are not both in this repository.

## What building it changed

Four slices, four PRs, nothing above rewritten. Where the plan was wrong it was
wrong in a way worth keeping visible.

**Slice 2 was a live bug, and worse than described.** The plan said a caller who
stops reading never persists. It also never *ends the turn*: `yield from
prepared.events` sat outside the `try`, and `run_start` is the first of those --
so stopping at the first event left the claim taken, the checkpointer open and
the interpreter running. Not an exotic path, the common one.

**`_record` promised something it did not do.** *"A turn that died before the
first superstep has nothing to add"* was prose; the code read `.values` off
whatever `get_state` returned. Unreachable while persistence only ran after a
completed turn, reachable the moment it ran on every turn.

**The async path needed a second spelling, not the same one.** `stack.push`
after entering, rather than `enter_context`: `ty` cannot resolve the type
variable through `asyncio.to_thread`, and `push` is better anyway, because it
leaves the turn's exception reaching a provider's `__exit__` where a callback
would have swallowed which way the turn ended.

**One test passes for a reason that is not the code.** With nothing releasing
the tree at all, an ordinary async turn still releases it -- the suspended
generator is collected as soon as the last reference goes, and its `finally`
runs then. Only a turn that *raises* pins the release, because its frames stay
alive in the traceback. Both tests are kept and the weaker one says so.

**S3's warning applies to test doubles too.** Every guard here was checked by
breaking it: disabling the layout refusal, moving persistence back to the end,
putting the pre-run events back outside the `try`, narrowing the tree bracket,
removing the async release. A guard whose test still passes when the guard is
gone is the same failure as a policy that grants `/tmp`.

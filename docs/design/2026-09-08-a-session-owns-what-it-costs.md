# A session owns what it costs

**Status:** proposed. Nothing here is built. **One part of it is not a proposal
at all**: the shell sandbox can be escaped on the default macOS configuration,
which was found while deciding where the profile may live, and that fix is the
first slice -- see *The hole this opened* below.
**Date:** 2026-09-08. **Written against `5cc6cd3`**, and every claim in it was
checked there rather than carried over: the first draft was written against
`682f101`, before `f99d2ce` cut most of the repository's comments, and four of
its citations quoted prose that no longer exists.
**Cited by symbol, not by line.**

Per-session state lives in two places, and only one of them is swept. A session
directory is deleted with its session and counted by `session_bytes`; everything
under `state_dir` is deleted by nothing and counted by nothing. Three things sit
there: the pinned agent (`agent_snapshot`), the run log (`log_path`), and the
scratch directory `TMPDIR` points at.

`delete_session` removes the directory, the thread, the claim and the store's
copy. `reap` does the same for expired sessions and additionally clears claims
whose session has gone. Neither touches the snapshot or the log. So a workspace
accumulates one `agents/<id>.yaml` and one `runs/<id>.jsonl` per session that
ever existed, permanently, and `.kingfisher/tmp/` accumulates whatever the shell
put there, shared by every session at once.

**The rule this proposes is already written down, twice, about single files.**
`TRANSCRIPT`'s comment says the conversation sits at the session root "so it is
deleted with the session, counted by `session_bytes`, and carried by whatever
keeps the rest". *Sessions: what persists and where* in `decisions.md` says the
per-session sqlite database bought "a conversation deleted with its directory
(one workspace held 132 orphaned threads after every session had been reaped)"
and a conversation the quota can see, and that both had to survive its removal.
Two records state the rule about the one thing each of them owns. Nothing states
it about the layout, which is why three files sit outside it.

## The layout this proposes

    <workspace>/
      agents/ skills/ subagents/ tools/     authored, agent-unwritable
      .kingfisher/                          harness-owned, agent-unwritable
        WORKSPACE                             the marker, carrying a layout version
        shell.sb                              the sandbox profile
      sessions/<id>/
        data/ derived/ memory/ runs/          the names the agent addresses
        .home/  skills/uploaded/              plumbing
        .tmp/                                 TMPDIR: per session, writable
        .harness/                             harness-owned, agent-unreachable
          agent.yaml                            the agent this session opened with
          transcript.jsonl                      the conversation
          claim/                                the turn lock
          runlog.jsonl                          the run log

## Decisions

**Two properties, and portability is deliberately not one of them.** Nothing a
session leaves behind survives its deletion, and everything a session costs is
countable. The first is satisfiable two ways -- move the file in, or delete it
on disposal, which is what `_discard_dead_claims` already does for claims -- and
the second only by moving, because `session_bytes` is `rglob` over one directory
and has no other definition. Portability is a third thing that would have argued
for the same moves, and it is *not* the reason: `SessionStore` is the mechanism
for a session that outlives its machine, and naming portability here would make
this design responsible for something that port already owns.

**Uniform rather than by size.** A rule that moved the large things and left the
small ones (the pin is about a kilobyte, a claim is an empty directory) buys the
same two properties for a smaller diff. It is rejected because the exception is
where the reasoning goes to hide: the pin is small, and it is also the file that
decides which endpoint a session's prompts reach and whose credentials pay for
them. A layout whose rule is "unless it is small" cannot be checked by reading
the layout.

**The session gets a directory the agent cannot reach, and it needs two
enforcement points because one of them is bypassable.** `.harness/` is denied
`read` and `write` by a `Route` in `layout.py` -- `FilesystemPermission` takes
both operations, and `_all_paths_scoped_to_routes` is why the path has to be a
route to carry a rule at all -- and denied writes again by the sandbox, because
the shell bypasses tool permissions entirely. That is the pairing `/skills/`
already has: `denied_scopes` binds the file tools and `protected_roots` binds the
shell, and both are needed because a file only the first refuses is one the shell
can still read and write.

On macOS one static profile covers every session, with a regex rather than a
subpath:

    (deny file-write* (regex #"^<workspace>/sessions/[^/]+/\.harness(/|$)"))

Verified on a host: a write to `derived/` succeeds and a write to `.harness/` is
refused. A per-session profile was the alternative and is worse -- `shell.sb` has
one fixed path, so two concurrent turns would race to write different bytes to
it. On Linux `argv_for` is already built per session, so it is one `--ro-bind`
after the session's `--bind`, with the caveat that `_present` drops binds whose
source is absent: the directory has to be created by `ensure_session_layout`
before the argv is built.

**Denying reads costs nothing at the prompt.** A deny rule does not refuse a
bulk call; `_filter_paths_by_permission` and its `ls`/`grep` siblings remove
denied entries from the *results*. So `.harness/` is invisible rather than
present-and-refused, and the model never sees a directory it will then try to
open. The over-firing warning in that module is about `interrupt` mode.

**`TMPDIR` becomes `sessions/<id>/.tmp`, and `KINGFISHER_SCRATCH_DIR` goes.**
Shared scratch is the worst offender on both properties at once -- nothing sweeps
it, nothing counts it -- and it is also a cross-session channel: `writable_roots`
names it for `sandbox-exec` and `argv_for` binds it writable for bwrap, so one
caller's derived data sits where another caller's agent can read it.
`prepare_scratch` creates the directory `0o700` and re-checks its owner, and that
argument is about other Unix users on the host; it says nothing about other
sessions, because inside one workspace there was nothing to say.

Per-session and relocatable are mutually exclusive, which settles the variable
rather than taste deciding it. Scratch is reachable by the shell only, which is
what makes moving it out of the workspace safe today: file tools resolve through
routes and `execute` does not, so a root the tools cannot address cannot
disagree with itself. The moment scratch is inside the session it is reachable
both ways -- the shell at `.tmp/`, the file tools at `/.tmp/` through the default
backend -- and it has to stay under the workspace for the two views to agree.

Not protected, unlike the rest of `.harness/`: the shell must write there.

**What the store carries is decided per member, not by prefix.**

| Member | In the session | Protected | Carried |
|---|---|---|---|
| `agent.yaml` | yes | yes | **yes** |
| `transcript.jsonl` | yes | yes | yes, as now |
| `claim/` | yes | yes | **no** |
| `runlog.jsonl` | yes | yes | **no** |

A claim restored onto a fresh host makes the session look busy, and
`claim_stale_after` is `turn_timeout_s` plus the longest model timeout, so the
session is refused for minutes before the slot may be taken. Today a wholesale
`.harness/**` would happen to be harmless, because a claim is an empty directory
(`create_exclusive` is `mkdir`) and both `fetch` and `keep_from` handle files
only. That is an accident: the day someone writes a holder id into the claim,
wholesale carrying silently starts wedging resumed sessions.

The run log is a weaker no and still a no. `save` writes whole bytes and
`keep_from` reads whole files, so carrying a monotonically growing JSONL
re-uploads the entire log every turn -- for a file nothing in production reads.
`read_usage` has two callers, `tests/unit/test_runlog.py` and
`tests/integration/driver.py`, and both are tests.

**Carrying the pin closes a hole that exists now.** `keep_from` is called with
`(*kept, TRANSCRIPT)` and the pin lives in `state_dir`, which the store never
sees. So on a deployment where a session moves between machines -- the store's
entire purpose -- the new host calls `agent_started_with`, gets `None`, and
`_agent_for` re-pins from *its* catalogue while accepting whatever agent the
request named. `_agent_for` says it returns "the agent this turn runs, which is
the one its session opened with"; that is true on one host and quietly false
across two. Moving the pin into the session is what makes it carryable; carrying
it is what makes the sentence true.

**`KINGFISHER_STATE_DIR` goes with it.** After the moves, `state_dir` holds one
file, `shell.sb`, and a variable that relocates one generated file is a knob
with nothing behind it. The profile stays at `<workspace>/.kingfisher/shell.sb`
and `.kingfisher/` joins `protected_roots`, which gives that directory a single
meaning -- harness-owned, workspace-scoped, agent-unwritable -- rather than
today's "state, some of which happens to be per-session". This is safe only
after scratch has moved: `protected` denies are emitted after the `allow` lines
and win, so protecting `.kingfisher/` while `tmp/` still lived under it would
have killed `TMPDIR`.

Reads stay allowed. The boundary is the kernel enforcing the rules, not the
agent's ignorance of them, and carving a read hole inside an otherwise readable
workspace is mechanism for no gain.

**No compatibility path. The marker carries a layout version instead.**
`.kingfisher/WORKSPACE` contains `"kingfisher workspace\n"` and nothing has ever
read its contents -- `is_new_workspace` checks only that the file exists -- so
the marker is where the version goes. A workspace whose marker names an older
layout is refused with a message.

Fallback readers were the alternative and are worse here, because the failure
modes are not "it breaks". A missing pin means the session silently changes
agent; a transcript read from the wrong path means the conversation comes back
empty. A fallback papers over both, and it has no forcing function to be
removed: the day the old paths are gone, nothing says so, and two readers stay
in the tree describing a world that ended. A version check gets cheaper instead
-- the next layout change inherits it.

What breaks is every existing workspace, until it is recreated or its `sessions/`
and `.kingfisher/{agents,runs,claims,tmp}` are deleted by hand. Sessions are
described throughout this codebase as disposable, and the durable content is the
definitions that `KINGFISHER_SKILLS_DIR` exists to keep elsewhere -- which
`layout.py` already names as the thing worth versioning, "rather than around
200MB of sessions". The part being broken is not the part anyone would mourn.

**The name is `.harness/`, and `runlog.jsonl` is not `runs.jsonl`.** `.state/`
was the first choice and is retired vocabulary the same week: `state_dir` and
`KINGFISHER_STATE_DIR` are being deleted here, so the word would stop meaning the
workspace thing and start meaning the session thing, and git history would read
backwards. `.kingfisher/` at both scopes gives one rule for both and makes every
path string in the code scope-ambiguous, in functions where both are in scope.
`.harness/` is what `LAYOUT_DIRS` already calls the neighbour it names -- "the
harness's own directory" -- and what the package layer is called. And `/runs`
already means per-turn scratch the agent addresses; two things called "runs" in
one session directory is how the last pair of names drifted apart.

**This deletes more than it adds.** `_discard_dead_claims` exists to sweep claims
whose session is gone, and an orphaned claim is impossible once the claim lives
inside the thing it guards. `retention.orphaned` stays: reconciling threads
against live sessions is its other caller, and that one is unaffected.
`writable_roots` loses its scratch parameter and `argv_for` loses a bind. Against
that, the reaper's `still_held` stops being one `listdir` of `state_dir/claims`
and becomes one stat per session directory.

## The hole this opened

Deciding where `shell.sb` may live turned up the fact that it may not live where
it does. **The macOS shell sandbox can be escaped in two commands on the default
configuration.** `state_dir` defaults to `workspace/.kingfisher`,
`writable_roots` returns the whole workspace, and `protected_roots` names only
the definition roots -- so the profile file sits inside the region its own rules
declare writable. `_sandbox_exec` re-reads it for every command while `resolve`
rewrites it only per `build_backend`, so the agent's version stands for the rest
of the turn.

Reproduced through `confinement.profile` with `readable_roots`, `writable_roots`
and `protected_roots`, at the path `resolve` writes:

    read home  : rc=1     # denied, as designed
    write skill: rc=1     # denied, as designed
    write prof : rc=0     # the shell overwrites .kingfisher/shell.sb
    read home  : rc=0     # the operator's home
    write skill: rc=0     # and skills/PWNED.md, which is there afterwards

This is the premise *Confining the shell* already argues for `tools/` and
`agents/`, one level further in: the file that defines the boundary is one the
agent can edit. *Wiring a store* states it as a rule and attributes it to
`confinement.resolve` -- *"host-side configuration, and a file the agent could
edit is not a boundary"*. **That attribution is now stale**: `f99d2ce` trimmed
the sentence out of `resolve`, so `decisions.md` cites a docstring that no longer
says it, and the state directory it was arguing for *is* the workspace by
default. The rule survived in prose and was never true in the code.

macOS `auto` only: bwrap reads no profile, and `external` and `off` have nothing
to defeat. It closes today if `KINGFISHER_STATE_DIR` points outside the
workspace, which is the variable this document removes.

The fix is one line, and by `path` rather than `subpath` so `TMPDIR` under
`.kingfisher/` keeps working until slice 2 moves it:

    (deny file-write* (path "<profile>"))

Checked against every route rather than the obvious one: overwrite, append,
unlink and rename-over are all refused, and a write to `.kingfisher/tmp/` still
succeeds. The profile should also be written through a temp name and
`os.replace`, so a turn cannot `sandbox-exec -f` a half-written file while
another turn rewrites it. The content cannot differ between concurrent turns --
every input to `profile` comes from `cfg` -- so this is a torn read, not a race
for correctness.

## Slices

Four, one green commit each, a pull request per slice off `main`. Ordering is
forced between 2 and 4 and otherwise free.

1. **Close the escape.** The deny line and the atomic write, with a test that
   builds the real profile and asserts the write fails. This document ships with
   it rather than before it: it describes a live hole in a shipped default, and a
   proposal that publishes one ahead of its fix is worse than a late proposal.
2. **`TMPDIR` per session.** `sessions/<id>/.tmp` created by
   `ensure_session_layout`; `KINGFISHER_SCRATCH_DIR`, `scratch_root` and
   `scratch_dir` removed; `writable_roots` loses a parameter and `argv_for` a
   bind.
3. **The session owns its harness state.** `.harness/` created and protected at
   both points, the four members moved in, `keep_from` carrying the pin, and the
   marker gaining a layout version that refuses older workspaces.
4. **Retire `state_dir`.** `KINGFISHER_STATE_DIR`, `state_root` and `state_dir`
   removed; the profile at a fixed path with slice 1's single-path deny widened
   to the whole directory; `origins`' state entry; the guides.

Every slice adds a protection whose passing state looks identical to its absence,
so each wants the mutation check: break the guard, confirm red, restore.

## What is not verified, and what will follow it

The macOS half was tested on a host -- the regex deny, the read filtering, the
escape, and the one-line fix. **The bubblewrap half was not**, for want of a
Linux machine: a `--ro-bind` over a subpath of an already-bound directory is
ordinary bwrap usage, and it is still an assumption in this document rather than
a measurement.

Two readers follow the run log where it goes. `read_usage` reaches it through
`log_path`, and `log_path` is also part of what a local caller is handed on a
`RunEvent`, so the path they see changes. Neither is a reason not to move it, and
both are things the slice that moves it has to touch.

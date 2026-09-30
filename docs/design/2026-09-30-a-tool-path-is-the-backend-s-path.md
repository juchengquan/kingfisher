# A tool's path is the backend's path

**Status:** proposed, nothing built. Its questions were settled in review on
2026-10-01, under *Settled in review*.
**Date:** 2026-09-30.
**Occasion:** an audit after *A session is its backend* landed, asking what else on
the agent's side still assumed a session is a folder on this host. Three things
did. One of them turned out not to be about remote backends at all: the way a
workspace tool is handed a file is a second, divergent copy of the backend's own
path mapping, and it is wrong on the default backend too.
**Cited by symbol, not by line**, for the reason
`2026-09-05-an-answer-that-is-not-prose.md` gives.

## What was found

**1. A tool's `path` is translated against the session folder, not the backend.**
A workspace tool with an argument called `path` (`PATH_ARGUMENTS`) is handed a
real file path: `SessionPaths.real` joins the virtual path onto the session
directory and checks it stays inside. Three things are wrong with that, each
measured on 2026-09-30:

- **It diverges from the backend on the default backend.** `default_backend` routes
  `/skills/` to the catalogue, and every bundle and mount to their own directories.
  `SessionPaths.real("/skills/report/template.md")` returns
  `<session>/skills/report/template.md`, which does not exist; the backend reads the
  same virtual path from `<workspace>/skills/report/template.md`. A tool asked to
  read a skill's file fails with `FileNotFoundError` where `read_file` succeeds.
- **It hands a tool `/.harness`.** Containment is the whole check, and `.harness` is
  inside the session. `real("/.harness/agent.yaml")` and
  `real("/.harness/transcript.jsonl")` return the real files -- the pinned agent and
  the conversation the file tools are denied reading and the shell is fenced away
  from. A workspace tool runs in kingfisher's own process, outside every fence, so a
  tool that writes to its `path` could rewrite the pin. No shipped tool writes to
  its `path`, so today this is read exposure; on the default backend with no session
  key, a deployment's own writing tool would leave nothing to catch it.
- **It assumes the files are on this host.** Against a backend that keeps the
  session elsewhere, the tool is handed a local path to nothing. Driven through a
  real turn against the test backend that keeps each session in a directory of its
  own: the file was in the backend's session, the tool raised `FileNotFoundError`
  on the local one.

**2. A delegate finds its session folder by guessing.** `subagents.py` passes
`getattr(backend, "workspace", None)` to `tool_guards`, because a delegate has no
`session_dir` of its own. `WorkspaceScopedBackend` has that attribute; a
deployment's backend need not. Without it the delegate's workspace tools get no
translation and no refusal of host paths in their other arguments -- silently.

**3. Two leftovers from the last proposal.** `kingfisher decide --session ID`, asked
what a session is waiting on, first checks that `<workspace>/sessions/<id>` exists,
so it answers "no such session" for one a backend keeps elsewhere.
`RunResult.session_dir` always names that local folder, and the driver prints it
and reads outputs from it.

## Not remote, but not reachable

The first finding reads like "refuse path tools on a remote backend", and that is
the wrong rule. A backend that is not `default_backend` may keep every file on this
host (a `LocalShellBackend` rooted somewhere else, like the test backend); keep some
local and some not (the `ports.md` example keeps `/data` and `/memory` on local
folders and runs its shell remotely); or keep them on a network mount this host
reads. Whether a tool can be handed a real path is a property of **one path on one
backend**, not of which backend it is.

So the question is asked of the backend, per path, at the call.

## The proposal

**Translation asks the backend where a virtual path lives on this host, and refuses
where the answer is nowhere.** `SessionPaths` stops holding a session directory and
holds the turn's backend and its rules. For each `path` argument:

1. **The turn's deny rules first.** A path under a read-denied scope
   (`denied_read_scopes()`, today `/.harness/**`) is refused, with the same refusal
   text a file tool gives. This closes the second half of finding 1 on every
   backend, keyed or not.
2. **Then the backend's own mapping.** `host_path(backend, virtual)`:
   - a backend defining `host_path(virtual) -> Path | None` answers for itself -- how
     a deployment whose storage is a network mount says so;
   - a `CompositeBackend` hands the path to the route that owns it, with the prefix
     stripped, exactly as it does for a read;
   - a `FilesystemBackend` in virtual mode (which `LocalShellBackend` is) answers
     `cwd` joined with the path -- its public root, and the rule its own
     `_resolve_path` applies;
   - anything else answers `None`.
3. **Containment against the root that answered**, not the session directory:
   resolved, symlinks followed, and required to stay inside that route's root. A link
   the agent made in `/derived` pointing at another session is refused as it is today.
4. **`None` is a refusal the model can read**, naming the path and saying this
   backend does not keep it on this host -- and, for the tool's author in the log, that
   a tool taking `runtime: ToolRuntime[ToolContext]` reads through
   `runtime.context.backend` on any backend.

That fixes the three parts of finding 1 with one change, and finding 2 falls out of
it: a delegate is already built on the turn's backend (`SubAgentMiddleware(backend=)`),
so `tool_guards` and `guarded_tools` take the backend instead of a directory and
nothing has to guess. The refusal of host paths in a tool's *other* arguments keeps
`NOT_FOR_TOOLS`, and refuses `<workspace>/sessions/` from the `Config` rather than
from a session directory's parent.

Decided per call, not at startup. A startup rule would have to say which backends
are "local", and the honest answer is per path. It would also refuse a deployment
over a tool it never calls.

**The shipped path tools move to `ToolContext`.** `line_count`, `csv_profile`,
`log_levels`, `status_codes` and the redactor's `mask_secrets` read through
`runtime.context.backend`, so the examples work on any backend and teach the
pattern that does. Translation stays for a deployment's own tools that take a
`path`; it is the convenience, and `ToolContext` is the route that always works.

**`RunResult.session_dir` goes.** A caller reads outputs as `artifacts` and fetches
them with `Kingfisher.artifact`, on every backend; the driver does the same.

**`decide` asks the backends** whether the session exists, as `reap --session` does.

## Settled in review

Decided on 2026-10-01, each with the alternative that lost.

**A path under a write-denied scope is handed over.** `/skills/**` and `/data/**` are
read-only to the agent, and a tool may still be handed a real path under either.
Under `/data` that is safe on the default backend, because the directory is
read-only on disk. Under `/skills` it is not: the catalogue is writable on disk and
shared by every session, and a tool runs outside the shell's fence. What decided it
is the case finding 1 found broken -- a `fill_template(path="/skills/report/template.md")`
that reads a skill's template -- against the risk of a tool that writes back, such
as a `tidy_whitespace(path=...)` pointed at the same file, which would rewrite the
template for every session. Workspace tools are the operator's own reviewed code
rather than the model's, and every shipped one only reads, so the protection rests
on the operator not writing that tool. Refusing write-denied scopes was the
alternative. It would have kept the catalogue out of every tool's reach and left a
path tool unable to read a skill's file except through `ToolContext`.

**`host_path` is part of the documented backend contract.** It is optional,
described in `ports.md`, and checked by a new entry in `SESSION_BACKENDS_CONTRACT`:
what a backend's `host_path` answers for one session never resolves inside another.
The case that decided it is a deployment keeping sessions on a network mount, say
`/mnt/sessions`, behind a backend of its own. Its files really are on this host, and
with `host_path` its existing `csv_profile(path=...)` keeps working. Keeping the
method internal was the alternative. Only kingfisher's own backends would have
answered, and that deployment would have rewritten every path tool against
`ToolContext` for files that were already there. The cost is one more optional
method a deployment can get wrong -- pointing outside its session hands a tool
another session's files -- which is what the check is for.

## How it is tested

- **The `/skills` case, on the default backend:** a tool reading a skill's file
  through its `path`, which fails today and succeeds after.
- **`/.harness` refused** through a tool's `path`, on the default backend without a
  key -- the case with nothing else behind it -- driven through a real turn.
- **Mixed and remote:** against a backend whose `/data` is local and whose default is
  not, a tool reads `/data/x` and is refused `/derived/y` with the readable refusal;
  against the fully-elsewhere test backend, a `ToolContext` tool reads what a path
  tool is refused.
- **A delegate on a backend with no `.workspace`:** its tool is translated and its
  host-path refusal holds.
- **`host_path` answered by a deployment's backend:** a backend over a directory of
  its own that answers `host_path` has its path tool translated, and one that answers
  a path inside a different session fails the new kit check.
- **A link out** of `/derived` to another session is refused under the route-root
  containment, as it is under today's.
- **Mutation:** drop the deny-rule step and the `/.harness` test fails; answer the
  session directory instead of asking the route and the `/skills` test fails;
  translate on `None` and the remote test fails.

## Slices, if this moves

One green commit each, a pull request per slice off `main`.

1. **`decide` asks the backends; `RunResult.session_dir` goes.** Small, and
   independent of the rest.
2. **Translation asks the backend.** `SessionPaths` over the backend and the turn's
   rules; `tool_guards` and `guarded_tools` take the backend; the `getattr` goes.
   Closes findings 1 and 2.
3. **The shipped path tools move to `ToolContext`.** Independent of 2 and could go
   first.
4. **`host_path` documented and checked**: the `ports.md` section and the kit entry.
   Then `decisions.md` gains the entry and this document is removed.

# Configuring a deployment

Every setting kingfisher reads from the environment, what it does, and what
happens if you leave it alone.

[`.env.example`](../../.env.example) is the file you copy and edit. It carries
the same settings with the reasoning attached — why the workspace has to be
absolute, why the sandbox is on by default — and it is where an argument for a particular
value belongs. This page is the list, for when you want to know what exists
rather than why.

**One variable is required.** `KINGFISHER_WORKSPACE` has no default and raises
`ConfigError` when it is missing; everything else defaults inside it or is off.
That is deliberate: a workspace is the one thing kingfisher cannot invent, and
a default would put a deployment's sessions somewhere the call site never
mentions.

## Where things live

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_WORKSPACE` | The workspace. Everything below defaults inside it. | **required** |
| `KINGFISHER_ASSETS` | Where `kingfisher seed` copies definitions from. Without it seeding lays the workspace out, writes `models.yaml.example`, and then refuses — in that order, so a deployment with nothing to seed still gets somewhere to start. | none |
| `KINGFISHER_MODELS_FILE` | The model catalogue: which models exist, where each runs, which key it uses. | `<workspace>/models.yaml` |
| `KINGFISHER_GROUPS_FILE` | The group vocabulary. No file means access control is off entirely. | `<workspace>/groups.yaml` |
| `KINGFISHER_AGENTS_DIR` | Relocate the agents catalogue. | inside the workspace |
| `KINGFISHER_SKILLS_DIR` | Relocate the skills catalogue — this is how several deployments share one reviewed set. | inside the workspace |
| `KINGFISHER_SUBAGENTS_DIR` | Relocate the subagents catalogue. | inside the workspace |
| `KINGFISHER_TOOLS_DIR` | Relocate the tools catalogue. | inside the workspace |
| `KINGFISHER_SESSION_STORE` | A directory sessions are kept in, so they survive the machine that ran them. | none — the session directory is the only copy |
| `KINGFISHER_SESSION_STORE_FACTORY` | `module:name` naming something callable with no arguments that returns a store of your own — a bucket, a database. A factory rather than a class, because kingfisher does not know whether yours wants a DSN or a mount point. | none |
| *(none)* | Where session directories are, while a turn runs. No setting moves them; mount `<workspace>/sessions` on whatever device you want them on. | `<workspace>/sessions` |

**The last row is in the table rather than left out of it.** A reader asking how
to put sessions on another disk should find the answer here, not conclude from an
absence that it cannot be done. A mount is the answer because the harness cannot
tell a mounted directory from a plain one — it resolves the session root and
checks containment per access, which a bind mount passes and a symlink does not.
`KINGFISHER_SESSION_STORE` above is a different question: that is where a session
is *copied* for safekeeping, not where it lives while it runs. A deployment whose
session tree exists only for the length of a turn wants the `SessionRoot` port
instead — see [`ports.md`](ports.md).

The four `*_DIR` settings exist because definitions are authored and reviewed
rather than produced by a run. Relocating them is safe for the reason relocating
the state directory is: the agent reaches a catalogue through a route, and the
shell has no business there.

## Limits

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_EXECUTION_TIMEOUT_S` | How long one shell command may run. | `120` |
| `KINGFISHER_TURN_TIMEOUT_S` | How long one turn may run. | `3600` |
| `KINGFISHER_RECURSION_LIMIT` | How many steps a turn may take before it stops. | `150` |
| `KINGFISHER_SESSION_MAX_BYTES` | Cap on what one session may hold. Checked between turns, never during one. | none — unbounded |
| `KINGFISHER_SESSION_TTL_S` | How long an idle session survives before it is swept. | `604800` (7 days) |

**Unbounded is survivable on a disk and is not survivable in memory.** A
deployment whose sessions are memory-backed shares one fixed size between every
session in the process, so one session can starve the rest. `kingfisher doctor`
says so when it finds that arrangement without a cap — and it measures the
sessions tree, so a tmpfs mounted at `<workspace>/sessions` counts whether or not
the workspace around it is on a disk.

## What the agent is allowed

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_SKILLS_ENABLED` | Skills the agent may read and run. Costs ~450 tokens of preamble on every turn before a single skill is named, which is why it is a switch. | `false` |
| `KINGFISHER_MEMORY_ENABLED` | The memory directory a session carries between turns. On means runs stop being repeatable: the agent writes notes that come back. | `false` |
| `KINGFISHER_INTERPRETER_ENABLED` | A JavaScript sandbox the agent can compute in: no filesystem, no network, capped memory and time. | `false` |
| `KINGFISHER_CONVERSATION_ENABLED` | Whether a session remembers earlier turns. **The one flag that is on unless you turn it off.** | `true` |

A flag reads as true for `1`, `true`, `yes` or `on`. **Anything else is false**,
including a value that looks entirely deliberate: `y`, `enabled` and a path are
all off, with no error. That is worth knowing before you write one from memory.

All four carry `_ENABLED` so that "whether" is visibly a different question from
"where" — `KINGFISHER_SKILLS_ENABLED` beside `KINGFISHER_SKILLS_DIR`. The bare
names are read by nothing. `kingfisher doctor` reports one that is still set,
which is the only thing that will: a name that stops being read takes its
setting's effect with it and says nothing.

These two are the only capabilities with a flag, and the reason is the prompt.
Each splices a section into the base prompt, which is the cached prefix every
turn is compared against — so whether they are on has to be a deployment-stable
fact, decided once. Tools, subagents and agents are attached per request through
capabilities instead, and leave the prefix alone. A request may decline memory
this deployment wired, and when it does the *reads* are denied rather than the
prompt rewritten, for the same reason.

## Keeping the shell in its place

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_SHELL_SANDBOX` | `auto` uses whatever the platform offers, `external` says the runtime already confines this process, `off` opts out and warns on every start. | `auto` |
| `KINGFISHER_SHELL_PATH_EXTRA` | Extra directories on the agent's `PATH`, which is how it reaches something like `/opt/homebrew/bin`. | empty |

`execute` reaches the whole host filesystem regardless of the virtual paths the
file tools use, so this is the boundary rather than a tidying preference. A
container that mounts only the workspace has already provided one and should say
so with `external`; a developer's machine has provided nothing, which is why
`auto` is the default rather than `off`.

Whatever you add to `PATH` is granted to the fence as readable, so a directory
named here is one the agent can run from.

## The HTTP service

A prefix of its own, so that reading a deployment's environment tells you which
half of the split each setting belongs to. **These are not in `.env.example`.**

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_SERVICE_HOST` | Address to bind. | `127.0.0.1` |
| `KINGFISHER_SERVICE_PORT` | Port to bind. | `8000` |
| `KINGFISHER_SERVICE_MAX_BODY_BYTES` | Largest request body accepted. | `1048576` (1 MiB) |
| `KINGFISHER_SERVICE_HEARTBEAT_S` | How often a streaming response sends a keep-alive. | `15.0` |
| `KINGFISHER_SERVICE_FILE_STORE_DIR` | Where files named by id are fetched from. | none |
| `KINGFISHER_SERVICE_AUDIT_CONTENT` | Whether the audit log records content rather than only events. | `false` |

`KINGFISHER_SERVER_*` was the earlier prefix and is read by nothing. Renaming an
environment variable is the one rename that fails in silence — a moved import
stops the program and says which, while a variable nobody reads falls back to its
default and the server comes up on port 8000 with nothing to show for it — so
`kingfisher doctor` reports the whole prefix, suffix by suffix, and is worth
running after an upgrade.

## Two things that catch people

**`KINGFISHER_SKILLS` means one thing now, and it is not a flag.** The agent's
shell gets it holding the *path* to the skills catalogue, which is how a skill's
scripts reach their neighbours; nothing reads it as a yes/no any more. That is
what closed the trap `_ENABLED` was introduced for — a deployment writing the
path was setting a flag to a value no parser recognises, and skills went **off**
with no error. The two can no longer arrive at one reader.

**A deployment configured by reading `.env.example` will miss the service
settings**, including the port. The file covers the library and stops there.

## Checking it

`kingfisher doctor` reports what stands between an install and a run: a missing
catalogue, a shell with no confinement, a memory-backed sessions tree whose
arithmetic does not work, a setting nothing reads any more. It is the fastest way to find out whether the
environment you have assembled is the one you meant.

# Configuring a deployment

Every setting kingfisher reads from the environment, what it does, and what
happens if you leave it alone.

[`.env.example`](../../.env.example) is the file you copy and edit. It carries
the same settings with the reasoning attached — why scratch is created `0700`,
why the sandbox is on by default — and it is where an argument for a particular
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
| `KINGFISHER_STATE_DIR` | Harness state the agent never addresses — run logs, session claims. | `<workspace>/.kingfisher` |
| `KINGFISHER_SCRATCH_DIR` | The agent's `$TMPDIR`. Created `0700` and checked; a directory that is not yours is refused rather than used. | `<state dir>/tmp` |
| `KINGFISHER_AGENTS_DIR` | Relocate the agents catalogue. | inside the workspace |
| `KINGFISHER_SKILLS_DIR` | Relocate the skills catalogue — this is how several deployments share one reviewed set. | inside the workspace |
| `KINGFISHER_SUBAGENTS_DIR` | Relocate the subagents catalogue. | inside the workspace |
| `KINGFISHER_TOOLS_DIR` | Relocate the tools catalogue. | inside the workspace |
| `KINGFISHER_SESSION_STORE` | A directory sessions are kept in, so they survive the machine that ran them. | none — the session directory is the only copy |
| `KINGFISHER_SESSION_STORE_FACTORY` | `module:name` naming something callable with no arguments that returns a store of your own — a bucket, a database. A factory rather than a class, because kingfisher does not know whether yours wants a DSN or a mount point. | none |

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
deployment whose workspace is memory-backed shares one fixed size between every
session in the process, so one session can starve the rest. `kingfisher doctor`
says so when it finds that arrangement without a cap.

## What the agent is allowed

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_SKILLS` | Skills the agent may read and run. | `false` |
| `KINGFISHER_MEMORY` | The memory directory a session carries between turns. | `false` |
| `KINGFISHER_INTERPRETER` | A JavaScript sandbox the agent can compute in: no filesystem, no network, capped memory and time. | `false` |
| `KINGFISHER_CONVERSATION` | Whether a session remembers earlier turns. **The one flag that is on unless you turn it off.** | `true` |

A flag reads as true for `1`, `true`, `yes` or `on`. Anything else is false,
including a value that looks deliberate — see the trap below.

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

`KINGFISHER_SERVER_*` was the earlier prefix. It is still read, the new name
wins where both are set, and using the old one says so once — because renaming
an environment variable is the one rename that fails in silence, where a moved
import stops the program and says which.

## Two things that catch people

**`KINGFISHER_SKILLS` means two different things.** To a deployment it is a
yes/no that turns skills on. To the agent's shell it is the *path* to the skills
catalogue, exported under that same name so a skill's own scripts can find their
neighbours. Set it to a path in your own environment — the natural mistake,
since that is what the name means everywhere the agent can see it — and the flag
parser reads a value that is not `1/true/yes/on`, which is **false**. Skills go
off, with no error and nothing in the log.

**A deployment configured by reading `.env.example` will miss the service
settings**, including the port. The file covers the library and stops there.

## Checking it

`kingfisher doctor` reports what stands between an install and a run: a missing
catalogue, a shell with no confinement, a memory-backed workspace whose
arithmetic does not work. It is the fastest way to find out whether the
environment you have assembled is the one you meant.

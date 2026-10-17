# Configuring a deployment

Every setting kingfisher reads from the environment, what it does, and what
happens if you leave it alone.

[`.env.example`](../../.env.example) is the file you copy and edit. It carries
the same settings with the reasoning attached — why the workspace has to be
absolute, why the sandbox is on by default — and it is where an argument for a particular
value belongs. This page is the list, for when you want to know what exists
rather than why.

**Two variables are required**, and they name two folders kept apart.
`KINGFISHER_DEFINITIONS_DIR` is what people write: the definitions, `models.yaml`
and `source_ids.yaml`. `KINGFISHER_WORKSPACE` is what sessions make. Neither has a
default, each raises `ConfigError` when it is missing, and everything else is off
or defaults inside one of them. That is deliberate: these are the two things
kingfisher cannot invent, and a default would put a deployment's sessions, or the
definitions it serves, somewhere the call site never mentions. Seeding needs only
the definitions folder, since that is all it writes.

## Where things live

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_WORKSPACE` | What sessions make: `sessions/`, and kingfisher's own `.kingfisher/` beside it. | **required** |
| `KINGFISHER_DEFINITIONS_DIR` | What people write, under fixed names: `agents/`, `middlewares/`, `skills/`, `subagents/` and `tools/`; `models.yaml`, the model catalogue — which models exist, where each runs, which key it uses; and `source_ids.yaml`, the source-id vocabulary, whose absence turns access control off entirely. A missing kind folder means none of that kind. A start reads it and never writes it: a folder that is not there is refused rather than made. Neither it nor the workspace may be inside the other. | **required** |
| `KINGFISHER_ASSETS` | Where `kingfisher seed` copies definitions from — the assets. This repository ships its own in `assets/`; definitions from another project belong outside the checkout, seeded with a second `--from`. Without it seeding lays the definitions folder out, writes `models.yaml.example`, and then refuses — in that order, so a deployment with nothing to seed still gets somewhere to start. | none |
| `KINGFISHER_MODEL_KEYS_FILE` | The file an endpoint's `key:` names an entry in, one `name: key` line each. Read only once this names it; named and missing, unreadable or not such a list, it refuses startup. Keep it out of the workspace, and on macOS under the home folder — see *What each fence hides*. `seed` places a `keys.yaml.example` to copy out; `doctor` says where each key came from, and warns when the shell or another user can read the file. | none |
| `KINGFISHER_WIRE_FORMATS` | `module:name` naming something callable with no arguments that returns wire formats of your own, for an endpoint's `api` to name. See [below](#a-wire-format-kingfisher-does-not-ship). `KINGFISHER_ADAPTERS_FACTORY`, its old name, is refused if still set. | none — `anthropic` and `openai_responses` only |
| `KINGFISHER_SKILLS_MOUNTS` | Further skills directories, as `label=path` pairs separated by `:`. Each is offered at `/skills/<label>/` and granted as `<label>::<name>`, for a party that cannot write into the catalogue. Skills sit directly in each one, and nothing creates or seeds it. The shell reaches a mount's scripts as `$KINGFISHER_SKILLS/<label>/...`, through a view of links kingfisher keeps in `<workspace>/.kingfisher/`. | none |
| `KINGFISHER_SESSIONS` | `module:name` naming something callable with no arguments that returns a `Sessions`, for the `kingfisher` command. Only the command reads it — `Kingfisher` takes its sessions as `sessions=` — and it is how `sessions`, `reap` and `artifact` see sessions yours keep elsewhere. `KINGFISHER_SESSION_STORE` and `KINGFISHER_SESSION_STORE_FACTORY`, which configured the store this replaced, are refused if still set, and so are `KINGFISHER_SESSION_BACKENDS_FACTORY` and `KINGFISHER_BACKEND_FACTORY`, this setting's old names. | `LocalSessions` |
| `KINGFISHER_PLUGINS` | `module:name` naming something callable with no arguments that returns a `kingfisher.plugins.Plugins`: the run events, offered middleware and instructions the `kingfisher` command hands every `Kingfisher` it builds, as `Kingfisher` takes them as keywords. A field left unset is kingfisher's own. `kingfisher run --log` replaces the run events it names. | kingfisher's own of each |
| *(none)* | Where session directories are, while a turn runs. No setting moves them; mount `<workspace>/sessions` on whatever device you want them on. | `<workspace>/sessions` |

**The last row is in the table rather than left out of it.** A reader asking how
to put sessions on another disk should find the answer here, not conclude from an
absence that it cannot be done. A mount is the answer because the harness cannot
tell a mounted directory from a plain one — it resolves the session root and
checks containment per access, which a bind mount passes and a symlink does not.
A deployment whose sessions should not be directories on this host at all names a
backend that keeps them itself — see [`ports.md`](ports.md).

The definitions folder is apart from the workspace because definitions are
authored and reviewed rather than produced by a run, and one reviewed set can serve
several deployments. Keeping it elsewhere is safe for the reason it is worth doing:
the agent reaches a catalogue through a route, and the shell has no business there
— on macOS a definitions folder under your home folder is hidden from the shell but
for `skills/`, whose scripts it runs. One folder rather than a setting per kind:
`KINGFISHER_AGENTS_DIR`, its four siblings, `KINGFISHER_MODELS_FILE` and
`KINGFISHER_SOURCE_IDS_FILE` moved one thing each, and are refused if still set. A
definition left in a workspace from before is read by nothing; delete it, or move it
into the definitions folder.

## Limits

| Variable | What it does | Default |
| --- | --- | --- |
| `KINGFISHER_EXECUTION_TIMEOUT_S` | How long one shell command may run. | `120` |
| `KINGFISHER_TURN_TIMEOUT_S` | How long one turn may run. | `3600` |
| `KINGFISHER_RECURSION_LIMIT` | How many steps a turn may take before it stops. | `150` |
| `KINGFISHER_THREAD_POOL_SIZE` | How many threads `astream` and `arun` hand blocking work to. One pool per process: a second service asking for a different number is refused when it is built. It serves the short work a turn does on this host, and any session-backends method left to its default, which runs the sync one on it: raise it for many turns at once on remote sessions whose async methods are those defaults. | `16` |
| `KINGFISHER_SESSION_MAX_BYTES` | Cap on what one session may hold. Checked between turns, never during one. | none — unbounded |
| `KINGFISHER_SESSION_TTL_S` | How long an idle session survives a sweep — `kingfisher reap`, or a deployment's own call to `reap()`. Nothing sweeps on a schedule. | `604800` (7 days) |

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
| `KINGFISHER_CONVERSATION_ENABLED` | Whether a session remembers earlier turns. **The one flag that is on unless you turn it off.** Also what an approval gate pauses into, so an agent with `interrupt_on` refuses to build where this is off. | `true` |

A flag reads as true for `1`, `true`, `yes` or `on`. **Anything else is false**,
including a value that looks entirely deliberate: `y`, `enabled` and a path are
all off, with no error. That is worth knowing before you write one from memory.

All four carry `_ENABLED` so that "whether" is visibly a different question from
"where" — `KINGFISHER_SKILLS_ENABLED` beside `KINGFISHER_SKILLS_MOUNTS`. The bare
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
| `KINGFISHER_SHELL_SANDBOX` | `auto` uses whatever the platform offers, `bubblewrap` asks for bubblewrap even where Landlock would run, `external` says the runtime already confines this process, `off` opts out and warns on every start. | `auto` |
| `KINGFISHER_SHELL_PATH_EXTRA` | Extra directories on the agent's `PATH`, which is how it reaches something like `/opt/homebrew/bin`. | empty |

`LocalSessions` reads both as it is built, so they reach every service on kingfisher's
own sessions, the command's and yours. Code that decides instead passes
`LocalSessions(shell=ShellSettings(...))`, which reads neither -- see
[`ports.md`](ports.md#sessions--the-filesystem-the-agent-runs-against). Sessions of your
own fence their shell themselves, and neither setting reaches them.

`execute` reaches the whole host filesystem regardless of the virtual paths the
file tools use, so this is the boundary rather than a tidying preference. A
container that mounts only the workspace has already provided one and should say
so with `external`; a developer's machine has provided nothing, which is why
`auto` is the default rather than `off`.

**`external` is a boundary against the host, not between callers.** One container
serves every session, so "the runtime already confines this process" says nothing
about session A reading session B — the file tools give each session a root of
its own, and the shell does not go through the file tools. A single instance
serving several callers wants `auto` inside the container as well, which fences
each turn to its own session directory. `tests/linux/test_fence_escapes.py` is
the list of ways out that fence is held to, each one paired with a control that
runs unfenced and proves the escape works without it.

On Linux `auto` reaches Landlock only where `sandlock` is installed and the
kernel offers ABI 6; `pip install 'kingfisher[fence]'` supplies the first, and
the shipped image does. Where Landlock cannot run, `auto` falls back to
bubblewrap, which needs a seccomp profile permitting user namespaces — Docker's
default denies them. bubblewrap also closes the shell's network, so a script the
agent runs through `execute` can no longer download anything; `http_fetch` is a
registered tool and is unaffected. Set `bubblewrap` to get that closed network
even where Landlock would run. `kingfisher doctor` names the mechanism it settled
on and says why when it settled on nothing.

Whatever you add to `PATH` is granted to the fence as readable, so a directory
named here is one the agent can run from.

### What each fence hides

The fences differ in shape, not just in strength. On Linux, bubblewrap and Landlock
start from nothing and grant the shell a list: the system directories, the
interpreter, the skills, and the session's own directory. macOS `sandbox-exec` does
the opposite. It allows everything and denies the operator's home, then gives the
workspace, the interpreter and the skills back inside it. What follows from that,
measured on 2026-10-01 -- on macOS by probes through `backend_at`, on Linux from the
fences' code -- and for the definitions folder on 2026-10-13, the same way:

| The shell reading… | macOS `sandbox-exec` | bubblewrap | Landlock | No fence |
|---|---|---|---|---|
| The operator's home | hidden | hidden | hidden | readable |
| The workspace | readable | hidden | hidden | readable |
| The definitions folder, `models.yaml` included | under the home folder, hidden but for `skills/`; elsewhere readable; never writable | `skills/` only, read-only | `skills/` only, read-only | readable |
| Other sessions' folders | readable and writable | hidden | hidden | readable and writable |
| Anywhere else -- `/tmp`, `/Users/Shared`, `/opt` | readable | hidden | hidden | readable |
| `/etc`, `/usr` | readable | readable | readable | readable |
| A process's environment as it started | readable | hidden | not settled | readable |
| The network | open | closed | open | open |

**On macOS the sandbox does not keep sessions apart.** It hides the operator's home,
not one session from another, so each session's agent can read and change the others'
files -- measured on 2026-10-11 with the workspace both inside the home folder and
outside it. Keep people apart on one Mac by giving each a workspace of their own.
`doctor` says it as `sessions apart`, and so does the warning a run prints when nothing
confines the shell.

"No fence" is `off`, `external` with nothing outside actually confining the shell,
and a Linux host where neither fence could start, which warns and runs.

The environment row is the one that surprises. On macOS a sandboxed shell cannot run
`ps`, but only because the sandbox will not launch a setuid binary. The system call
`ps` uses still answers, through any interpreter on `PATH`, for every process the
operator runs. It answers with the environment each process *started* with. So a key
exported in a shell profile, a launchd plist or a container's `env:` is readable, and
one `.env` added after kingfisher started is not. Landlock grants `/proc`, where
command lines are readable. The kernel is expected to refuse another process's
`environ`, but no test here holds it to that.

**Kingfisher does not keep the model keys from the shell; where they sit decides it.**
On macOS, keep the keys file `KINGFISHER_MODEL_KEYS_FILE` names under the home folder
and out of the workspace and the definitions folder, keep `.env` under it too and
start kingfisher from there,
and do not export keys before it starts. A key set in code with
`Endpoint(api_key=...)` reaches no file and no starting environment. A remote backend
runs its shell on another machine, and this one's keys are not there to read.

## A wire format kingfisher does not ship

An endpoint's `api` in `models.yaml` names a wire format: which LangChain chat
class to build, and what it needs to be told. kingfisher ships `anthropic` and
`openai_responses`. To add one, return it from a factory and name the factory:

```
KINGFISHER_WIRE_FORMATS=mycompany.models:wire_formats
```

```python
from kingfisher import Landing
from kingfisher.plugins import WireFormat

def wire_formats():
    return {
        "chat_completions": WireFormat(
            "langchain_openai:ChatOpenAI",
            Landing(
                model="model_name",
                base_url="openai_api_base",
                api_key="openai_api_key",
                max_tokens="max_tokens",
                timeout="request_timeout",
            ),
        ),
    }
```

```yaml
endpoints:
  my-gateway:
    api: chat_completions
    base_url: https://gateway.example/v1
    key_env: GATEWAY_API_KEY
```

The class is named as `module:Class` and imported only when a model is built.
Its package has to be installed; kingfisher installs only the two it ships.

`Landing` is the part to get right. kingfisher passes every class the same
keywords — `model`, `base_url`, `api_key`, `max_tokens`, `timeout`, and
`max_retries` where a model sets it — and each class keeps them under its own
attribute names. `Landing` says which, and a model
that did not keep a value there is refused as it is built. That refusal is the
point: a class handed a keyword it does not know moves it aside with a warning
and builds anyway, so a URL it did not keep sends your prompts, and the gateway's
key, to the vendor's default host. `max_retries` is the one a `Landing` may leave
out, for a class with nowhere to keep it; a model setting it on that format is
refused.

A name kingfisher already ships is refused rather than replaced, so an existing
`api: anthropic` cannot quietly start meaning your class. `extra` on a `WireFormat`
is passed to every model it builds, and may not name anything the model entry
already sets.

The setting is read wherever the catalogue is, so `kingfisher doctor` and
`kingfisher run` see your rows as well as your own program does.

## A thing that catches people

**`KINGFISHER_SKILLS` means one thing now, and it is not a flag.** The agent's
shell gets it holding the *path* to the skills catalogue, which is how a skill's
scripts reach their neighbours; nothing reads it as a yes/no any more. That is
what closed the trap `_ENABLED` was introduced for — a deployment writing the
path was setting a flag to a value no parser recognises, and skills went **off**
with no error. The two can no longer arrive at one reader.

## Checking it

`kingfisher doctor` reports what stands between an install and a run: a missing
catalogue, a shell with no confinement, a memory-backed sessions tree whose
arithmetic does not work, a setting nothing reads any more. It is the fastest way to find out whether the
environment you have assembled is the one you meant.

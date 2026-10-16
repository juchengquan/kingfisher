# Core and plugins

**Status:** proposed. Slice 1 is this document and the startup check that replaced the
import-cost tables; the rest is listed under *Slices*, one pull request each.
**Date:** 2026-10-16.
**Occasion:** a review of the package's shape. The four layers -- `domain/`,
`application/`, `infrastructure/`, `presentation/` -- no longer describe how the code
depends on itself, and what a deployment may replace is spread over a constructor, two
settings and a guide that tells it most of the interfaces are not for replacing. This
replaces the layers with a core, a set of plugins that each ship a default, and the
command.
**Cited by symbol, not by line**, for the reason
`docs/design/2026-09-05-an-answer-that-is-not-prose.md` gives.
**Supersedes, once built,** *Layering*, *The front door* and *Splitting a file* in
`decisions.md`. The last slice replaces all three with one entry. *Splitting a file*
rejected splitting by subject twice; this proposal splits anyway, on the decision of the
person who owns the repository, and that entry will say so.

## Why

**The layers are not the dependencies.** `domain/` imports `kinds/`, `application/`
imports `infrastructure/harness/` at module scope in more than a dozen places, and more
than a dozen other imports of the harness are deferred into functions to get past the
rule rather than because the dependency is wrong. `decisions.md` already describes the
code as an adapter over deepagents, which is a core with deepagents inside it, not a
domain with deepagents at the edge.

**The harness quarantine protects one of its two things.** It was to keep a deepagents
upgrade inside `infrastructure/harness/` and keep startup cheap. The last upgrade,
*Upgrade deepagents to 0.7.22*, changed `infrastructure/session_files.py` and
`kinds/subagents/spec.py`, neither inside it. Startup cost is real and worth holding --
`import deepagents` is about 1.4s where `kingfisher seed` is about 20ms -- and a check
that imports for real in a fresh interpreter holds it without a folder rule.

**What a deployment may replace is not one list.** `Kingfisher(...)` takes seven
keyword arguments, `backends` among them as required. Two settings name factories, for
session backends and for wire formats, and nothing else a deployment can replace has
one, so the command cannot reach most of what the library can. `ports.md` documents six
repository Protocols and says they are not for replacing.

## Words

A **plugin** is a part of kingfisher a deployment may replace with its own. Each ships a
**default**, used when the deployment names none, so replacing one is never required.
The **interface** is what a plugin must be for the core to use it, stated once, by the
core. The **core** is everything every deployment runs whatever it has replaced: what a
request, a turn, a session and a grant are, and the interfaces.

"Port", "adapter", "module" and "extension" are not used for any of these. `Adapter` is
renamed because it was never one: it is a model wire format.

## The shape

```
kingfisher/
  __init__.py          the public names for embedding, and for tool files
  core/
    interfaces.py      the six interfaces
    definitions/       the five kinds, the catalogue, seeding, templates/
    access/            capabilities, source-id access, audiences, datazones
    agent/             building one request's deepagents graph, kingfisher's own
                       middleware, and prompts/ -- what kingfisher tells every agent
    session/           one session's state and one turn
    config.py layout.py request.py result.py service.py run.py inventory.py
    origins.py reporting.py
  plugins/
    __init__.py        Plugins, every field defaulted
    sessions/          LocalSessions: session folders, backend, sandbox, runner,
                       the turn lock, and prompts/ -- what the shell says of itself
    events.py conversations.py wire_formats.py instructions.py
  cli/
tests/checks/          the contract checks, no longer shipped
```

Which file goes where is settled per move, against the history of which files change
together, and the disagreements are brought to review rather than decided in a move.

## The plugins

| Plugin | Interface | Default |
|---|---|---|
| Sessions | `open`, `sessions`, `size`, `delete`, `mark_used` and their async twins, and the turn lock: `claim(cfg, session_id, *, stale_after)`, `release`, `held` | `LocalSessions` |
| Run events | `record(event)` | the logger sink and the JSONL sink |
| Conversation store | langgraph's checkpointer, unchanged | an in-memory saver |
| Wire formats | rows, named by format | the shipped rows; `Adapter` becomes `WireFormat` |
| Offered middleware | a name for each factory | none |
| Instructions | `text(cfg) -> str` | `INSTRUCTIONS.md` in the definitions folder |

**Sessions takes the turn lock.** Today it sits on the backend `open` returns, so a
deployment's own backend has to carry `claim`, `aclaim`, `release`, `arelease` and
`held` beside the filesystem it is. On Sessions, the backend `open` returns is a plain
deepagents backend, with `host_path` and `shell_prompt` as its only optional extras.
`unprotected`, which the service reads with `getattr`, becomes `LocalSessions`' own and
is reported through the log. `type(backends) is DefaultBackends` becomes `isinstance`.

**Instructions replaces two settings with one plugin.** A deployment's standing text for
every agent and subagent is read after kingfisher's own prompt and before the agent's.
It replaces `Config.deployment_prompt` and the workspace's `PROMPT.md`, and its default
reads a third file beside the two `authored_files_for` already names, `models.yaml` and
`source_ids.yaml`. What kingfisher tells every agent stays in the core and is not
replaceable; what the shell says of itself belongs to the Sessions plugin that runs it.

**The conversation store is carried over unchanged.** Its pause path rebuilds a paused
turn from fields of `InMemorySaver`, and whether it holds for a saver a deployment
supplies is a follow-up, below.

## Wiring

`Kingfisher(cfg)` runs with every default. `Kingfisher(cfg, plugins=Plugins(sessions=...))`
replaces one. `KINGFISHER_PLUGINS=module:callable` names a callable returning `Plugins`,
which gives the command the same reach as the library and replaces both `*_FACTORY`
settings. `catalogue` and `grants` move onto `Config`, which is otherwise left as it is.
`Kingfisher`'s methods keep their names and signatures. `graph=` stays: a deployment may
still run a graph it compiled itself, and then has to name the sessions it keeps.

**`backends` stops being required, and that reverses a reason written on it.** It has no
default so that a deployment cannot wire the service without learning there is a
boundary, because the backend is the sandbox. With Sessions a plugin, the default is
`LocalSessions`, which is that sandbox: naming nothing gets the boundary, not its
absence, and a deployment replacing it meets the interface it has to satisfy.

## The public names

Two addresses. `kingfisher`, for embedding and for tool files -- `Config`, `Request`,
the result types, `Capabilities`, the errors, `run` and `stream`, seeding and
`inventory`, `ToolContext` and `tool`. `kingfisher.plugins`, for writing a plugin --
`Plugins`, the six interfaces, every default, `CommandRunner`, `CommandResult`,
`ShellPrompt` and `HostPathError`. Everything else is private and may move. The command
may import anything.

The contract kits leave the wheel for `tests/checks/`. What each one checks is written
on the interface it checks, as a promise, and `ports.md`'s *Checking what you wrote*
becomes that list of promises. Two of the checks are refusals the core makes of every
backend at startup rather than tests, and stay in the core: that deepagents recognises
the backend as one with a shell, and that the routes cover the layout.

## The rules

- The core imports no plugin, except `core/service.py` importing `Plugins`.
- A plugin imports the core and never another plugin.
- deepagents is part of the core.
- `import kingfisher` loads no installed package. The command loads nothing but `dotenv`
  and `yaml` before it knows its verb. Every public name except the few that build or run
  an agent loads none of the agent runtime. All three are imported for real, in a fresh
  interpreter. **Built in slice 1**, replacing `SDK_LOADING`, `LIGHT_EXPORTS` and
  `HEAVY_EXPORTS` and the probes that duplicated them. `langchain_core` is outside the
  runtime on purpose: `kingfisher.tool` subclasses its `StructuredTool`.
- The export table and `__all__` agree, as today.

About half of today's architecture rules name today's folders. Those go, each in the
move that breaks it; the rest stay, and the last slice adds the rules above.

## What goes

The six repository Protocols. Both
`*_FACTORY` settings. `Config.deployment_prompt` and the workspace's `PROMPT.md`. The
shipped contract kits and their `*_CONTRACT` names. The table that justifies each public
name. The rules tied to today's folders.

## Slices

One pull request each, off `main` once the one before it has merged.

1. This document, and the startup check.
2. Delete the repository Protocols.
3. `backends` stops being required, before anything renames it, so the call sites that
   only named the default are deleted once rather than renamed first.
4. Sessions: `SessionBackends` becomes `Sessions`; `DefaultBackends`, `default_backends`
   and `backend_at` become `LocalSessions`; the turn lock moves onto it; `unprotected`
   becomes its own, still reported to the caller as `protect_failed`.
5. `Plugins` and `KINGFISHER_PLUGINS`.
6. `Adapter` becomes `WireFormat`.
7. The Instructions plugin.
8. `catalogue` and `grants` onto `Config`.
9. The contract kits to `tests/checks/`; the two refusals into the core; the promises
   onto the interfaces and into `ports.md`.
10. Move `plugins/`, splitting the prompts between it and the core.
11. Move `core/definitions`.
12. Move `core/access`.
13. Move `core/agent`.
14. Move `core/session`.
15. Move the flat core.
16. Move `cli/`.
17. The new import rules, one `decisions.md` entry in place of the three this supersedes,
    `CLAUDE.md` and the guides, and this document deleted.

Renaming before moving keeps every move a pure move, which is what lets `git log
--follow` and review read it as one. After its move, each file over about 600 lines is
split by subject in a pull request of its own, with the cut lines agreed first:
`service.py`, the command's `__main__.py` (a module per verb), `health.py`,
`inventory.py`, `backend.py`, `agent.py`, `confinement.py`, `subagents/spec.py` and
`domain/access.py`.

## Not in this

- **Regrouping `Config`.** Only `catalogue` and `grants` join it here.
- **Trimming `Kingfisher`'s methods.**
- **Whether to drop `graph=`.** It was proposed here as a seam only tests used, and is
  not: `upgrading.md` documents running a graph a deployment compiled itself, and the
  tests hand it 145 stand-ins of several kinds. Kept until that is decided on its own.
- **Pausing with a deployment's own conversation store**, which the pause path has not
  been shown to support.
- **Refusing a save outside the session's folders.** It was built once, on a layout that
  has since become configurable, and is rebuilt on the new shape after the moves.

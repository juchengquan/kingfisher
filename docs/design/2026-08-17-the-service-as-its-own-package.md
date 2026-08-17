# The HTTP service, as its own package

**Status:** implemented.
**Date:** 2026-08-17

`pip install kingfisher` should not put a web service on disk. Today it does —
`packages = ["src/kingfisher"]` ships `presentation/` to everyone, and the
`server` extra decides only whether fastapi and uvicorn are *installed*, not
whether the service *is there*.

## What was measured first

The layers are already clean, and the separation is further along than it
looks:

| | third-party imports |
|---|---|
| `domain`, `application`, `cli`, root | **none** |
| `infrastructure` | deepagents, langchain, langgraph, yaml, aiosqlite |
| `presentation` | fastapi, starlette, uvicorn |

`presentation/` imports **only `from kingfisher`** and its own submodules —
never into `domain`, `application` or `infrastructure` — and
`test_architecture.py` already holds that line. Outside itself, exactly one
thing refers to it: the console script. Everything else is comments.

The optional install genuinely works. With fastapi and uvicorn made
unimportable, `import kingfisher` succeeds, every public name resolves
including the agent and the service, the CLI runs, and only
`kingfisher.presentation` refuses.

Three things that changed the plan:

- **`pydantic` cannot be made optional.** It arrives with langchain, so it is
  present whether or not anyone installs the server. The extra adds fastapi and
  uvicorn and nothing else.
- **The service is not what costs.** Reaching `Kingfisher` takes 1337 ms
  against 25 ms for `Request` and `Capabilities`, and every millisecond of that
  is `infrastructure`. `application/service.py` imports nothing outside the
  standard library.
- **A circular extra resolves.** Verified with a throwaway pair of packages: a
  base whose extra names a second distribution that depends back on the base
  installs cleanly, and the second package is absent without the extra. So
  `kingfisher[service]` can pull a separate wheel.

## Decisions

| # | Decision | Why |
|---|---|---|
| V1 | **A separate wheel**, `kingfisher-service`, in `service/` — the shape `assets/` already uses. | An extra separates dependencies; only a second distribution separates code. `src/kingfisher/__init__.py` exists, so `kingfisher` is a normal package owned by the base wheel and a second wheel cannot cleanly add a subpackage inside it. That forces the import path out, which is the real cost of this and is taken deliberately. |
| V2 | **`service` everywhere**: wheel `kingfisher-service`, import `kingfisher_service`, command `kingfisher-service`, extra `kingfisher[service]`. | The naming rule that produced `presentation` dies with the move — a directory is named for where it sits in the dependency graph, and this one stops sitting alongside `domain` and `application`. One word across the install line, the import and the command is one fewer thing to remember. The command rename rides along because the import break is unavoidable anyway: one migration note beats two. |
| V3 | **`kingfisher[service]` installs it**, alongside `pip install kingfisher-service`. | It is the line that was asked for, and it works. Measured rather than assumed. |
| V4 | **`kingfisher serve` is where the pointer lives.** The base declares no `kingfisher-service` command of its own. | Two distributions cannot share a console script, and the plan said they could. Measured: installing the service replaces the base's copy, *reinstalling the base replaces it back*, and uninstalling the service deletes the command outright — so an upgrade would silently swap a working server for a note telling you to install what you already have. `kingfisher serve` already did the right thing (deferred import, `except ImportError`, a message) and collides with nothing; it only needed the new names. |
| V5 | **The service accepts `>=0.1,<0.2` of the base**, not any version. | The repo's own rule, applied to itself: it caps `langchain-quickjs` below 0.4 because "under 0.x the minor is where breaks land", and "a build that stops is a decision to make, where a silent upgrade is one already made". `kingfisher-assets` depends on `kingfisher` unpinned and gets away with it because its content rarely calls the library; the service calls the public names on every request. |
| V6 | **Settings are read from `KINGFISHER_SERVICE_*` and `KINGFISHER_SERVER_*` both**, preferring the new and saying so when the old is used. | The one rename that fails *silently*. An import break stops the program; an unread environment variable just falls back to the default and the server comes up on the wrong port. This codebase turns quiet shortfalls into loud errors everywhere else, and renaming these outright would be exactly the failure it refuses, in production, at startup. |
| V7 | **Two checks that the base works without the service**: a rule that no base module may name `kingfisher_service`, and a CI job that installs the base with nothing optional and imports every public name. | Neither exists today. CI runs `uv sync --all-extras`, so fastapi is always present and the extra's optionality is entirely untested — it works by nobody yet having added the wrong import. Reading the code catches the common mistake cheaply; only the job proves the *install*, and a static rule cannot see a package that arrived through someone else's dependencies. |

## What moves

| From | To |
|---|---|
| `src/kingfisher/presentation/` (1299 lines) | `service/src/kingfisher_service/` |
| the 7 test files naming `presentation` | `service/tests/` |
| `kingfisher-server = "kingfisher.presentation.__main__:main"` | `kingfisher-service`, declared by the new wheel |
| the half of `test_architecture.py` that checks the service imports only public names | `service/tests/` |

Staying in the base: the rule that nothing in `kingfisher` imports the service
— packaging does not enforce that direction — plus the small command from V4 and
the new `service` extra.

`ServerConfig` becomes `ServiceConfig`, and `src/kingfisher/server/`, which
holds nothing but stale bytecode from an earlier rename, is deleted.

## The cost, stated

`kingfisher.presentation` stops existing. Nothing in this repo imports it
outside its own tests, and the base package never exported it, so the break is
narrow — but it is a break, and there is no compatibility shim, because a shim
in the base would be the service code the split exists to remove.

Two distributions now have to be released together and kept in step. That is
the price of the bytes being absent, and it is only worth paying because the
bytes being absent is the requirement.


## Changed while building

**Two packages cannot share a command name.** V4 originally put a
`kingfisher-service` stub in the base, to be shadowed by the real one. A
throwaway pair of packages showed what actually happens:

```
service installed        -> REAL service
after reinstalling base  -> STUB: install basepkg[service]
after removing service   -> Failed to spawn: `demo`
```

An upgrade of the base silently replaces a working command, and removing the
service takes the pointer with it. The intent survives on `kingfisher serve`,
which was already shaped for it.

**The one permitted mention had to be narrowed.** `MAY_NAME_IT` let
`cli/__main__.py` name the service anywhere, and a mutation putting the import
at module scope passed everything — while a base install would have died before
`kingfisher list` ran. Module scope is now refused even there.

**A rule with no cases passes.** Nothing in `src/` imports the service as it
loads, so that half of the check was unreachable and deleting it changed no
result. Both halves moved into one function a test can hand a violating file,
which is the same fix `test_architecture` already carries a scar for.

## Verified on the artifact

The base wheel was built and its contents read: **no service files**, 59 in
total. Installed alone into a clean environment, all 46 public names resolve,
`kingfisher serve` exits 1 with `pip install 'kingfisher[service]'`, and
`import kingfisher_service` raises. With the service wheel added, the app
imports and `kingfisher serve` reaches the real one.

1358 tests, ruff and ty clean. Seven mutations, each caught.

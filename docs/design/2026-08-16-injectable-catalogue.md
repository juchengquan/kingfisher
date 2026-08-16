# An injectable catalogue

**Status:** implemented. Scope reduced to the seam before building — see *Corrections*.
**Date:** 2026-08-16

The question that started this was whether some of `infrastructure/` should be
classes rather than modules of free functions. The measured answer is **none of
them — and not even the new one, yet.**

What is worth building is smaller than a class: the catalogue's three
directories become an argument instead of three hardcoded reads of `Config`.
The design below is what survived; the premises it overturned are recorded in
*Corrections* rather than quietly dropped.

## The asymmetry

`Kingfisher.__init__` injects six collaborators, and one of them is
`definitions: DefinitionStore`. That makes a request's *uploaded* skills and
subagents pluggable — a catalogue service serves them by id, `uploads.provision`
materialises them under the session, and the backend routes to what it wrote.

The *catalogue* got none of that. It is hard-wired to the local filesystem in
three places:

```python
skill_store.names(cfg.skills_dir)      # agent.py:95
load_all(cfg.subagents_dir)            # agent.py:112
load_tools(cfg.tools_dir)              # agent.py:243
```

So two things are both called "store", and only one of them is a port. A
deployment that wants its reviewed catalogue from somewhere else has to patch
module-level functions.

## Why the stores stay functions

The obvious reading of "make them classes" is `SkillStore(directory)` with
`names()` and `misplaced()` on it. That buys nothing here.

`skill_store.names(directory)` is a pure function of a path. Binding the path
into a constructor does not remove the parameter, it moves it — and then
introduces a lifetime question that does not currently exist, because
`available_skills` reads *two* directories (`agent.py:95-97`), the catalogue and
the session's uploads, and the second is per-session. It also costs the
one-liner test: `skill_store.names(tmp_path) == ("flat",)`
(`test_presets.py:132`).

The layer already has a consistent rule for when infrastructure gets a class,
and it is none of these:

- an adapter satisfying a domain Protocol — `LocalSessionDirs`
- a subclass a foreign framework demands — `ConfinedShell`,
  `WorkspaceScopedBackend`, `JsonlRunLogger`, the four middlewares in `scoping`
- a frozen dataclass as a value object — `Confinement`, `Seeding`, `Usage`,
  `Brought`, `DataPlacement`, `Provider`
- an exception

## Why there is no new class either

A `CatalogueSource` protocol with a `LocalCatalogue` adapter was designed in
full, and then measured against the decision to build the seam before a
consumer exists. `LocalCatalogue` returns three paths from `Config` after making
sure the directories are there. Nothing else. It is a function with a `.roots()`
attached.

Everything that would justify an object — fetching, staging, cleaning up after
itself, re-resolving when the upstream catalogue changes — is exactly what is
being deferred until somebody asks for it. So the object would be an interface
invented for a consumer who has not arrived, shipped in the public API, where
it has to be kept or broken.

The mapping does the whole job for now, and the protocol stays available as an
additive change later: accept a mapping *or* something with `roots()`, and no
existing caller breaks.

## The shape

```python
Kingfisher(catalogue_roots={"skills": …, "subagents": …, "tools": …})
```

Omit it and the three directories come from `Config`, as they do today.

Supply it and a deployment can put its catalogue anywhere, fetched however it
likes. Staging and durability are the caller's — which is where they were going
to end up regardless, since the backend routes `/skills/` at a **path**
(`backend.py:340`) and the agent reads that path on every turn, long after
construction returned.

The mapping is resolved and checked **once**, in `__init__`, then threaded to
`build_agent` and `build_backend` per request.

Everything downstream is unchanged. These are still ordinary directories, so
`skill_store.names`, `skill_store.misplaced`, `subagent_store.load_all` and
`tool_store.load_tools` run against them exactly as written — and `misplaced`
now catches a *foreign* catalogue that nested its skills too deep, which is the
same silent failure it was built for.

## Decisions

| # | Decision | Why |
|---|---|---|
| C1 | **Paths, not content.** Whatever supplies the catalogue supplies finished directories. | Skills are not read by kingfisher. They are read by the agent, through `FilesystemBackend(root_dir=str(cfg.skills_dir))` and, for the shell, through the sandbox profile's readable roots (`backend.py:330,340`). Anything that only answered "which skills exist" would let `build_agent` advertise a name the agent then cannot open. Tools have the same constraint for a different reason: `spec_from_file_location` needs a real file. |
| C2 | **Three named roots, not one tree.** | `skills_root`, `subagents_root` and `tools_root` are three independent overrides (`config.py:119-121`), deliberately — *"pointing several deployments at one directory is how a reviewed catalogue serves all of them."* There may be no common parent. The mapping already exists inverted, as `presets.destinations(cfg)`. |
| C3 | **A plain mapping, not an object.** | See *Why there is no new class either*. A mapping also has no lifetime to manage, which matters: `Kingfisher` has none and is not growing one. Where this codebase needs a lifetime it puts the manager *outside* and injects the result — `async with async_checkpointer(cfg) as threads: Kingfisher(cfg, threads=threads)` (`checkpointing.py:98`). |
| C4 | **The caller owns durability.** | Forced as well as preferred: `test_the_application_layer_does_not_write_to_disk_itself` bans `mkdir`/`copytree`/`copy` in `application/`, so `Kingfisher.__init__` could not stage anything even if it wanted to. It takes paths, checks them, and hands them on. |
| C5 | **Application defaults; infrastructure does not.** `Kingfisher` derives from `Config` when the argument is omitted; `build_agent` and `build_backend` take paths. | The same split `dirs` and `threads` already follow — infra never invents a `LocalSessionDirs`, `Kingfisher.__init__` chooses one (`service.py:295-296`). Out of the box, behaviour is unchanged. |
| C6 | **`catalogue_roots: Mapping[str, Path] \| None = None` on the infra functions too, falling back to `cfg`.** | The rule `build_agent` actually follows is *derive from `cfg`, or raise; never invent* — `model=None` becomes `build_model(cfg)` (`agent.py:369`), while `_backend_for` raises because no `cfg`-derived session root exists (`agent.py:225`). Catalogue roots do have a `cfg`-derived answer. A required keyword would break 45 `build_agent` and 25 `build_backend` call sites in `tests/`, and both are public API (`test_architecture.py:158`). |
| C7 | **Supplied roots *replace* the config-derived ones; they do not sit beside them.** | Every existing function assumes one root per kind: `load_tools` takes a directory, `_skill_denials` emits against one route, `confinement.resolve` grants the shell one path. Augmenting reopens all three plus `available_skills`, `SKILLS_SOURCES` and the tool-shadowing check. A deployment wanting both merges them itself — which means answering the collision question, and that is policy kingfisher should not guess at (`uploads.py:117-125` refuses collisions; local-wins and remote-wins are both defensible). Nothing is overwritten; authored files are simply no longer the catalogue. |
| C8 | **`--list` reads the same roots the agent does.** | `main.py:175,180,185` read `cfg` directly and never construct a `Kingfisher`. `test_only_one_module_decides_what_a_skill_is` exists because *"`--list` and `build_agent` must mean the same thing by 'a skill'"*. Under C9 the two cannot diverge today; routing both through one resolution makes that structural rather than accidental. |
| C9 | **Library-only. No environment variable.** | `definitions` has no `KINGFISHER_DEFINITION_STORE` either. Env wiring means resolving a dotted path and importing it at startup — the "import arbitrary Python" surface `tool_store.py:16-22` is careful about, but worse: before any sandbox exists, from a string that lives outside any reviewed directory. |
| C10 | **Derived roots are created; supplied roots must already exist.** Either way the check runs at construction and raises `ConfigError`. | A missing directory is silently empty today (`skill_store.py:28`, `subagent_store.py:27`, `tool_store.py:82`), which is safe only because `ensure_layout` makes them (`layout.py:37-42`). Kingfisher creating its *own* derived directories keeps that true when they are relocated. Creating a *supplied* one would hide a staging failure behind an empty catalogue, which is the opposite of what `service.py:266` promises: *"a broken workspace or an unreachable state directory fails at startup, not on the first turn."* `prepare_scratch` raises `ConfigError` for the structurally identical case. |
| C11 | **The `mkdir` lives in infrastructure**, called from `__init__`. | C4. `build_backend` already did exactly this for one of the three; this makes the special case the general rule. |
| C12 | **`$KINGFISHER_SKILLS` follows the catalogue too.** Found while building, not while designing. | `shell_env` handed the shell `cfg.skills_dir` while the route and the sandbox profile followed the catalogue. A skill's own scripts address their directory through that variable, so a supplied catalogue would have been readable by the file tools, readable by the shell, and named wrongly to the scripts that actually use it. Three answers to one question rather than the two the design set out to collapse. |

## Corrections to the original sketch

Four premises did not survive measurement, and each changed the design.

**"Make the stores classes" was the wrong shape of the right instinct.** The
instinct — that something here should be substitutable — was correct, and the
thing that should be is not any of the three modules. `skill_store`,
`subagent_store` and `tool_store` are untouched by this design.

**A separate managed root was designed, then deleted.** The first version had a
fetched catalogue materialised into a kingfisher-owned directory under
`state_dir`, added as a third entry in `SKILLS_SOURCES` beside `Catalogue` and
`Uploaded`. It was there to avoid clobbering authored files, since
`presets.seed` overwrites and reports doing so — *"Replacing silently is the
part that was wrong"* (`presets.py:122`). Once nothing was being copied, the
concern evaporated. That deleted a third backend route, a third readable root in
the sandbox profile, extra `_skill_denials`, a union in `available_skills`, and
a merge in the tool loader that would have reopened tool shadowing.

**"Materialise at construction" narrowed to "resolve at construction."**
Kingfisher performs no catalogue I/O beyond creating its own derived directories
and checking that all three are there — which C4 shows was never optional.

**The protocol and its adapter were designed in full, then cut.** `CatalogueSource`
was to live in `domain/ports.py` beside `DefinitionStore`, with a `LocalCatalogue`
default and a `PackagedPresets` sibling. It was cut once the consumer turned out
to be hypothetical: the object's whole justification is behaviour — staging,
refresh, cleanup — and all of it is deferred. What remains is a mapping, and the
protocol is an additive change on the day a second source exists.

## What changes

| File | Change |
|---|---|
| `config.py` | `Config.catalogue_roots`, the three directories as one answer |
| `application/service.py` | `catalogue_roots=` argument, resolution at construction, threading |
| `infrastructure/workspace_fs.py` | `resolve_catalogue`: derive and create, or check and refuse |
| `infrastructure/agent.py` | optional `catalogue=` on `build_agent` and the three readers |
| `infrastructure/backend.py` | same, for the `/skills/` route, the sandbox profile and `shell_env` |
| `infrastructure/uploads.py` | the collision check measures against the same catalogue |
| `main.py` | `--list` reads the resolved roots |
| `tests/test_catalogue.py` | new, 10 tests |

Nothing is added to `domain/ports.py`, and no new module is created. Every
signature change is an additive keyword-only argument with a `cfg` fallback, so
no existing call site moved: 556 tests passed before and after, and `build_agent`,
`build_backend` and `shell_env` keep their public shapes.

Two behaviour changes ship, both narrow:

- Relocated `subagents/` and `tools/` directories get created. Only
  `skills_dir` did, so `KINGFISHER_SUBAGENTS_DIR` pointing somewhere that did
  not exist yet yielded an empty catalogue and a clean start.
- Supplied roots that resolve to nothing fail loudly instead of quietly.

Everything else is unchanged unless someone passes `catalogue_roots=`.

Three of the new tests were mutation-checked rather than assumed: reverting the
`/skills/` route, the sandbox grant and `$KINGFISHER_SKILLS` to `cfg` each fails
a test. The sandbox one had to be rewritten to earn that. Staged under `tmp_path`
it passed against both the fixed and the broken code, because the profile is
`(allow default)` with the operator's home denied — so anywhere outside the home
is readable regardless, and the guard only bites for a catalogue inside it, which
is the case `KINGFISHER_SKILLS_DIR` exists for.

Explicitly out of scope: `--seed-presets` and `presets.seed` are untouched.
Under C9 the CLI always uses the config-derived roots, so seed destinations and
catalogue roots are the same paths and seeding cannot fill a directory nothing
reads — the failure `presets.py:60` records `--seed-examples` already having
caused once.

## Still undecided

- **Whether the protocol ever arrives.** It is worth adding the day a second
  source exists and has told us what it needs — lazy per-skill fetching,
  version pinning, per-tenant variation, refresh without restart. Guessing at
  those now is what this revision removed. The migration is additive: accept a
  mapping or an object.
- **Reload.** The roots are resolved once per `Kingfisher`. A long-lived process
  whose catalogue is updated upstream sees the change only if the paths' contents
  change underneath it, which is the caller's business and unstated here.
- **Per-tenant catalogues.** The tenancy note in the session-scoped design
  concluded that per-tenant `Kingfisher` instances in one process are cheap
  (~160 KB each). Different `catalogue_roots` per instance now makes a private
  catalogue per tenant possible. Whether that is a supported story — and what it
  does to prompt-prefix caching, since the skills listing varies with the
  directory — has not been examined.

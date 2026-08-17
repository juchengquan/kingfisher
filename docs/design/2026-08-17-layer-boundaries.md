# Where the layer boundary actually is

**Status:** proposed.
**Date:** 2026-08-17

The proposal was four top-level folders — `application/`, `domain/`,
`infrastructure/`, `presentation/` — with DTOs and registries moved into
`application/`, so the tree follows DDD rather than merely resembling it.

Measured first, because the premise turned out to be mostly true already. Three
of the four folders exist under exactly those names, and the boundary between
them is not a convention: `tests/test_architecture.py` parses every module's
imports and fails the run on a wrong one. A domain module may import the
standard library and itself, nothing else. Application may not import
deepagents. Infrastructure may not reach back into application. The domain may
not touch the filesystem at all — not "may not import langchain", may not call
`mkdir`.

So this document is not about adopting the layering. It is about the two places
where the layering is real but nothing says so, and the one place the proposal
would have made it worse.

## What was measured

**The package root.** Four things sit outside every layer, and I went looking
for the mess there. `config.py` is layer-less deliberately and argues the case
in its own docstring. `__init__.py` is the lazy export table. `prompts/` is
three markdown files the system prompt is assembled from. `presets/` looked
misfiled because it holds Python among the markdown — but that Python is
template code copied *out* into a workspace and never imported by this process,
so the folder is data all the way through. Nothing there is in the wrong place.

**`infrastructure/`, which has 23 modules doing two unrelated jobs.**

| Speaks to the harness (10) | Adapts the disk, the OS, the environment (13) |
|---|---|
| `agent`, `backend`, `checkpointing`, `delegation`, `models`, `runlog`, `runtime`, `scoping`, `skill_registry`, `skills_backend` | `catalogue`, `confinement`, `definitions`, `files`, `layered`, `model_catalogue`, `prompting`, `seeding`, `skill_store`, `subagent_store`, `tool_store`, `uploads`, `workspace_fs` |

Both halves are legitimately infrastructure, so no rule catches the mixture.
But a folder where ten modules would be rewritten if deepagents were replaced
and thirteen would not is a folder that has stopped telling you which is which.

**The line between the halves runs one way, with a single documented
exception.** Six edges cross inward — `agent` → `catalogue`, `layered`,
`prompting`; `backend` → `catalogue`; `delegation` → `prompting`;
`skill_registry` → `skill_store` — which is the legal direction. Exactly one
crosses outward: **`catalogue` → `skill_registry`**.

That one is deliberate and recent. `Catalogue.registry` asks deepagents which
skills it will actually load, because kingfisher's own reading of a skills
directory disagreed with deepagents' and shipped a bug — four directories
advertised, three loaded, two of them different. `skill_registry` exists to end
that disagreement by asking instead of re-implementing, and `catalogue` is where
the question gets asked from.

Worth being exact about what the edge costs. It does not weaken the rule in L3,
which is about *foreign imports* and not about edges between kingfisher's own
modules. What it means is that the swap boundary is a **type** boundary rather
than a module boundary: `catalogue` depends on `read()`'s signature and its
return type, not on deepagents, so replacing the harness rewrites
`skill_registry` and leaves `catalogue` alone. That is the boundary being
claimed, and it survives the edge.

This section was written against a base nine commits older, where the harness
set was nine modules with no outward edge at all. Re-measuring on the real base
is what found `skill_registry`. The claim that changed is recorded rather than
quietly corrected, because "the line is already perfectly clean" was the
argument for the split being free, and it is now the weaker "the line runs one
way apart from one edge that has a reason".

**`_modules_in` does not recurse.** `tests/test_architecture.py:42` collects
modules with `glob("*.py")`. Nine rules feed off it:

| Rule | Layer |
|---|---|
| imports only the standard library and itself | domain |
| touches nothing outside the process | domain |
| reaches the harness only through infrastructure | application |
| does not write to disk itself | application |
| is where foreign types live | infrastructure |
| is the layer doing the touching | infrastructure |
| does not reach back into application | infrastructure |
| does not depend on the eval harness | all three |
| does not import the server | all three |

Every one of them silently stops covering any module that moves into a
subpackage. `_server_modules` in the same file uses `rglob`, so the
inconsistency is eight lines away from the bug.

## Decisions

| # | Decision | Why |
|---|---|---|
| L1 | **The guard learns to recurse before anything moves.** | Nine rules read from one non-recursive `glob`. The first subpackage created under any layer drops its contents out of all nine, and every rule still passes — the failure mode is silence. Today there are no subpackages, so the change is a no-op that must be *mutation-tested* to mean anything: put a `import yaml` in a nested domain module and watch the rule fail. Landing it first is the whole point; landing it alongside the move would leave the move unguarded during review. |
| L2 | **`infrastructure/harness/` holds the ten modules that import deepagents, langchain or langgraph.** | A `git mv` and an import-path update, not a refactor: one edge crosses out of the set and it is `catalogue` → `skill_registry`, which changes path and nothing else. No call site changes meaning and no behaviour moves. What changes is that the folder states the swap boundary — replace the harness and exactly these ten files are rewritten — and a test says so rather than a person remembering it. |
| L3 | **The existence check becomes a containment rule.** | `test_infrastructure_is_where_foreign_types_live` asserts that *somebody* in the layer imports something foreign. It passes as long as one file does, so nothing stops `catalogue.py` growing a `from deepagents import ...` tomorrow — it would be caught by no rule in this file. Replaced by: only `infrastructure/harness/` may import the harness. The existence half stays, scoped to that package, because a `harness/` that imports nothing foreign means the coupling went somewhere less visible. |
| L4 | **The other thirteen stay flat.** | A second subpackage would be tidiness. `harness/` earns a folder because it carries a rule; `storage/` would carry nothing, and the thirteen have no line between them that a test could hold. Splitting for symmetry is how `layered.py` explains *not* giving tools a layer: it would advertise a distinction that does not exist. |
| L4a | **`catalogue` keeps its edge into `harness/`, and the rule permits it.** | The alternative is inverting it — a port in `domain/ports.py` that `skill_registry` satisfies and `catalogue` is handed. That would be the textbook move and it would be wrong here: the port would have exactly one implementation, forever, whose entire purpose is to be deepagents-specific. `SkillRepository` is a port because two things already answer it; this would be a port because a diagram wanted one. Left as a direct import, with the rule scoped to foreign packages so it does not have to lie about the edge. |
| L5 | **`server/` is renamed to `presentation/`.** | The only change here that buys a name rather than a rule, and it should be judged that way. 64 references across 17 files, most of them tests and docs. Against: `uvicorn kingfisher.server.asgi:app` is the sort of string that ends up in a systemd unit, and breaking it costs a real operator a real minute. For: a top level that reads as one vocabulary instead of three layers and a thing. At 0.1.0 with one operator the cost is close to zero, so the name wins. |
| L6 | **The console script stays `kingfisher-server`.** | `kingfisher-presentation` is a layer name pointed at a person. The folder is named for where it sits in the dependency graph; the command is named for what it starts. Those are different audiences and there is no reason they should match. |
| L7 | **Registries do not move to `application/`.** | The things that look like registries here — `tool_store`, `skill_store`, `subagent_store`, `catalogue`, `layered`, `model_catalogue`, `definitions` — are adapters implementing Protocols declared in `domain/ports.py`, and two of them parse YAML. In `application/` they would put file reading and YAML parsing in the layer that orchestrates, which `test_the_application_layer_does_not_write_to_disk_itself` exists to prevent and which that test's own docstring records going wrong once already. |
| L8 | **DTOs do not move to `application/`.** | The wire shapes are `server/payloads.py` and `server/capabilities.py`, and they exist so an HTTP body can change without the domain changing. In `application/` the orchestration layer learns what HTTP is, and the one type that genuinely is an application-level command — `Request` — already exists in `domain/`, described there as "what a caller asks for, with no knowledge of how kingfisher is wired". There is no third thing left for an application DTO to be. |
| L9 | **The package root does not change.** | Measured above. Moving `config.py` into a layer re-creates the problem its docstring describes solving; moving `prompts/` or `presets/` moves data into a code layer. |

## What the move costs at the edges

Six entries in `_EXPORTS` point at harness modules and change path:
`async_checkpointer`, `build_agent`, `build_backend`, `build_checkpointer`,
`build_model`, `shell_env`. **No public name changes** — the lazy export table
absorbs it, which is the thing it is for. `system_prompt` is the one light
export in the affected neighbourhood and it lives in `prompting.py`, which
stays flat, so `test_a_light_export_stays_light` is untouched.

`application/service.py` imports three harness modules by name (`agent`,
`checkpointing`, `runlog`) and four non-harness ones. Only the three change.

## Phases

Each lands on its own, and each is verified by mutating the rule it adds
rather than by the suite going green.

| # | What | Verification |
|---|---|---|
| 1 | `_modules_in` recurses. | A nested domain module importing `yaml` must fail the rule. Confirm all nine rules still pass unchanged — with no subpackages yet, this is a no-op by construction. |
| 2+3 | `infrastructure/harness/`, the ten moved, imports updated, `_EXPORTS` repointed, and the containment rule that replaces the existence check. | Import graph re-measured: `catalogue` → `skill_registry` is the only edge out of the set, and no new one appeared. Eight mutations. The shipped server binary booted and served, and the CLI listed a workspace. Full suite, `ruff`, `ty`. |
| 4 | `server/` → `presentation/`. | `uvicorn kingfisher.presentation.asgi:app` started for real and a turn run through it. The entry point has been unrunnable before while the whole suite passed — nothing in `tests/` starts the shipped command, so nothing in `tests/` can find this. |

## Two things the plan got wrong, found while building it

**Phases 2 and 3 could not be separated.** The `harness/__init__.py` docstring
has to say what keeps the boundary true, and splitting the phases meant landing
a docstring that named a test which would not exist for another PR. A boundary
claimed and unenforced, for however long the second review took. Phase 1's own
argument settles it the other way round: the guard goes in *before* the move it
watches, and it did.

**L3 was too narrow to be correct.** Written as "only `infrastructure/harness/`
may import the harness", which assumes the rule knows what the harness is. It
did not. `FOREIGN` named five packages; the agent runtime accounts for eight
here, and `langchain_quickjs`, `aiosqlite` and `langchain_openai` were in none
of them — an `application/` module importing any of the three passed, as would
one importing `yaml`, or anything else nobody had thought of.

That is the same defect
`test_domain_imports_only_the_standard_library_and_itself` was rewritten to
escape, still present in the two rules that had not been turned around yet. So
the shipped rule is a table: every area's third-party surface written down, and
anything undeclared refused wherever it appears. It subsumes both old rules,
which are gone.

The table also decides what happens when a new directory appears under
`src/kingfisher/`: `_area_of` finds no entry, falls back to the package root,
and the root may import nothing third-party. A new area therefore starts denied
and someone has to write down what it needs — which is the behaviour phase 4
will meet when `server/` becomes `presentation/`.

## Not in scope

**`application/service.py`.** It is 1068 lines and 24 methods on one class, and it is
simultaneously the composition root, session management, the turn lifecycle and
the streaming loop. That is the largest structural problem in the repository and
it would improve things more than phases 2 to 4 together. It is left out because
it is a file split rather than a folder move, and folding it in here would mean
a folder-shaped review reading a behaviour-shaped diff.

**Skills, subagents and tools as first-class packages.** Where a definition is
*read from* is settled; where the *rules* about it live was settled in
[tool rules in the domain](2026-08-17-tool-rules-in-the-domain.md). Neither is
reopened.

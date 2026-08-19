# A subagent brings its own — Implementation Plan

**Goal:** Let a subagent hold tools and skills that belong to it alone, stored
beside its definition rather than in the shared catalogue. Granting the delegate
grants its parts; nothing else can reach them.

**Why this is not tidiness.** An agent gets *every* tool by default —
`read.selection(document.get("tools"), absent=ALL)`. So today there is no way to
have a tool the top-level agent cannot call. A bundle is that way: it is the one
place a capability can sit that the parent does not hold. A delegate can be
trusted with something its caller is not.

## What was decided, and what each rules out

1. **The grant travels with the delegate.** A request activating `reviewer`
   thereby holds reviewer's own tools and skills, without naming them. This
   refines rather than breaks `delegation`'s rule that "a delegate cannot reach
   past the request that summoned it": the request summoned the delegate, and a
   delegate is made of parts.
2. **A bundle belongs to one subagent, not to a folder.** A folder under
   `subagents/` is a bundle when it holds a definition whose `name:` matches the
   folder. Per-folder privacy would be proximity rather than privacy — two
   delegates in `analysis/` would share everything in it.
3. **Private assets sit outside request narrowing.** They ride on the subagent
   grant alone. `--without-subagents reviewer` declines the lot; there is no
   finer lever, and that is the point.
4. **The bundle wins a name collision.** A name is looked up in the bundle
   first, the catalogue second. The alternative — refusing the collision — means
   adding a `fetch` to the shared catalogue breaks an unrelated delegate that
   has had its own `fetch` for months, which is the coupling a bundle exists to
   remove.
5. **Subagents only.** Agents already reach the whole shared catalogue, so a
   bundle would be a second way to say what they have.
6. **A bundle is always held by its owner**, tools and skills both. Omitting
   `tools:` gets the parent's inherited set *plus* the bundle. The file being in
   the folder is the declaration; naming it again would be a second place to
   keep in step.
7. **A broken private tool fails at startup**, like any other tool — `list`
   exits 1. A broken private skill is reported and never fatal. This is the
   existing rule, measured in `e3c6d61`: broken tool and subagent exit 1, broken
   skill exits 0 because a run works without it.

## What "private" honestly buys, per kind

**Tools: real.** An ungranted tool is not bound into the agent, so it cannot be
called.

**Skills: advertisement only.** `test_skills_read_only` states it — "the point
is read-only, not unreachable". Anything holding `read_file` can open anything
under the skills mount. A private skill is not in another delegate's listing or
prompt; it is not beyond its reach. This is already true of every unactivated
skill, so it is not a regression — but a bundle is not a vault, and the docs
must say so rather than let a reader assume otherwise.

## The layout

```
subagents/
  reviewer.yaml                    a plain definition, no bundle
  analysis/                        a grouping folder, unchanged
    profiler.yaml
  surveyor/                        a bundle: folder name == definition name
    surveyor.yaml
    tools/
      probe.py                     TOOLS = [...], as in the shared catalogue
    skills/
      sampling/SKILL.md
```

**Refused, because there is no good answer:** a folder matching one definition
that also holds others — `surveyor/surveyor.yaml` beside `surveyor/helper.yaml`.
Is `helper` inside the bundle or beside it? The loader refuses the pair and says
so, which is what this codebase does with an ambiguity everywhere else.

**Not refused:** a folder matching nothing, which is a grouping folder and stays
exactly what it is today.

---

### Task 1: Find bundles

**Files:** `src/kingfisher/infrastructure/catalogue/subagents.py`, and a new
`Bundle` record beside `LocalSubagentRepository`.

`_definitions_in` already recurses at any depth and already tolerates a folder
that is a Python package. What is missing is the *claim*: which folder is whose.

- [ ] A `bundles` cached property on `LocalSubagentRepository`, answered from
  the same single walk as `specs` and `sources` — the class exists because
  `load_all` and `sources` each walked the tree, and a third walk would undo
  that.
- [ ] Refuse a bundle folder holding more than one definition, naming both files.
- [ ] Report — not refuse — a folder holding `tools/` or `skills/` that matches
  no definition. It is a grouping folder with directories in it, which is legal;
  but nine times in ten it is a bundle whose definition was renamed, and that is
  the `misfiled` shape the skill registry already reports for the same reason.

### Task 2: Load a bundle's tools at startup

**Files:** `catalogue/tools.py`, `catalogue/__init__.py`

`LocalToolRepository` takes one root and imports it. A bundle needs the same
treatment against a different root, and the results must stay *labelled* so
nothing merges them into the shared offering by accident.

- [ ] A repository per bundle, built from `<bundle>/tools/`, reusing
  `LocalToolRepository` rather than a second loader.
- [ ] `Definitions.warm()` touches them, so a broken private tool fails where
  every other broken definition already fails. Its docstring says why: warming
  moves the error "from the first turn to startup".
- [ ] `Offering.of(...)` is **not** given bundle tools. Their absence from the
  offering is what makes them unreachable by name from a request, and it is what
  makes the whole feature true rather than enforced.

### Task 3: Hand a delegate its own

**Files:** `infrastructure/harness/agent.py`, `infrastructure/harness/delegation.py`

The build site already passes `tool_objects` and `skills` per delegate, so this
is an addition at one place rather than a new path.

- [ ] `tool_objects` for a delegate with a bundle is *bundle first, then the
  granted catalogue tools*, with a catalogue tool dropped when the bundle
  defines that name. One candidate per name, so `duplicated` still holds and
  nothing is silently replaced — the resolution is stated before the lookup.
- [ ] `subagent_tools` / `subagent_skills` keep narrowing the *catalogue* half
  by what the request activated, and do not narrow the bundle half at all.
  Decision 3 lives here, in two functions, and each needs the reason written
  beside it.
- [ ] `refuse_unoffered` for a delegate consults the bundle as well as
  `offered`, or a definition naming its own skill is refused as unknown.

### Task 4: Mount a bundle's skills

**Files:** `infrastructure/harness/backend.py`

The mechanism exists: `UPLOADED_SKILLS_ROUTE = "/skills/uploaded/"` is a second
physical location mounted into the one logical tree, winning by longest prefix.
A bundle's skills follow that pattern exactly.

- [ ] Route `/skills/subagents/<name>/` per bundle, mounted from
  `<bundle>/skills/`.
- [ ] **It must sit under `/skills/`**, not beside it. Two enforcement points
  make the catalogue read-only — the `SKILLS_ARE_READ_ONLY` tool permission and
  the sandbox profile — and both are scoped to that prefix. A route outside it
  would be a writable skills mount, which is the exact hole
  `tests/test_skills_read_only.py` was written after finding.
- [ ] `subagents` becomes a reserved folder name under the skills root, handled
  the way `uploaded` already is at `backend.py:327`. Prefer refusing it over
  skipping it: `uploaded` is skipped, and a skills folder silently not offered
  is the failure this codebase keeps naming.
- [ ] The bundle's source is added to *its owner's* `skill_sources` and to
  nobody else's. Mounting is not advertising — the parent's sources come from
  `roots.registry.folders` and must not learn about bundles.

### Task 5: Say what is there

**Files:** `application/inventory.py`, `presentation/cli/listing.py`

A capability the parent cannot call is exactly the thing an operator must be
able to see.

- [ ] `--list` prints a bundle's tools and skills indented under the subagent
  that owns them, with a note that they are private.
- [ ] A shadowed catalogue name is printed as shadowed. Decision 4 is only
  acceptable because it is visible.
- [ ] `failed` counts a broken bundle tool, so `list` exits non-zero. The rule
  is now "any of them" rather than a list of kinds, so this should need nothing
  — assert it rather than assume it, since that predicate has been wrong once.

### Task 6: Refuse an uploaded bundle

**Files:** `infrastructure/uploads.py`

A bundle holds Python. `NOT_UPLOADABLE` already states the rule: tools are
"code, imported into this process -- never caller-supplied".

- [ ] An uploaded subagent never gets a bundle, and a caller cannot create one.
- [ ] `test_kind_vocabulary` is where this belongs as a rule rather than a
  comment, since that file exists to stop a kind going undecided.

### Task 7: Ship one, and document it

- [ ] A bundle among the shipped assets, so `kingfisher seed` produces a working
  example. `seeding` copies whole directories, so this should need no change —
  verify rather than assume, and check `STAGED_KINDS` still does the right thing.
- [ ] `docs/formats.md` gains a bundle section, including the honest limit: a
  private skill is not advertised, not unreadable.
- [ ] The `catalogue/__init__.py` docstring says "the three kinds are the point
  of it" and there are now four. Unrelated to this work and worth fixing while
  the file is open — the prose rule cannot catch it, because it reads module
  names and not counts.

## Verification

- `uv run ruff check src/ main.py tests/ service/`, `uv run ty check`, bare
  `uv run pytest -q`.
- `kingfisher list` against a seeded workspace, and against a workspace with a
  deliberately broken bundle tool — exit 1, and the other three sections still
  printed. That last is the bug `e3c6d61` fixed; a new kind of tool is exactly
  how it would come back.
- Mutations worth running, because each guard here is written against a tree
  that already satisfies it: a bundle folder holding two definitions; a bundle
  tool shadowing a catalogue name; a private skill named by a *different*
  delegate, which must be refused as unknown.

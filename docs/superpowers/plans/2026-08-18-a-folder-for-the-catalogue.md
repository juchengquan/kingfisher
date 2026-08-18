# A folder for the catalogue — Implementation Plan

**Goal:** Make the three definition kinds legible at the top of the
infrastructure layer. `infrastructure/` holds fourteen flat modules, seven of
which are one subject — where a definition comes from, and what it is once read
— spread across names (`skill_store`, `subagent_store`, `tool_store`,
`definitions`, `layered`, `importing`, `catalogue`) that never say so. After
this, `ls infrastructure/` reads as the agent runtime, the catalogue, and the
host; and `ls infrastructure/catalogue/` reads as the three kinds.

**Architecture:** `infrastructure/catalogue.py` becomes
`infrastructure/catalogue/__init__.py`, and six siblings move in beside it. The
package keeps the module name `kingfisher.infrastructure.catalogue`, so every
`Definitions` and `resolve_definitions` import site — eight in `src/`, about
eighteen in tests — is untouched. The subject already has a narrow front door:
outside the cluster, `definitions` and `importing` have no consumers at all,
`layered` has one, and the three stores are reached only for `ToolError`,
`SKILL_LAYOUT` and one function.

**Shipped in two pieces, each branched fresh off `main`.** The prose check is
independent of the move and lands first, so the check is green *before* nine
comments go stale — the check tells you, rather than you telling the check. Not
stacked: both touch `catalogue.py`, and a stacked pair does not survive a
rebase-merge here.

## Global constraints

- What CI runs, not a narrower version: `uv run ruff check src/ main.py tests/
  service/`, `uv run ty check`, and bare `uv run pytest -q`. Bare, because
  `pytest tests/` does not collect `assets/` — which is where the last move of
  this kind actually broke.
- Never `ruff format`. Confirm ruff by its summary line (`^Found` / `^All
  checks`), not by the trailing note.
- Every guard added here is mutation-tested in both directions. A rule written
  against a tree that already satisfies it proves nothing by passing.

## What is deliberately not in scope

- **`harness/skill_registry.py` stays where it is.** It can move — `THIRD_PARTY`
  is a table meant to be edited — but the ledger is against it. Its `offered`
  mapping carries deepagents' own skill objects (`agent.py` iterates them,
  `describe` reads `["description"]` off one), so it is a foreign type in
  substance and belongs where foreign types may be named. And the move would
  trade two watched edges (`catalogue`, `uploads`) for one watched
  (`skills_backend`) plus two unwatched (`scoping`, `agent` reaching back out),
  while making `harness/__init__.py`'s ten-file swap-boundary claim false.
- **`uploads.py` and `seeding.py` stay flat.** Uploads is request-scoped and
  exports to `service.py`; seeding writes definitions rather than reads them and
  is public API.
- **`docs/design/` is history and is not rewritten.**

## Known-stale prose found while planning, not fixed here

- `seeding.py`'s docstring describes definitions arriving as installed packs
  found through an entry point. The code reads one constant,
  `ASSETS = "kingfisher.assets"`.
- `harness/__init__.py` says "one edge crosses out of here" and names
  `catalogue`. `HARNESS_EDGES` names four consumers.
- `service/tests/test_capabilities_on_the_wire.py:28` re-derives `AXES` locally
  while its sibling imports the real one — same name, same value, two files, in
  the distribution the deduplication rule does not read.

---

# Piece 1 — Comments that name a module name one that exists  ✅ done

**What the plan expected:** two wrong comments in `config.py`, and a rule that
checks a reference resolves to a module.

**What it found:** ten, and the rule had to be stronger to see them.

Planning measured against a set of module *names*, which accepts any reference
whose parent resolves. `infrastructure` is a package, so every
`infrastructure.<gone module>` passed as a package plus an attribute nobody
looked for — and that is the shape of six of the ten. Checking the trailing
segment against the target module's top-level names closes it, and still admits
the prose worth writing: `domain.skill.split` and
`infrastructure.harness.backend.prepare_scratch` both name real functions and
both resolve.

Six of the ten were left by one move, `infrastructure/harness/`, which renamed
four modules that comments went on naming at the old path:

| where | said | is |
|---|---|---|
| `main.py:182` | `infrastructure.inventory` | `application.inventory` |
| `config.py:22` | `infrastructure.models` | `infrastructure.harness.models` |
| `config.py:474` | `infrastructure.backend.prepare_scratch` | `infrastructure.harness.backend.…` |
| `config.py:515` | `workspace_fs.resolve_definitions` | `infrastructure.catalogue.…` |
| `domain/subagent.py:133` | `infrastructure.agent` | `infrastructure.harness.agent` |
| `domain/subagent.py:239` | `infrastructure.models` | `infrastructure.harness.models` |
| `harness/delegation.py:419` | `infrastructure.models` | `infrastructure.harness.models` |
| `test_architecture.py:784` | `infrastructure.models` | `infrastructure.harness.models` |
| `test_capabilities.py:112` | `infrastructure.delegation` | `infrastructure.harness.delegation` |
| `test_subagent.py:391` | `infrastructure.delegation` | `infrastructure.harness.delegation` |

`config.py:515` is the one the rule does not catch: written as a bare module
name, which is the half that cannot be checked. Fixed anyway, in the long form,
so it is checked from now on.

**`PROSE_GONE`, which the plan did not anticipate.** `tests/test_architecture.py`
names gone modules on purpose — in the docstring of the rule a rename broke, and
in the negatives asserted gone rather than merely absent. Keyed by file rather
than by name, because `infrastructure.agent` appears twice in this repository:
once as that deliberate mention, once in `domain/subagent.py` as a live pointer.
One is the rule working and the other is the defect it catches, and a table keyed
by name alone would have to excuse both.

**Mutations, all three caught** (the first attempt at the first one was a no-op —
`{} or {...}` evaluates to the second dict — and had to be redone against the
lookup instead):

- excuse table never consulted → fails, naming the file's deliberate mentions
- attribute check removed, parent is enough → discrimination test fails
- layer rooting dropped → both fail, on `models.yaml` and friends

The rule also failed on ten real defects before any of them were fixed, which is
better evidence than a synthetic mutation: the defects predate the rule.

**Verified:** `ruff check src/ main.py tests/ service/` → All checks passed;
`ty check` → All checks passed; bare `pytest -q` → 1498 passed.

# Piece 2 — The move

Branch fresh off `main` after Piece 1 lands.

### Task 2.1: Move seven files

**Files:** `git mv` only — no content changes in this step, so the diff is a
rename and the rules fail for reasons that are about the move.

| from | to | holds |
|---|---|---|
| `catalogue.py` | `catalogue/__init__.py` | `Definitions`, `resolve_definitions`, `source_of`, `catalogue_root`, `DEFINITION_KINDS` |
| `skill_store.py` | `catalogue/skills.py` | `LocalSkillRepository`, `SKILL_LAYOUT`, `reachable`, `DEEPEST` |
| `subagent_store.py` | `catalogue/subagents.py` | `LocalSubagentRepository`, `NEAR_MISS` |
| `tool_store.py` | `catalogue/tools.py` | `LocalToolRepository`, `ToolError`, `EXPORT` |
| `layered.py` | `catalogue/layered.py` | `LayeredSkills`, `LayeredSubagents`, `for_session` |
| `definitions.py` | `catalogue/documents.py` | `decode`, `read_subagent`, `skill_name`, `LITERAL` |
| `importing.py` | `catalogue/importing.py` | `load`, `modules_in`, `skipped`, `LoadError` |

`definitions.py` has to be renamed whatever else happens: it would land next
door to an `__init__.py` exporting a type called `Definitions`, meaning something
unrelated. `documents.py` comes from its own first line — reading a definition
document into the value the domain works with.

`layered.py` keeps its name; `session.py` would collide with `domain/session.py`,
which is the collision the house rule is actually about.

- [ ] **Step 1:** `git mv` the seven, create `catalogue/__init__.py` from
  `catalogue.py`, and update the intra-package imports.
- [ ] **Step 2:** Move `Definitions` in `catalogue/layered.py` under
  `TYPE_CHECKING`. It is annotation-only there, and leaving it at module scope
  makes `layered` import the package that would one day import `layered`. Doing
  it now removes the cycle before it can exist.
- [ ] **Step 3:** Update the import sites outside the package. Only five modules
  change, because the package name did not:
  - `application/inventory.py` — `tool_store` → `catalogue.tools` (`ToolError`)
  - `infrastructure/uploads.py` — `definitions` → `catalogue.documents`
  - `harness/agent.py` — `layered` → `catalogue.layered` (`for_session`)
  - `harness/skill_registry.py` — `skill_store` → `catalogue.skills` (`reachable`)
  - `kingfisher/__init__.py` — `_EXPORTS["SKILL_LAYOUT"]` and its
    `TYPE_CHECKING` import → `kingfisher.infrastructure.catalogue.skills`
- [ ] **Step 4:** Update the test imports — roughly eleven files naming
  `skill_store`, ten `subagent_store`, eight `tool_store`, ten
  `infrastructure.definitions`, one `layered`.

### Task 2.2: The rules that refuse, one at a time

Run `uv run pytest -q` after the move and fix what it names. Four refuse loudly;
that is them working.

- [ ] **`HARNESS_EDGES` — key by relative module name.** The table is keyed by
  `path.stem`, and `catalogue/__init__.py`'s stem is `__init__`, so the
  catalogue's deepagents edge reads as an unnamed escape. Replace the keying
  with the module's name relative to its layer — `catalogue` for a package's
  `__init__.py`, `catalogue.skills` for a submodule, `uploads` and `service`
  unchanged. All four existing entries stay byte-identical: the move does not
  get to rewrite the reasons written beside them. It also ends a latent
  ambiguity, since two modules with the same stem in different layers currently
  share one key.

```python
def _consumer_key(path: Path) -> str:
    """A module's name below its layer, so a package and its modules differ.

    Was `path.stem`, which answers `__init__` for a package -- silently
    unkeying the entry whose reason is written beside it -- and which cannot
    tell `application/uploads.py` from `infrastructure/uploads.py`.
    """
    parts = list(path.relative_to(SRC).parts[1:])
    parts = parts[:-1] if parts[-1] == "__init__.py" else [*parts[:-1], parts[-1][:-3]]
    return ".".join(parts)
```

- [ ] **`test_only_one_module_decides_what_a_skill_is`** — `owners` holds
  `infrastructure/skill_store.py`; point it at `catalogue/skills.py`. Fails
  loudly, because the real file at the new path becomes an offender.
- [ ] **`test_the_base_stands_alone`** — raises on the stale
  `_EXPORTS["SKILL_LAYOUT"]` path. Fixed in Task 2.1 Step 3; confirm it is what
  went green.
- [ ] **The dangling-import rule** — catches any import missed across `src/`,
  `tests/`, `service/`, `assets/`, `main.py` and `evals/`.

And one that passes while pointing at nothing, so it must be found by reading:

- [ ] **`test_area_of`** asserts `_area_of(SRC / "infrastructure" /
  "catalogue.py") == "infrastructure"`. `_area_of` computes from the path string
  and never touches disk, so it keeps passing about a file that no longer
  exists. Point it at `catalogue/__init__.py` and add the case the folder makes
  available — that a module under `catalogue/` is judged as `infrastructure`,
  not as its own area, since `THIRD_PARTY` has no entry for it and longest-prefix
  falls back.

`THIRD_PARTY` needs no new entry. `infrastructure/catalogue` inherits
`infrastructure`'s `{yaml}`, which is what `documents.py` needs and all it needs.

### Task 2.3: Derive the kinds, and bind the modules to them

Today the three kinds are written down twice: `DEFINITION_KINDS = ("skills",
"subagents", "tools")` and the three fields of `Definitions`, six lines apart,
with nothing binding them. The folder makes it three — `skills.py`,
`subagents.py`, `tools.py` — and the third is the one nobody would think to
check. This is the defect the last two commits were about.

- [ ] **Step 1:** Derive the constant, below the class:

```python
#: The kinds, from the type that has one field per kind rather than beside it.
#: `AXES` already does this one layer down. Field order is now load-bearing:
#: `seeding` iterates this to decide what to copy and in what order, so
#: reordering `Definitions` is a change to seeding rather than a cosmetic edit.
DEFINITION_KINDS: tuple[str, ...] = tuple(f.name for f in fields(Definitions))
```

- [ ] **Step 2:** Bind the module names to it, in `tests/test_architecture.py`:

```python
def test_the_catalogue_holds_one_module_per_kind():
    """The folder is what makes this a checkable claim at all.

    Flat among thirteen other modules, "one module per kind" was not a shape
    anything could assert. It is now, and the module names are the third place
    the three kinds are written down -- the one with no type and no constant
    behind it.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    modules = {p.stem for p in (SRC / "infrastructure" / "catalogue").glob("*.py")}

    assert set(DEFINITION_KINDS) <= modules, (
        f"{sorted(set(DEFINITION_KINDS) - modules)} is a kind the catalogue "
        "reads with no module named for it"
    )
```

- [ ] **Step 3: Mutation-test the guard both ways.** Rename `tools.py` and
  confirm the rule names `tools`. Add a fourth field to `Definitions` and
  confirm the rule names it rather than passing. Restore both.

### Task 2.4: The nine comments the move invalidates

**Files:** `domain/tool.py`, `domain/subagent.py`, `domain/skill.py`,
`domain/fields.py`, `harness/delegation.py`, and the moved modules' own
docstrings.

The convention Piece 1 established: a comment naming a module in another layer
or package writes the layer-rooted form; bare names stay fine among siblings,
which move together. Cross-layer is where prose goes stale, because the two
files move independently — which is what both of `config.py`'s wrong comments
were.

- [ ] Rewrite each cross-layer reference into the long form —
  `` `tool_store` `` in `domain/tool.py` becomes
  `` `infrastructure.catalogue.tools` `` — so it lands under the Piece 1 check.
- [ ] Leave within-package references bare. `catalogue/subagents.py` naming
  `documents` is fine; they move together.
- [ ] Update the moved modules' own docstrings where they describe their old
  position — `skill_store` calling itself "the mirror of `subagent_store`",
  `tool_store` calling itself "the third of the store trio", `documents.py`
  explaining it is named `definitions` rather than `fields`. That last one now
  has a better reason to give, and should give it.
- [ ] Add a `catalogue/__init__.py` paragraph saying what the folder is and what
  it deliberately does not hold — the `skill_registry` ledger above, in one
  paragraph, so the next person to ask gets the answer rather than the question.

### Task 2.5: Verify

- [ ] `uv run ruff check src/ main.py tests/ service/` — confirm by the summary
  line, not the trailing note.
- [ ] `uv run ty check`
- [ ] `uv run pytest -q` — bare, so `assets/` and `service/` are collected.
- [ ] `uv run kingfisher list` and `uv run kingfisher --help` from the installed
  console script. A move is what breaks an entry point in a way no test notices.
- [ ] Commit.

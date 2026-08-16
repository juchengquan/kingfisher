# Nested discovery for tools and subagents

**Status:** implemented.
**Date:** 2026-08-16

Three kinds of definition, three discovery rules, and until now the same
answer to "can I put these in folders": no. That is right for one of them and
was never examined for the other two.

- **Skills** — `skills/<name>/SKILL.md`, exactly one level.
- **Tools** — every `.py` directly in `tools/`, skipping `_`-prefixed ones.
- **Subagents** — every `.yaml` directly in `subagents/`.

Tools are the directory that wants subfolders, because it is the only one that
is a pile of loose files. A skill is already a folder.

## Who reads the files is the whole answer

The rules differ because the readers differ, and that is not incidental.

**Skills are read by deepagents, off the filesystem, one level down.** Measured
against the installed library rather than taken from our own docstring: it lists
the skills directory once, treats every immediate subdirectory as a candidate,
and looks for `SKILL.md` directly inside it. It never recurses. So
`skills/research/company-lookup/SKILL.md` is not hidden -- it is invisible, and
nothing errors. That is why `skill_store.misplaced` exists.

**Tools and subagents are read by us.** A tool module is imported into this
process; a subagent document is parsed here. Neither directory is a backend
route, and the agent is given no path that reaches either. Nothing outside
kingfisher has an opinion about how deep they sit.

## Names do not come from paths

For tools and subagents the file layout is *already* not the namespace:

- a tool is named by its own `name` attribute or its function; `find_company.py`
  may define a tool called `lookup`, and that is what a request grants
- a subagent is named by the `name:` field, and `subagent_store` says outright
  that the filename is not authoritative

Only a skill ties identity to its folder, because deepagents requires the two to
match. So nesting tools and subagents cannot affect a name, which is what makes
it cheap.

## What the model sees, and what it does not

Measured on the shipped presets:

| | in the prompt, every turn | loaded only if chosen |
|---|---|---|
| a tool | 60–110 tokens (the full schema) | — |
| a skill | ~60 tokens (name + description) | ~370 tokens (the body) |

The model never sees a folder. Tools arrive as a flat list of names and schemas,
subagents as a flat list on the `task` tool, skills as a flat markdown list.
Nesting `tools/research/find_company.py` leaves the model's view byte-identical.

Two consequences, and they point opposite ways. Nesting is safe -- there is
nothing for the model to get lost in. And nesting is *not* a remedy for a large
catalogue: fifty tools is ~5,000 tokens of schema in every prompt whether they
sit in one directory or twenty, because the grouping never reaches the model.
The levers for that already exist and are per-request narrowing and delegation.

## Dispatch was never the problem

Once the model names a tool, the graph looks that name up in a dictionary of
tool objects and calls it. The object carries its own function; the file it came
from is gone by then. There is no step that pins a call to a path, so there is
none to get wrong. Cross-folder duplicates are caught by the check that already
exists -- verified with the same tool name in two folders: refused, naming the
file that claimed it first.

## Loading was the problem

The case worth building for is the one that fails today. A self-contained
nested file imports fine. A nested file written as a *package* does not:

```
find_company.py: ModuleNotFoundError: No module named 'kingfisher_workspace_tools'
```

Each file is imported as a standalone module under a synthetic name, never as
part of a package, so a relative import has no parent to resolve against. And
under a recursive scan the helper it was importing would itself be scanned and
required to declare `TOOLS`.

Which is the whole point: a tool grows helpers, that is *why* it wants a folder,
and that is the case that breaks.

## Decisions

| # | Decision | Why |
|---|---|---|
| N1 | **Tools and subagents nest; skills stay flat.** | Not a consistency failure but the readers differing. Deepagents looks one level down and nothing we do to our own scan changes that. |
| N2 | **Names stay flat. A folder never enters a name.** | A name is what a caller types -- grants, `--tools`, `--without-tools`, and the `tools:` field inside a subagent definition. Prefixing means moving a file silently breaks all of them. It also makes the name *inferred* from where a file sits, when `tool_store` deliberately has a module *declare* its exports so a name cannot be decided by accident. |
| N3 | **A plain folder is organisation; a folder with `__init__.py` is one package, and the scan stops there.** | Reads the way Python already reads. The package states its exports in `__init__.py`, helpers are ordinary modules, relative imports work, and nothing needs an `_` prefix to hide. Walking *into* a package would scan its helpers as tool files, which is the problem being solved. |
| N4 | **Unlimited depth for plain folders.** | No reason to cap it once names are flat and the scan is cheap. |
| N5 | **Skip hidden directories and `__pycache__` while walking.** | A one-level scan could never reach a stray `.venv` or a build directory under `tools/`; a recursive one can, and this layer *imports what it finds*. The guard is new because the exposure is new. |
| N6 | **Multi-source nesting for skills was measured and rejected.** | Deepagents does take several sources, but the labels appear only in a separate "where skills live" block -- the skills list itself stays flat and ungrouped, so it buys nothing for comprehension. Worse, later sources silently override earlier ones on a name clash, which is the failure every other check here refuses. It would also cost a backend route and deny rules per folder. |
| N7 | **`misplaced` says why, not just what.** | Once tools nest freely, "skills are one level deep" reads as an arbitrary inconsistency to someone who nested their tools an hour earlier. The message should say the agent reads skills off the filesystem itself and only looks one level down. |
| N8 | **Fix the preset seeder's bytecode filter while making packages possible.** | It skips `__pycache__` only at the top level and then `copytree`s directories wholesale. Safe today only because a preset tool is always a single file. The moment one can be a package, one test run leaves bytecode inside it and seeding copies that into the operator's workspace -- exactly what the comment there says it exists to prevent. The *reporting* half of the same function already filters at any depth; only the copy does not. |
| N9 | **`--list` shows where a nested definition lives.** | The only reason to nest is so a person can find things. A flat list of names makes them grep anyway. |
| N10 | **Uploads are unaffected.** | An uploaded subagent is one file by rule, an uploaded skill already carries its own files inside one folder, and there is no upload path for tools. Scanning the session's subagent directory recursively is harmless, since uploads write flat into it. |

## The package import, proven

Not plausible -- run:

```python
spec = importlib.util.spec_from_file_location(
    unique_name, directory / "__init__.py",
    submodule_search_locations=[str(directory)],
)
```

Relative imports resolve, `TOOLS` comes from `__init__.py`, and both tools in
the package invoke correctly.

The isolation the flat loader was built for survives: two catalogues each with
their own `research/` package load side by side, each resolving its own helper,
and neither directory goes on `sys.path`. The existing path-hash naming is what
keeps them apart.

**A synthetic parent package was written for this and then deleted.** The design
said one had to be registered in `sys.modules` so the dotted name would resolve,
which is true of a *loose file* doing a relative import -- that was the original
failure -- and not true of a package. A package resolves `.client` against
itself, and it is already in `sys.modules` before its body runs, so the
grandparent is never consulted. Found by mutating it out and watching every test
still pass: the code was doing nothing while carrying a docstring saying it was
load-bearing, which is the same "unused but reassuring" shape as the dead tool
loader removed a day earlier.

What replaced it is worth more. A loose file *cannot* use a relative import --
it has no parent package, and it never will -- so the failure now says the thing
to do instead of leaking an internal module name:

    thing.py: a relative import needs a package, and this file is loaded on its
    own. Add __init__.py to sub/ and declare TOOLS there -- then its modules
    import from each other normally.

## What changes

| File | Change |
|---|---|
| `infrastructure/tool_store.py` | recursive walk; package-as-leaf import; hidden and `__pycache__` skips; `sources()`; advice on a loose relative import |
| `infrastructure/subagent_store.py` | recursive walk; `sources()`; a duplicate names both files |
| `infrastructure/skill_store.py` | `misplaced` explains why skills cannot nest |
| `infrastructure/presets.py` | filter `__pycache__` at any depth when copying |
| `main.py` | `--list` says where a definition came from when its name does not |
| `presets/tools/csv_profile/` | new: a tool package with a shared helper |
| `presets/subagents/analysis/profiler.yaml` | new: a nested subagent |
| `tests/test_nested_discovery.py` | new, 17 tests |

Nothing changes for a flat catalogue, which is every existing one: 652 tests
passed before the new ones were added and after.

Four mechanisms were mutation-checked rather than assumed -- descending into a
package, the debris guard, the recursive subagent walk, and the seeder's
bytecode filter each fail a test when reverted. A fifth was mutated, survived,
and was deleted for it (above).

`--list` gained something unplanned by saying where a name came from: it now
shows `sql_tables  (sql_query.py)`. That tool has always been declared in a file
named after its sibling, and nothing ever said so.

Importing a workspace tool used to leave a `__pycache__` beside it, in the
workspace. That was noted here as neither new nor ours -- it happened to flat
presets already -- and `main` fixed it independently while this was being built,
by suppressing bytecode for the length of one `exec_module`.

That fix made the seeder test above pass for the wrong reason: with no bytecode
ever created in the preset tree, there was none to carry, and the test would
have held whether the filter existed or not. It now plants the debris itself
against a fixture tree. The filter still earns its place -- a developer
importing a preset directly, or a wheel built with bytecode in it, puts the same
files there by a route this loader never sees.

## Still undecided

- **Whether a large catalogue needs anything at all.** Nesting does not help the
  model, and the two levers that would -- narrowing per request and delegating --
  already exist. A third that does not: one skill describing a family of tools,
  so twenty schemas become one listing entry and a body loaded on demand. Worth
  measuring against a real catalogue before building.
- **Whether `subagents/` wants packages too.** They are YAML, so there is no
  import to make work and no helper problem to solve; a recursive walk is the
  whole feature. If a subagent ever grows adjacent files, this is the decision to
  revisit.

# Assets as packages, not as cargo

**Status:** designed, not implemented.
**Date:** 2026-08-17

Kingfisher ships fifteen asset files inside the wheel: three skills, four
subagents, three tools. They are good files and each was added to demonstrate
one feature of one format. This says they should leave anyway, become pip
packages of their own, and be found rather than named.

The framework's job is loading and composing definitions held as static files.
What those definitions *say* is not the framework's business, and every asset
in the tree is something a real workspace rewrites on contact with a real task.

Read the decisions with the measurements. Where a measurement changed what was
about to be decided, it is recorded beside the decision rather than folded in.

## What is actually in there

| | |
|---|---|
| `skills/` | `code-review`, `release-notes` (with a `reference/`), `tabular-qa` |
| `subagents/` | `extractor`, `reviewer`, `second-opinion`, `analysis/profiler` |
| `tools/` | `http_fetch`, `sql_query`, `csv_profile/` (three files) |
| not an asset | `models.yaml.example`, `README.md` |

The last row is the one that decides most of this document. `models.yaml` is
**required and has no fallback**: a deployment without one gets a `ConfigError`
that prints a minimal version inline and then points at the shipped example,
with a comment calling it *"the only place a new deployment would think to look
for it."* That file is not content. It is the worked example of a mandatory
configuration file, and it has to arrive with the thing that demands it.

`README.md` is the same argument in a different shape. It carries the field
tables, the tool contract and the block-scalar rules, and tests hold it to the
code: the tool table against the real tool surface, the field table against
`KNOWN`, every fenced example through the real loader. That is documentation of
the framework's formats, not a catalogue of things to install.

## Decisions

| # | Decision | Why |
|---|---|---|
| A1 | **Kingfisher ships no skills, no subagents and no tools.** | Every one of them is content a workspace rewrites. The package's job is to find, validate and compose definitions, and it can do all three against files it did not write. The counter-argument — that a format is learned from a working file rather than a paragraph — is real and is answered by A6 rather than by keeping the tree. |
| A2 | **Assets become separate distributions that depend on kingfisher.** | The arrow points assets → framework and never back. Kingfisher does not list them as a dependency, an extra, or a name in its source; nothing in it changes state when they are absent. `pip install kingfisher-assets` pulls the framework along, so it stays one command for the person who wants both. |
| A3 | **Packs announce themselves through entry points; kingfisher names none.** | A named source (`--from kingfisher_assets`) is a soft dependency written in prose: kingfisher would have to document which name to type. With `[project.entry-points."kingfisher.assets"]` the framework asks who is installed and gets an answer, so a pack published by a team internally is no less first-class than an official one. This is the difference between a library with a blessed bundle and an ecosystem. |
| A4 | **Two packs claiming one filename is refused, naming both.** | Last-one-wins is *"silently different from what you asked for"*, which this codebase refuses at every other boundary — an upload that shadows a catalogue name, two tool modules claiming one tool, a workspace tool shadowing a built-in. A collision across packs is the same failure one level out, and the message has to name both packs because the person reading it may own neither. |
| A5 | **`models.yaml.example` and the format documentation stay, under a directory that is not called `presets`.** | See *What is actually in there*. Renaming matters: leaving them in a directory named for the thing that left is how the next reader concludes assets still ship. |
| A6 | **The documentation carries its worked examples inline.** | Four of the six documentation tests pass today only because the files they link to exist. A page that links into another repository rots quietly — someone renames a file over there and nothing here fails, which is the exact failure `test_every_variable_read_is_documented` and the `app/config.py` rename were written about. Inline examples keep the existing check working: `every_complete_definition_in_the_readme_parses` already pulls every fenced block starting with `name:` through the real loader, and needs no files at all. |
| A7 | **A fresh workspace seeds itself, in the driver only.** | Nothing is copied unless a pack was installed, which is an explicit choice; a new workspace is empty by definition so nothing can be overwritten; and it is the first moment the destination exists. It belongs in `main.py` and never in `Kingfisher.__init__` — constructing a library object must not write to somebody's disk. It prints what it wrote, because `is_new_workspace` also fires on a *misconfigured* workspace and a wrong path holding fifteen files reads more like success than an empty one does. |
| A8 | **Nothing is placed at install time.** | Wheels have no post-install hook, by design, and the `setup.py` tricks that used to work do not run under wheel installs. Even if they did, the workspace is chosen by `KINGFISHER_WORKSPACE` at *run* time and one install serves many — a build image, a CI box, none. A7 is what makes it feel automatic. |

## Measurements

| | |
|---|---|
| the assets, as a share of the wheel | 80 KB of 600 KB — 15 files. Weight was never the argument |
| what the tool presets import | stdlib and `langchain_core.tools`, already a dependency. Removing them frees nothing |
| are they imported by the package | no. They are copied out and loaded from the workspace, so they cost nothing at import and cannot break the package |
| library code that reads asset content | none. `confinement.py` mentions them in a comment; `model_catalogue.py` imports a *filename* to name in an error |
| the seeder's source | `PACKAGE = "kingfisher.presets"`, one constant, read through `resources.as_file(resources.files(PACKAGE))`. Making the source a parameter is the whole mechanism A3 needs |
| the seeder's destination | already generic: `destinations()` walks `CATALOGUE_KINDS` into `cfg.catalogue_roots`, and each of the three relocates independently. **The coupling seam was already built** — a deployment can point kingfisher at any tree today |
| test files that call the `presets` API | 5 of the 11 that mention the word |
| the 29 tests in `test_presets.py` | 13 test the framework (seeding, discovery, shadowing) and stay, needing a fixture tree; 10 test the assets and move; 6 test the documentation, of which 4 pass only because the linked files exist — A6 |
| every asset's stated reason | each demonstrates a distinct format feature, written down: `csv_profile/` is *"a tool that outgrew one file"*, `subagents/analysis/` exists because nested discovery does, `sql_query` narrows a capability `execute` already had. **This tree is a curriculum, not a bag of assets** — which is why A1 needed A6 rather than being obvious |

## Sequenced plans

Each step leaves the tree working.

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | The seeder takes its source as a parameter instead of a constant, and discovers packs through entry points. Kingfisher's own presets become the first pack it finds, registered from inside the package. Nothing moves yet; nothing breaks. | — |
| **2** | The fixture tree under `tests/`, and the 13 framework tests moved onto it. They stop depending on shipped content — which is how a preset count broke an unrelated reporting test earlier. | 1 |
| **3** | The documentation's examples go inline; the four link-dependent tests are rewritten against them. | — |
| **4** | The asset repository: the fifteen files, the ten tests that describe them, and its own `pyproject.toml` declaring the entry point and depending on kingfisher. | 1 |
| **5** | Kingfisher drops the assets and the entry point registration. `presets/` becomes `reference/`, holding `models.yaml.example` and the docs. `--seed-presets` is renamed, and `model_catalogue`'s error message follows it. | 2, 3, 4 |
| **6** | A7: a fresh workspace seeds from whatever packs are installed, and says what it wrote. | 5 |

Phases 1 to 4 are additive and reversible. Phase 5 is the one that removes
something, and it is deliberately last: until it lands, both arrangements work,
and the asset repository can be proven against a real kingfisher before the
files stop shipping.

## Still undecided

- **Whether one pack or several.** Entry points support many by construction,
  and A4 says what happens when two collide, but nothing here decides whether
  the official assets ship as one distribution or as `-analysis`, `-web` and so
  on. That is a packaging question best answered once there is more than
  fifteen files to divide.
- **What a pack declares about the format it was written for.** A definition
  using a field an older kingfisher does not know is refused by name, which is
  a loud and adequate failure. A tool is Python and fails differently. The
  ordinary answer is a version floor in the pack's own `pyproject.toml`, and
  the ordinary answer is probably right, but nothing has tested it.
- **What is lost with the working files.** The documentation is good today
  because `reviewer.yaml` can be opened and read — a file somebody shipped and
  maintains, not an example written to be an example. Inline examples stay
  honest and testable, and they are not the same thing. This is the strongest
  argument the current arrangement has, and A1 outweighs it rather than
  answering it.

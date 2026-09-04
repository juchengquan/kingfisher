# kingfisher

A library for running agents against a workspace, with what each request may do
decided by the caller rather than by the code.

```bash
pip install kingfisher
```

```python
from kingfisher import definitions_source, ensure_layout, paths_from_env, seed

paths = paths_from_env()
ensure_layout(paths.workspace, authored=paths.authored_files)

done = seed(paths, definitions_source(paths))
for name in done.written:
    print(f"seeded {name}")
for left in done.skipped:
    print(f"skipped {left.label} — needs {', '.join(left.names)}")
```

Four calls, and the first is belt-and-braces rather than an ordering you have to
get right. `seed` lays the workspace out itself, so `models.yaml.example` arrives
whether or not you call `ensure_layout` — a deployment told to write
`models.yaml` and given no example of one is a dead end, and the library closes
it from the inside. `authored` is where that example goes: `models.yaml` and
`groups.yaml` both relocate, and an example a directory away from the file it
describes is the same dead end wearing a different coat.

What the explicit call still buys is the path `seed` never reaches.
`definitions_source` refuses when a deployment has named no assets, and it
refuses *before* seeding starts — so without the line above, that deployment gets
an empty directory and a traceback instead of a laid-out workspace and an error
explaining itself. Cheap, idempotent, and it only shows up on the day something
is misconfigured.

`seed` requires a directory to copy from and will not invent one;
`definitions_source` is what turns configuration into that directory, reading
`KINGFISHER_ASSETS` and taking a path that overrides it. On the command, the
same two:

    kingfisher seed                          # from KINGFISHER_ASSETS
    kingfisher seed --from ./my-definitions  # from here instead

**Read `skipped`, or your workspace will quietly be missing agents.** A
definition naming middleware or groups this deployment has not registered is
refused when it is built, so seeding one produces a file that cannot run.
`seed` leaves those behind and says which names each would have needed;
`seed(..., everything=True)` and `kingfisher seed --all` take them once you have
registered the names. Seeding the shipped `assets_examples/` on a bare checkout
leaves five behind and writes thirteen.

`seed` takes a *destination* rather than a whole `Config` — anything with a
`workspace` and `catalogue_roots`, which both `WorkspacePaths` and `Config`
satisfy. That is what lets it run on a fresh workspace, before there is a
`models.yaml` inside it to read.

The formats — agents, skills, subagents, and the catalogue they live in — are
documented in [`docs/guides/formats.md`](docs/guides/formats.md). A tool is the
one that is code rather than a document, so writing one has its own page:
[`docs/guides/tools.md`](docs/guides/tools.md). The rest of
[`docs/`](docs/README.md) says why the code is shaped the way it is and what
deepagents actually does underneath it.

`kingfisher list` shows what a workspace offers a request — tools, skills,
subagents — and where each one came from. `kingfisher seed` writes a starting
workspace.

## What ships separately

**`kingfisher[service]`** — an HTTP surface over the library. Its own
distribution, so `pip install kingfisher` puts no web framework on disk, which
is checked against the built wheel rather than asserted.

**The definitions ship nowhere.** They were their own distribution once, found
through an entry point so anyone could publish a pack, and then a set inside
this wheel. Both are gone: where a deployment gets its definitions is a setting,
`KINGFISHER_ASSETS`, and a directory needs no wheel, no metadata and no publish
step.

The cost is real and is not hidden. A fresh install seeds nothing, and since a
request must name an agent, it cannot run until definitions arrive from
somewhere. This repository keeps a worked set in `assets_examples/` — one agent,
skill, subagent and tool, each demonstrating a distinct feature — and `assets/`
is where a deployment puts content it fetched from elsewhere.

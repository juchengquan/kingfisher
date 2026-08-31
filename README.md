# kingfisher

A library for running agents against a workspace, with what each request may do
decided by the caller rather than by the code.

```bash
pip install kingfisher
```

```python
from kingfisher import definitions_source, paths_from_env, seed

paths = paths_from_env()
for name in seed(paths, definitions_source(paths)).written:
    print(f"seeded {name}")
```

`seed` requires a directory to copy from — it will not invent one.
`definitions_source` is what turns configuration into that directory: it reads
`KINGFISHER_ASSETS`, and takes a path that overrides it. On the command, the
same two:

    kingfisher seed                          # from KINGFISHER_ASSETS
    kingfisher seed --from ./my-definitions  # from here instead

The formats — tools, skills, subagents, and the catalogue they live in —
are documented in [`docs/formats.md`](docs/formats.md), and the rest of
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
somewhere. This repository keeps a worked set in `examples/` — one agent, skill,
subagent and tool, each demonstrating a distinct feature — and `assets/` is
where a deployment puts content it fetched from elsewhere.

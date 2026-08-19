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
are documented in [`docs/formats.md`](docs/formats.md).

`kingfisher list` shows what a workspace offers a request — tools, skills,
subagents — and where each one came from. `kingfisher seed` writes a starting
workspace.

## What ships separately

**`kingfisher[service]`** — an HTTP surface over the library. Its own
distribution, so `pip install kingfisher` puts no web framework on disk, which
is checked against the built wheel rather than asserted.

The definitions are *not* separate. They were their own distribution, found
through an entry point so that anyone could publish a pack; a directory covers
the same ground without a wheel, and they ship inside this one so a fresh
install seeds something that works.

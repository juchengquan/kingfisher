# kingfisher

A library for running agents against a workspace, with what each request may do
decided by the caller rather than by the code.

```bash
pip install kingfisher
```

```python
from kingfisher import paths_from_env, seed

for name in seed(paths_from_env()).written:
    print(f"seeded {name}")
```

`seed` takes a directory to copy from and defaults to the definitions that ship
with kingfisher — one working tool, skill and subagent. Point it somewhere else
and it copies yours instead:

    kingfisher seed --from ./my-definitions

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

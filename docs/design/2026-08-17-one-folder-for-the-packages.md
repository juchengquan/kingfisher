# One folder for the packages

**Status:** implemented.
**Date:** 2026-08-17

Three distributions had grown three folders at the repository root — `src/`,
`assets/`, `service/` — with no way to tell from the tree that the last two were
packages and the first was the library. Tidiness, and nothing else: no caller
sees any of this.

## What was ruled out first, by measuring

**All three under one `src/`.** The obvious version, and it breaks development
silently. A package is built from its own folder; three distributions sharing
`src/` means two of them are not rooted there, and `force-include` copies their
files rather than linking them:

```
kingfisher loads from        : .../src/kingfisher/__init__.py        (live)
kingfisher_service loads from: .../site-packages/kingfisher_service/ (a copy)

source on disk says          : V="edited"
what python sees             : service
```

Edit the service, run the tests, and they pass against the code you just
changed. That is the failure this repository spends most of its effort refusing.

**The library under `core/`.** Would make the tree symmetric all the way down —
`kingfisher.core`, `kingfisher.service`, `kingfisher.assets`. Measured at **548
import lines across 105 files**, in exchange for something no caller sees.
Rejected on price, not on principle.

**A claim from the previous change, corrected.** That design said two
distributions cannot share a package name and that it "breaks on upgrade and
uninstall". That is wrong, and was never tested. Two wheels shipping disjoint
files under one package name install, uninstall and reinstall cleanly:

```
both installed:                  base='the public api'  service='service'
after uninstalling the service:  base='the public api'  service=GONE
after reinstalling the base:     base='the public api'  service='service'
```

So `kingfisher.service` is available whenever it is wanted. It is not taken here
because it does not serve tidiness, which is what this change is for.

## What it is now

```
pyproject.toml          the workspace, and the settings all three share
packages/kingfisher/    the library
packages/service/       the HTTP surface
packages/assets/        one working example of each kind
main.py, docs/, evals/
```

Each package has its own `pyproject.toml`, `src/` and `tests/`. The root file
keeps no `[project]` of its own — it is a uv virtual root, so one `uv sync`
installs all three and one `pytest` runs all three, while none of them can grow
a private idea of the line length.

`kingfisher[assets]` now exists, alongside `kingfisher[service]`. Assets was
install-by-name only, which is an odd asymmetry once its sibling has an extra.

## Found while moving

Four tests computed the repository as `parent.parent` of the test file, which
was true while the library *was* the repository. Under `packages/` that path
became the library's own directory, and each failed in its own way — the
dangling-import rule stopped seeing the other two distributions, and three live
helpers were reported as defined for tests alone because `main.py` had fallen
out of the walk.

Both are now found by a marker rather than a count: the repository is the
directory holding `pyproject.toml` *and* `packages/`. A count would go on
quietly working after the next move, pointed at the wrong tree.

## The check that was checking nothing

The `base-install` job installed the library and then ran it with `uv run`,
which **re-syncs the workspace first** — so it installed the two packages it
exists to prove are absent, started a real server, and failed on missing
configuration rather than on the service being there. It had been passing on the
previous layout for the same wrong reason, and only went red here because the
install path moved.

It now builds a wheel, installs it into a venv outside the workspace, and drives
that venv's own binaries. It also asserts the two siblings are unimportable,
which it never did.

## Verified

All three wheels built and read: each contains exactly its own package and
nothing else. The base installed alone resolves 46 public names, has neither
sibling importable, and `kingfisher serve` exits 1 saying how to get the service.

1359 tests, ruff and ty clean. The seven mutations from the previous change
re-run against the moved paths and are still caught — which took repointing the
script, since one aimed at the old tree passes for the wrong reason.

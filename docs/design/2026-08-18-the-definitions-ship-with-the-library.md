# The definitions ship with the library

**Status:** implemented.
**Date:** 2026-08-18

Reverses two things: the definitions stop being their own distribution, and the
three packages stop living under `packages/`. What is left is the library at the
root and the HTTP service beside it, which is where they were two days ago.

## Why the pack mechanism went

It was not incidental — it was a plugin architecture with a stated reason. Any
distribution could register under a `kingfisher.assets` entry point and seeding
would copy from every one it found, so a team publishing definitions internally
was no less first-class than the ones shipped alongside. Nine references in the
source, nine tests, and one asserting the framework registered no pack of its
own.

What replaced it is smaller and covers the case anyone actually had: **`seed`
takes a directory.** A deployment with its own definitions points at them —
no wheel, no metadata, no publish step. The entry-point group bought one thing a
path does not, which is *several* sources merged with a collision check, and
nobody had a second source.

## Decisions

| # | Decision | Why |
|---|---|---|
| D1 | **The definitions live in `src/kingfisher/assets/` and ship in the wheel.** | The alternative to a package was shipping nothing, and then `pip install kingfisher` followed by `kingfisher seed` gives an empty workspace. The definitions exist to teach the formats; content a reader has to go and find teaches nobody. 14 files, about 20KB against a 228KB wheel. |
| D2 | **`seed(cfg, source=None)`** — a directory, defaulting to the shipped ones. | The thing the plugin group was actually being used for, at a fraction of the surface. `--from` on the command, refused loudly when the path is not a directory rather than silently seeding nothing. |
| D3 | **`src/kingfisher/assets/` is excluded from every rule in `test_architecture`.** | It is content, not code: tools the *agent* imports and this package never calls, skills that are markdown, definitions that are yaml. `presets/` was excluded for exactly this before it left. Excluded in the collectors rather than rule by rule, so a rule added later cannot forget. |
| D4 | **`installed_packs` and `Pack` are replaced by `shipped_kinds`.** | `kingfisher doctor` reported which packs were installed. There is nothing to enumerate now — one directory either came with the install or did not — so the question became "is there anything to seed from". |
| D5 | **Back to `src/`, `tests/` and `service/` at the root.** | With two packages rather than three, a folder gathering them buys less than the indirection costs. |

## What was lost, plainly

Two tests have no successor, and neither can be written against a single
directory: two packs seeding together, and the refusal when two claimed the same
file. If a second publisher ever wants in, the entry-point group and both tests
come back.

## A marker that matched the wrong repository

`test_architecture` found the repository by looking upward for a directory
holding both `pyproject.toml` and `packages/`. When `packages/` went, nothing in
this tree matched — so the walk climbed *out of the checkout* and found the
parent clone, which is on `main` and still has one. Every rule then read a
different repository and passed. CI, with no parent clone, raised
`StopIteration` at collection.

It counts levels again. A marker only helps if it cannot match elsewhere, and
`src/` beside a `pyproject.toml` describes half the repositories on any disk.

## Verified

Built and read: the wheel carries 73 files, 14 of them definitions. Installed
clean into an empty environment, `kingfisher seed` writes three skills, four
subagents and the tools; `kingfisher seed --from ./mine` writes that directory
instead; and a path that is not a directory exits 2 saying so.

1363 tests, ruff and ty clean. Five mutations, each caught — including one that
made the shipped tools be judged as library code, which is the rule D3 exists
for.

## Superseded

D1 is reversed by *examples are ours, assets are yours* (2026-08-19). The
definitions leave the wheel: `src/kingfisher/assets/` becomes `examples/` at the
repository root, and where a deployment gets its definitions is a setting,
`KINGFISHER_ASSETS`, rather than a fact about the install.

D1's argument still stands and is simply outweighed. *"Content a reader has to
go and find teaches nobody"* is true; it is accepted because kingfisher is not
published, so the reader it protects does not exist yet. The full cost is
recorded there rather than here, including one this document did not have to
face: a request must name an agent, so a fresh install now produces a library
that cannot run at all.

What survived:

- **D2 held, and hardened.** `seed` still takes a directory. It is required now,
  with no default -- the default meant "the set that ships", and nothing ships.
- **D3 is retired rather than reversed.** `src/kingfisher/assets/` was excluded
  from every rule in `test_architecture` because the package carried content.
  It no longer does, so `CONTENT` and `_is_content` are gone: the separation
  stopped being a rule and became the layout.
- **D4 became `kinds_at(path)`.** `shipped_kinds` asked whether the definitions
  had arrived with the install, which could realistically only pass. A
  configured directory has four ordinary ways to be wrong.
- **D5 held.** `src/`, `tests/` and `service/` at the root, now with `examples/`
  and `assets/` beside them.

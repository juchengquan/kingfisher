# Examples are ours, assets are yours

**Status:** designed. Not implemented.
**Date:** 2026-08-19

Reverses D1 of *the definitions ship with the library*. The definitions leave
the wheel, the repository's own set is renamed for what it is, and where a
deployment gets its definitions becomes a setting rather than a fact about the
install.

## What this is actually about

Not tidiness. There is an untracked `examples/skills/pdf/` sitting at the
repository root right now — a skill fetched from somewhere else, carrying its
own `LICENSE.txt` and a `scripts/` folder, already shaped like a seed source.
It is untracked because there is nowhere legitimate to put it.

That is the whole problem. This repository holds two kinds of thing and has one
word for both:

| | the repository's own | fetched, like `pdf` |
|---|---|---|
| written | here | elsewhere |
| in git | yes — 20 files, ~48KB, and 400 lines of tests check them | no |
| licence | this repository's MIT | its own |
| purpose | teaching the formats: one working tool, skill, subagent, agent, each demonstrating a distinct feature | doing a job |

*assets-as-packages* already found the right word for the first column and did
not act on it: **"this tree is a curriculum, not a bag of assets"**. Calling it
`assets` was a leftover from when it was a wheel that shipped. So the two
columns get two names, and the folder that a deployment points at stops being
the folder this repository happens to keep its teaching set in.

## Decisions

| # | Decision | Why |
|---|---|---|
| E1 | **The definitions leave the wheel.** `src/kingfisher/assets/` becomes `examples/` at the repository root, outside `packages = ["src/kingfisher"]`. | They are content — the wheel is not how content should reach a deployment, and D1's own justification (`pip install` must produce a working workspace) is not currently load-bearing: kingfisher is not published. See *What was lost*. |
| E2 | **`KINGFISHER_ASSETS`, one path, read in `application/config.py`.** | Read *there* specifically: `test_every_variable_read_is_documented` finds variables by scanning that one module, so a variable read at the CLI edge is undocumented and nothing notices. One path rather than a list, because a list revives the two-source collision refusal that *the definitions ship with the library* deleted for having no user — and it still has none. |
| E3 | **Optional in config; `seed()` raises when it and `--from` are both absent.** | `_require` would make every non-seeding command depend on it. `paths_from_env` calls `KINGFISHER_WORKSPACE` *"the one thing no default can supply"*, and this is not a second one: a deployment that seeded six months ago runs fine without it. The absence only matters at the moment of seeding, so that is where it is refused. |
| E4 | **`models.yaml.example` moves from `seed()` to `ensure_layout()`.** | `_copy_example` states the invariant it would otherwise break: *"not conditional on a deployment having any"*. Once `seed` can refuse, the one worked example of a mandatory configuration file becomes unreachable on precisely the fresh machine that needs it. `main.py:437` records this exact trap from last time — *"A first run could not reach seeding at all, which is precisely the run seeding is for."* Moving it also makes `seed` one job: copy definitions from a directory. |
| E5 | **The repository's own set is `examples/`, holding the four kinds directly.** | It is a curriculum. `examples/skills`, `examples/subagents`, `examples/tools`, `examples/agents` is already a valid seed source, so `KINGFISHER_ASSETS=./examples` needs no nesting. An `examples/assets/` level would be a folder containing one folder; it earns its place when `examples/` holds a second kind of example and not before. |
| E6 | **`assets/` is committed, holds only a `README.md`, and ignores everything else.** | It names the convention: this is where fetched content lands. The `.gitignore` half is the load-bearing one — `examples/skills/pdf/` carries a third-party `LICENSE.txt`, and an MIT repository should not absorb someone else's terms as a side effect of `git add -A`. A README rather than a `.gitkeep`, because a hole held open by an empty file explains nothing, and *assets-as-packages* already warned that **"leaving them in a directory named for the thing that left is how the next reader concludes assets still ship."** |
| E7 | **Doctor's "definitions to seed" check is repointed, not deleted.** | It currently asks whether the definitions arrived inside the install, which its own comment admits *"is only ever wrong if an install is damaged"* — a check that can realistically only pass. After E1 the question becomes four ordinary mistakes: unset, mistyped, deleted, or pointed one level too high. `warn`, never `fail`: `worst()` makes `doctor` exit 1 on any failure, and a seeded workspace runs without the variable. |
| E8 | **Tests and `evals/seed.py` find `examples/` by walking to the repository root, never by reading `KINGFISHER_ASSETS`.** | The entire point of the variable is that it can point elsewhere. A `shipped()` fixture that read it would mean 400 lines of tests silently stop checking this repository's examples the first time a developer uses the feature, going green or red for reasons unrelated to the commit. |
| E9 | **`examples/` joins ty's `include` list and CI's ruff line.** | Measured: ty covers those files today only because they sit under `src`, and CI runs `ruff check src/ main.py tests/ service/` — an explicit list, not the repository. At the root they fall out of both silently. Both are clean on them today, so this keeps a check rather than fixing a mess. |
| E10 | **`seed(cfg, source: Path)` — required, no default, no `None` branch.** | E3 has to refuse somewhere, and `Destination` is the wrong place: it holds the workspace and the catalogue roots and nothing else, because *"asking for a whole `Config` to copy files was always more than the job required"* — the narrowness is what lets seeding run before a workspace exists. Widening it to carry a *source* would undo that on the first thing that asked. Required instead: the obligation is stated in the type, ty catches a caller who forgets at check time, and the `--from`-beats-the-variable precedence lives in one readable line at each call site rather than in a fallback chain inside the library. It breaks a public signature, which is honest — a call that silently starts refusing is worse than one that will not type-check. |
| E11 | **A helper resolves flag-or-variable, and it is public.** | Two callers need it — `kingfisher seed` and `main.py`'s auto-seed — and both must produce the same error naming `KINGFISHER_ASSETS` and suggesting `./examples`. Public because the shipped CLI is held to *"a consumer of the library, not an insider"*, so it cannot reach a private one. Same reason `shipped_kinds`' replacement `kinds_at(path)` stays exported for `doctor`. One implementation reachable two ways is the rule `main.py` already lives by; the alternative is two messages, of which the one you see daily is the one nobody reviews — `main.py` printed an instruction naming `--seed-assets` long after that was wrong advice. |
| E12 | **`main.py` stops rather than warns when a fresh workspace cannot be seeded.** | The alternative was warn-and-continue, on the grounds that the eval smoke brings its own skill (E8) and would have run anyway. Rejected because the warning fires **once per workspace**, in the middle of *"created a new workspace at…"*, and is the one line saying something did *not* happen. An error you cannot proceed past is the right shape for a condition hit once, whose fix is one line in `.env`. |
| E13 | **`--seed-assets` becomes `--seed`, with `--from DIR` beside it.** Two flags, not one with an optional argument. | The name had to change once "assets" means fetched content, since the flag will usually seed `examples/`. An optional argument was the intent, and argparse cannot express it safely here — `main.py` has a greedy `task` positional, and `--seed summarise the pdf` parses as `seed='summarise'`, `task=['the','pdf']`. Measured. Two flags cannot collide with the positional, and `--from` then means the same thing on the driver and on the command. `--from` without `--seed` is rejected: it can only be a mistake. |
| E14 | **The README's first example passes a literal path**, not the helper. | It currently reads `seed(paths_from_env())`, which E10 makes a type error — the project's front page teaches something that will not compile. A first example should be the smallest true thing, and *"you say where the definitions come from"* **is** the change, said in one line. `KINGFISHER_ASSETS` and the helper get a sentence underneath. The README's *"What ships separately"* section inverts: it exists to explain why the definitions are not separate, and now has to say that nothing ships but the library. `docs/formats.md` gets the same treatment — the entry-point history stays, its tense does not. |
| E15 | **`tests/assets/` is renamed.** | Six files — a probe tool, skill and subagent — standing in for a seed source, reached from exactly one line in `test_seeding.py`. Under E5 the word now means fetched content, so the fixture is misnamed the day this lands. One reference, so the rename costs a line; `tests/seed_source/` says what it is. |
| E16 | **`kingfisher seed` exits non-zero when it copies nothing.** | Nearly unreachable before — the shipped set always held all four kinds. Now it is one of the likeliest mistakes: `--from ./examples/skills` names a folder that exists, is readable, and holds no kinds. E4 sharpens it further, since without the catalogue example there is no longer a consolation file making the run look partly productive. Doctor still *warns* about the same state and that is not an inconsistency: doctor reports on a deployment, which runs fine on an already-full workspace, while `seed` is an action that did not happen. The message names the four kinds it looked for, because the mistake is almost always one directory level in the wrong direction. |
| E17 | **No agent ships. A deployment without definitions cannot run at all.** | Measured: `service.py:779` requires a request to name an agent and there is no built-in default, so every `run` raises until one arrives. That makes `agents/` unlike the other three kinds, which are genuinely optional — and it is the strongest case for the `models.yaml.example` carve-out being extended to a starter agent. Declined because kingfisher is not published, so the person it protects does not exist; and because a shipped agent is *usable*, where `models.yaml.example` is a template that must be copied before it does anything. If kingfisher is ever published, this is the first decision to revisit, and E4's paragraph is the argument already written. A built-in fallback agent in code is ruled out separately: `4c03e47` deliberately went the other way — *"Give the agent a file, so the thing that runs is a definition like the rest."* |
| E18 | **Four "try `kingfisher seed`" messages are rewritten.** | `service.py:784`, `service.py:791`, `listing.py:54`, `listing.py:184`. After E3 the bare command fails when `KINGFISHER_ASSETS` is unset, so a user with an empty workspace would follow the advice and hit a second error. This is the exact failure `main.py` already had once, with an error naming `--seed-assets` that failed the same way. They name a command that works from where the reader is standing. |
| E19 | **The architecture rule inverts and gets stronger.** | `test_the_shipped_definitions_live_only_under_assets` currently asserts the four kinds exist under `src/kingfisher/assets/`. It becomes: **no definition kind exists anywhere under `src/kingfisher/`.** Easier to state, impossible to satisfy by accident, and it is the check that stops E1 regressing. `CONTENT` and `_is_content()` are deleted with it — the exclusion has nothing left to exclude. |
| E20 | **The refusal message names `./examples` only when it exists.** | A fixed base — *"set `KINGFISHER_ASSETS` to a directory of definitions, or pass `--from DIR`"* — plus a suffix suggesting `./examples` when there is one. Naming it unconditionally would repeat the very fault E18 exists to fix: advice that is true in a checkout and false for the pip-installed reader who, by E17, is the one who cannot run anything at all. A stat call on a path that is already stopping costs nothing. It does put a branch on an error path, which is where untested code hides, so both halves get a test: stage the directory, stage its absence, assert each string. |
| E21 | **`assets/` keeps its name, and its README defuses the collision.** | `.env.example` points `KINGFISHER_ASSETS` at `./examples` while an empty `assets/` sits beside it, so a reader will assume the variable means the folder sharing its name. `vendor/` was considered — it names *theirs* where `assets/` names nothing, which is the actual distinction E5 draws — and rejected as a third rename for no functional change. The first line of `assets/README.md` does the work instead: point the variable here once there is content, and until then `.env.example` points at `../examples` so a checkout can seed. |
| E22 | **`.env.example` ships the variable uncommented, with a working relative value.** | `KINGFISHER_ASSETS=./examples`. Every other entry is either a commented absolute placeholder or, for the one required variable, uncommented — this is the first relative and first *real* value in the file, and the comment above it owns the break rather than leaving it looking accidental. Justified by E12: `main.py` now stops dead on a fresh workspace without it, so the file's job is to make a checkout work, and a placeholder needing an edit first defeats that. The comment also says the path resolves against the working directory — which fails in a subdirectory exactly when `.env` itself does, since `__main__.py:56` looks there and nowhere else. Placed near `KINGFISHER_WORKSPACE`, **not** with `KINGFISHER_SKILLS_DIR` and its siblings: those say where definitions land, this says where they come from. |

## What the measurement ruled out

**Keeping the folder at the root and mapping it into the wheel.** The obvious
way to have both, and it fails in the direction that hurts most. A hatchling
`force-include` of a root `assets/` into `demo/assets`:

```
wheel install     : files("demo")/assets/skills/SKILL.md  ->  present
editable checkout : files("demo")/assets/skills/SKILL.md  ->  does not exist
```

Under an editable install the force-include contributes nothing at all —
resources resolve into `src/demo/`, where the folder is not. So seeding would
work for someone who pip-installed and write nothing for every developer and
every `pytest` run in a checkout. That is the mirror of the staleness failure
*one folder for the packages* rejected, and worse, because CI's only test job
runs in a checkout.

## Landing

Three commits, in this order, each green on its own. The split is not about
diff size — it is that 1b's fallback is what makes 1c a *move* rather than a
move plus an API change plus a behaviour change. If something breaks after 1c,
one commit could not tell you whether it was the mechanism or the relocation.

| # | What lands | Decisions |
|---|---|---|
| 1a | `models.yaml.example` moves from `seed()` to `ensure_layout()`. No user-visible change; `seed()` becomes single-purpose before anything changes what it takes. | E4 |
| 1b | `KINGFISHER_ASSETS`, the public helper, and `seed(cfg, source: Path)` required — **still falling back to the definitions inside the wheel** when the variable is unset. Behaviour unchanged, every existing test passes. The whole API change, with nothing moved. | E2, E3, E10, E11 |
| 1c | The move. `src/kingfisher/assets/` → `examples/`, the gitignored `assets/`, doctor repointed, the fallback deleted so the variable becomes necessary, docs, tests, ty and ruff. | E1, E5–E9, E12–E19 |

1b ships a fallback that exists only to be deleted in 1c. That is deliberate and
belongs in its commit message, so it is not mistaken for a design.

Two further slices follow this work and are **not** part of it, each landing
alone: `main.py` → `tests/integration/driver.py` (which must update
`PRODUCTION` in `test_architecture`, since the driver is a real caller and
tests deliberately are not), and `tests/*.py` → `tests/unit/`. The bulk rename
goes last: it conflicts with anything in flight.

## Combining two sources already works

E2 takes one path, and it would be easy to read that as giving up on combining
the repository's examples with fetched content. It is not. `_copy` uses
`copytree(dirs_exist_ok=True)`, so seeding twice merges rather than replaces:

```
kingfisher seed --from ./examples
kingfisher seed --from ./assets
```

The workspace ends up holding both, in an order the caller controls, and
`_overwritten` compares file contents beforehand — so warnings appear only
where the two sources genuinely collide on the same file, and nowhere else.

What a list of paths would add is doing that in one command with a *refusal* on
collision rather than a warning. That is a smaller gap than it looks, and it is
written here so that nobody rebuilds the entry-point group to get something the
workspace already gives them.

## What was lost, plainly

**`pip install kingfisher` produces a library that cannot run anything.** Not
merely a workspace with no examples in it — a request must name an agent,
`service.py:779` offers no default, and so every `run` raises until definitions
arrive from somewhere. That is D1's entire argument, reversed, and then some:

> The definitions exist to teach the formats; content a reader has to go and
> find teaches nobody.

That sentence is still true. It is accepted here because kingfisher is not on
PyPI, so the reader it protects does not exist yet. The design is shaped so
that when they do, the answer is a *value for a variable* rather than a
re-architecture: publish the examples somewhere fetchable, or ship them again
under a build target, and `KINGFISHER_ASSETS` is already the seam.

Three assertions invert and must be rewritten rather than deleted, since each
one is now the wrong half of a real question:

- `test_seeding_source.py::test_the_definitions_ship_with_the_library`
- `test_architecture.py::test_the_shipped_definitions_live_only_under_assets`
- `tests/conftest.py::shipped`, documented as *"reached as an install would"*

**One thing gained, worth naming.** `test_architecture.py` carries
`CONTENT = "assets"` and `_is_content()` to strip the definitions out of every
rule by matching a path segment inside `SRC`, *"so a rule added later cannot
forget"*. That mechanism exists only because content lives inside the code
tree. After E1 it has nothing to do: the separation stops being a rule and
becomes the layout.

**Also lost:** the overwrite warning for `models.yaml.example`, since
`ensure_layout` returns a path rather than a report (E4). Acceptable — the file
is meant to be copied to `models.yaml`, never edited in place, so overwriting
it is the designed behaviour rather than lost work.

## Still undecided

- **`BaseSettings`.** The intent is to regulate the environment through pydantic
  later. `BaseSettings` is not in pydantic v2 — it moved to `pydantic-settings`,
  a separate distribution, and it is **not installed here** (pydantic 2.13.4 is,
  transitively). That is a new direct dependency in a `pyproject.toml` with
  strong stated opinions about transitive dependencies not being promises, so it
  deserves its own decision rather than riding along with this one.
- **Whether a list of paths ever comes back.** E2 takes one path on the grounds
  that there has never been a second source. If two arrive, the prior art is
  written down: the entry-point group and the two tests that went with it.
- **Whether `examples/` grows a second kind of example** and earns the nesting
  level E5 declines to build today.

## To verify when this lands

Not yet implemented, so nothing below is a claim — it is the list.

- A built wheel contains no `agents/`, `skills/`, `subagents/` or `tools/`, and
  still contains `models.yaml.example`.
- A fresh workspace laid out by `ensure_layout` has `models.yaml.example` in it
  before anything is seeded.
- `kingfisher seed` with the variable unset exits non-zero naming the variable
  *and* suggesting `./examples`; with `--from` it ignores the variable.
- `kingfisher doctor` on a deployment with a full workspace and no variable set
  exits **0** with one warning, not 1.
- The four doctor states — unset, missing, present-but-empty, ok — each print a
  distinct remedy.
- `ty` and CI's `ruff` both still read `examples/`; confirmed by breaking a file
  there on purpose and watching each go red.
- `git add -A` with a fetched skill in `assets/` stages nothing from it.
- Mutation-test E8: point `KINGFISHER_ASSETS` at a different directory and
  confirm the asset tests still read `examples/`.
- `seed(cfg)` with no source no longer type-checks; ty reports it.
- `main.py --seed --from ./x summarise the pdf` seeds `./x` and runs the task —
  the parse that E13's measurement rejected for the one-flag form.
- `--from` without `--seed` exits non-zero rather than being ignored.
- Seeding `./examples` then `./assets` leaves a workspace holding both, with an
  overwrite warning only where a file genuinely collides.
- `kingfisher seed --from ./examples/skills` — one level too deep — exits
  non-zero naming all four kinds it looked for.
- Every message that tells a reader to seed names a command that works with
  `KINGFISHER_ASSETS` unset. Provoked, not read: empty the workspace, unset the
  variable, and follow each of the four instructions to the letter.
- No directory named `agents`, `skills`, `subagents` or `tools` exists anywhere
  under `src/kingfisher/`, and `CONTENT`/`_is_content` are gone from
  `test_architecture`.
- Both halves of E20's message: with `./examples` present the suffix appears,
  without it the base message stands alone. Two tests, not one.
- Copying `.env.example` to `.env` in a fresh checkout is enough for
  `kingfisher seed` to work with no further edits.
- `test_every_variable_read_is_documented` still passes — it enforces E22's
  `.env.example` line automatically, given E2 puts the read in
  `application/config.py`.

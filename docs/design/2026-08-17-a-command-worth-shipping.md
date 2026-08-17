# A command worth shipping, and the exports that make it one

**Status:** designed, not implemented.
**Date:** 2026-08-17

The assets work left one thing unfinished, and it is written into that
document's *Still undecided*: a deployment that installed kingfisher from a
wheel has the definitions and no way to put them anywhere. Discovery is an entry
point, so an installed pack **is** found — but the command that seeds lives in
`main.py`, which is not in the wheel, and the error a fresh deployment hits
names that command by name.

This says what to build instead: a small `kingfisher` command that seeds and
lists, held to the same rule as the server — it may only use the library's
public API. Not a general CLI. `main.py` stays what it is.

Read the decisions with the measurements. Two of the measurements changed a
decision that had already been made out loud, and both are recorded beside it
rather than folded in.

## What `main.py` actually is

Its own docstring opens: *"Still not a CLI in the sense that matters —
kingfisher is a library and this just drives it."* That is accurate, and the
first line of `main()` after the argument parsing says why:

```python
is_smoke = not task
```

Running `main.py` with no arguments **is** the eval smoke — a real model call,
against whatever key the deployment holds, checked for pass or fail. That is
the right default for a development driver and an indefensible one for
something a stranger installs.

So this is not an extraction. Nothing is being lifted out of `main.py` and
shipped; a new, smaller thing is being written, and `main.py` keeps its default,
its flags, and its smoke.

## Decisions

| # | Decision | Why |
|---|---|---|
| B1 | **The shipped command does two things: `seed` and `list`.** | A pip user already has `Kingfisher`, `run` and `stream` for running a task, and `kingfisher-server` for serving one. What they have no way to do is fill a workspace or see what is in it. The task-running flags — `--no-checks`, `--input`, `--data`, four grant axes — exist to drive development, and are a large surface to promise to people who should be calling `run()`. |
| B2 | **It is a consumer of the library, not an insider.** | The same rule the server is held to: `from kingfisher import X`, never `from kingfisher.infrastructure.y import Z`. The point is not tidiness. This design's whole claim is that finding and seeding packs is something *any* caller can do; if the one command that seeds has to reach into `infrastructure` to do it, the claim was never true and nothing would have caught that. The server's rule shook out `async_checkpointer` and the caller-facing errors before anyone outside needed them. |
| B3 | **Seeding and the inventory become public API.** | Follows from B2, and is overdue independently: `assets/README.md` already tells outside callers to write `from kingfisher.infrastructure import seeding`, which reaches past the front door into a module carrying no promise. Either that snippet is wrong or the names are public. They are public. |
| B4 | **The library answers "what does this workspace offer" with a record, not a list of names.** | Every field beyond the names is there because something can be relocated or can fail silently: the resolved catalogue paths, because `KINGFISHER_SKILLS_DIR` can point anywhere and a stale path has caused three bugs; each tool's source, because a folder cannot reach a tool's name; the load warnings, because a skill nested too deep loads as nothing. A names-only answer drops exactly what makes listing worth doing — and then `main.py` keeps its own richer listing, which is two listings that drift. |
| B5 | **Subcommands, and bare `kingfisher` prints help.** | A shipped command needs a safe do-nothing default, and this repository has already paid to learn what a wrong one costs: bare `main.py` spends money. Flags force a default to be invented — `kingfisher` alone would mean nothing, or an error, both worse-behaved than help. Verbs also grow without competing: a later `doctor` is a new word, not a fifth flag arguing about what bare invocation means. |
| B6 | **`main.py` keeps `--seed-assets` and `--list`.** | Drift comes from two implementations, not two doors. Both call the same exported functions and print through the same formatter, so there is one implementation reachable two ways. Removing them costs 23 edits across 8 files to buy nothing, and leaves the daily driver unable to seed. |
| B7 | **The catalogue error names `kingfisher seed`.** | It currently names `--seed-assets`, which is a flag on a file a pip user does not have. A console script is on `PATH` in a checkout too — measured — so `kingfisher seed` is the one instruction that is true for whoever is reading it. |
| B8 | **This makes kingfisher publishable. It does not publish it.** | Publishing means owing a version floor and a deprecation cycle, and the formats are still moving — `--seed-presets` was renamed, `provider:` removed, `where::what` landed days ago. B1–B7 remove the reason a pip install would be useless; whether to take that step is a separate decision, still open, and the asset pack's missing `kingfisher` floor waits on it. |

## Measurements

| | |
|---|---|
| `main.py`, split by what it touches | **341 lines are free of the smoke harness; 198 touch it** — and those 198 are two functions, `prepare_smoke` (20) and `main` (178). An earlier claim that `is_smoke` branched "through all 751 lines" was wrong |
| what bare `main.py` does | `is_smoke = not task` — it runs the eval smoke, with a real model call |
| what `--list` costs if the command assembles it | 5 exports — `registered_tools`, `resolve_catalogue`, `source_of`, `offered`, the skill layout — several dragging internal shapes (`Catalogue`, `Offering`, `Found`) with them |
| what `--list` costs if the library answers it | **1, plus the record.** `_offered(cfg)` already exists in `main.py` and already returns `dict[str, tuple[str, ...]]` — the four grant axes, plain strings, no internal types. This is the measurement that changed B4 from "names only, it is cheaper" |
| what is public today | `build_agent`, `ensure_layout`, `from_env`, `paths_from_env`. Nothing at all for seeding — not `seed`, `installed_packs`, `Pack` or `Seeding` |
| references to the flag being kept | 23, across 8 files, 9 of them in `test_main.py`. This is the measurement that changed B6 from "remove it" |
| is a console script reachable from a checkout | yes. `.venv/bin/kingfisher-server` exists after `uv sync` and `uv run kingfisher-server` starts it, so B7's instruction is true for both audiences |
| the rule to extend | `_server_modules()` globs `SRC / "server"`; `_reaches_past_the_public_api` allowlists `kingfisher.server`. A CLI subpackage mirroring `kingfisher/server/` extends both by one name each |

## The shape

```
kingfisher seed      # copy in what installed packs hold, and say what was written
kingfisher list      # what this workspace offers
kingfisher           # help
```

`kingfisher/cli/`, mirroring `kingfisher/server/`: a `__main__.py` that decides
nothing, dual-invocable as `python -m kingfisher.cli`, and the printing beside
it. `main.py` imports that printer rather than keeping its own, which is what
makes B6 safe.

`inventory(cfg)` goes in `infrastructure/`. It builds an agent to answer
honestly — the tool surface includes whatever the workspace defined — so it
cannot sit any higher.

`seed` rather than `seed-assets`: the suffix distinguishes that flag from the
other things `main.py` seeds, and a dedicated command has nothing to
distinguish it from.

## Sequenced plans

Each step leaves the tree working, and no step is only a rename.

| Phase | Deliverable | Depends on |
|---|---|---|
| **1** | `inventory(cfg)` and its record, in `infrastructure/`. `main.py`'s `show_inventory` and `_offered` become callers. Nothing is exported yet, and nothing new is reachable — the listing has one implementation instead of two halves. | — |
| **2** | The exports: `seed`, `installed_packs`, `inventory`, and the records they return, each classified light or heavy. `assets/README.md`'s snippet stops reaching past the front door. | 1 |
| **3** | `kingfisher/cli/`, the two subcommands, help on bare invocation, and `[project.scripts]`. The architecture rule extends to cover it, which is what proves B2 rather than asserting it. | 2 |
| **4** | `main.py` prints through the CLI's formatter; the catalogue error names `kingfisher seed`; both READMEs say so. | 3 |

Phase 1 is invisible from outside and reversible. Phase 3 is the one that adds a
promise, and it is deliberately after the exports exist, so the rule is checked
against a real consumer rather than against an empty package.

## Still undecided

- **Whether `list` grows a machine-readable form.** `--json` is the obvious ask
  the first time somebody scripts against it, and B4's record is already the
  right shape to serialise. Nothing here decides it, and adding it later breaks
  nobody.
- **What `list` prints for a tool's source.** Today: `csv_columns
  (csv_profile)`, which reads well and cannot be pasted into a definition —
  `profiler.yaml`'s comment was corrected to stop implying otherwise. The
  qualified-tool-references design wanted the reference form,
  `csv_profile::csv_columns`. Making the record public does not settle it,
  because the record carries the source and the *printer* chooses. That is the
  right place for the question to live, and it is still open.
- **Whether `kingfisher-server` and `kingfisher` should be one command.**
  `kingfisher serve` is the shape most tools converge on, and two scripts from
  one distribution invites it. Against: the server is behind an extra, so
  `kingfisher serve` would be a subcommand that is missing on a plain install,
  which is worse than a script that is absent. Not decided here.

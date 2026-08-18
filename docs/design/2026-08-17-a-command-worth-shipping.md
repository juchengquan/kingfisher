# A command worth shipping, and the exports that make it one

**Status:** implemented, B1-B12. Five verbs.
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

It grew to five verbs — `seed`, `list`, `doctor`, `serve`, `help` — and none of
them runs a task, which is the line B1 drew and the one that still holds. What
the rule bought is easier to see now than it was then: every verb reached for
something private and every reach became an export, seven of them, each one a
promise made on purpose rather than a name somebody guessed a consumer might
want.

**Written against asset packs, which are gone.** This assumed definitions came
from separate distributions found through an entry point, and *2026-08-17
assets-as-packages* has since been reversed: they ship inside the wheel and
`seed` takes a directory. Every line below mentioning a pack, `installed_packs`
or `Pack` is a record of what was true when the command was designed. Nothing
about the command changed -- `seed` still seeds, from one source instead of
several -- and the two names left the public API with the mechanism.

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
| B9 | **`list` grows `--json`.** | The first thing anyone scripting against it asks for, and it costs almost nothing: `Inventory` is already the shape to serialise and its fields are already public, so this promises nothing new. The human form stays the default -- a listing whose default output is JSON is a listing nobody reads. |
| B10 | **`doctor` answers "why will this not start?", and nothing else.** | A name is not a job. Without a stated one it becomes a second `list` that prints the same things in a different order. Its job is every check that stands between an install and a run: the catalogue loads, every endpoint it names has a credential, every alias is bound, all three catalogues parse, there are definitions to seed from, **every definition can actually run**, and the shell confinement this host will actually use. Those checks existed already -- scattered across error paths, a warning in `model_catalogue.load`, and `warn_if_unconfined` in a driver that is not in the wheel. Collecting them is the whole value: they are the things you want to know *before* a run costs money. Two of them arrived later than this list, and one of them is the reason: writing the credentials check found that `doctor` could not report an absent one at all, and misdiagnosed the consequence -- see *2026-08-18 what-the-catalogue-dropped*, whose C4 and C5 are the two additions. |
| B12 | **There is a `help` verb, and this document argued against one.** | The argument is below and still holds as far as it goes: `-h`, `--help`, bare `kingfisher` and `<verb> --help` all reach the same text, so this is a fifth route to keep consistent rather than a way out of being stuck. It was asked for twice and built. What building it found -- and what the argument had missed -- is that an unknown verb is a case none of those four handle well: argparse refuses `kingfisher teleport` with a usage line, where `kingfisher help teleport` can say which words exist. That is a real difference, and it was not visible from the outside. |
| B11 | **`serve` is added as an alias; `kingfisher-server` stays.** | Two names for one thing is a real cost, and it is smaller than breaking every script, unit file and container that already calls `kingfisher-server`. Nothing is published, so nothing external breaks -- but "external" is the wrong test for a command a deployment already runs. One implementation, two ways in, which is the arrangement B6 already chose for the listing. |

| B8 | **This makes kingfisher publishable. It does not publish it.** | Publishing means owing a version floor and a deprecation cycle, and the formats are still moving — `--seed-presets` was renamed, `provider:` removed, `where::what` landed days ago. B1–B7 remove the reason a pip install would be useless; whether to take that step is a separate decision. It cited the asset pack's own missing floor as waiting on the same call, and that pack is gone -- see *Still undecided*, which is where the question lives now. |

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

`kingfisher/presentation/cli/`, mirroring `kingfisher/server/`: a `__main__.py` that decides
nothing, dual-invocable as `python -m kingfisher.presentation.cli`, and the printing beside
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
| **3** | `kingfisher/presentation/cli/`, the two subcommands, help on bare invocation, and `[project.scripts]`. The architecture rule extends to cover it, which is what proves B2 rather than asserting it. | 2 |
| **4** | `main.py` prints through the CLI's formatter; the catalogue error names `kingfisher seed`; both READMEs say so. | 3 |

Phase 1 is invisible from outside and reversible. Phase 3 is the one that adds a
promise, and it is deliberately after the exports exist, so the rule is checked
against a real consumer rather than against an empty package.

The three verbs above are additive and independent of each other:

| Phase | Deliverable | Depends on |
|---|---|---|
| **5** | B9: `list --json`, from the record that already exists. | 4 |
| **6** | B10: `doctor`, and the one export it needs -- a confinement check a consumer can call, since `confinement.resolve` takes six arguments and `main.py` is the only thing assembling them. | 4 |
| **7** | B11: `serve`, sharing an implementation with `kingfisher-server`. | 4 |
| **8** | B12: `help`, reading its verbs from the parser so it picks up whatever else lands. | 4 |

The argument against a `help` verb, kept because it is most of the reason to be
sparing with the others: `-h`, `--help`, bare `kingfisher` and `kingfisher
<verb> --help` already print this text, so a fifth route is not discoverability,
it is another thing to keep consistent. B12 overrides it on one point the
argument had no way to see from the outside.

All four landed within a day of each other and each conflicted with the last, in
the same place: `main()`'s chain of `if args.command == ...`. Five verbs took
that chain past ruff's return-statement limit, which was the useful part -- only
two verbs take `--json`, the fallthrough read `args.json`, and so the *order* of
those branches was load-bearing while looking interchangeable. Reordering two of
them was an `AttributeError` waiting for somebody tidying up. It is a table now,
each verb naming the arguments it actually has, and a test compares the table
against the parser's own choices both ways -- because a table turns an unwired
verb from a wrong answer into a `KeyError` in front of whoever typed it, which
is louder and still too late.

## Still undecided

- **Whether to publish at all — deferred, deliberately, and asked twice.**
  Nothing blocks it any more, which is the change worth recording: both wheels
  build and install into a clean environment, and `kingfisher seed` ships inside
  one, so a pip install now works rather than handing somebody definitions and
  no way to place them. That was the objection, and B1-B7 removed it.

  What is left is a commitment rather than a task. Publishing means owing a
  version floor and a deprecation cycle from that point, and the formats have
  moved three times in the weeks around this: `--seed-presets` renamed,
  `provider:` removed, `where::what` added, and the definitions themselves
  changed distribution and changed back. None of those would have been free
  under a published floor.

  The detailed version of this question was written into
  *assets-as-packages*, which has since been superseded -- so it is restated
  here, where a reader looking at the shipped command will find it.

- ~~**Whether `list` grows a machine-readable form.**~~ Decided as B9.
- ~~**What `doctor` does about a check it cannot answer.**~~ Answered by
  *2026-08-18 what-the-catalogue-dropped*, and the asking found a bug: `doctor`
  could not report a credential that was simply *absent*, and misdiagnosed the
  consequence as an undefined model. With that fixed the case a probe would
  catch shrinks to present-and-rejected, and C6 declines it -- a command that
  sometimes costs money comes out of the pipeline. The limit gets printed
  instead of assumed.
- ~~**Whether `doctor` and the smoke overlap.**~~ Answered by the same document
  as C8: they barely do. The smoke checks `GROUND_TRUTH` against a fixture the
  eval harness generates -- *does kingfisher still get its own dataset right*,
  which is a maintainer's question, not a deployment's. B1 holds without needing
  to be defended, and the consequence is stated rather than implied: nothing
  shipped makes a real call, and a caller's own task is the better proof.
- **What `list` prints for a tool's source.** Today: `csv_columns
  (csv_profile)`, which reads well and cannot be pasted into a definition —
  `profiler.yaml`'s comment was corrected to stop implying otherwise. The
  qualified-tool-references design wanted the reference form,
  `csv_profile::csv_columns`. Making the record public does not settle it,
  because the record carries the source and the *printer* chooses. That is the
  right place for the question to live, and it is still open.
- ~~**Whether `kingfisher-server` and `kingfisher` should be one command.**~~
  Decided as B11, and the argument recorded against it was simply wrong.
  *"`kingfisher serve` would be a subcommand that is missing on a plain
  install"* -- it would not. `kingfisher-server` is already present whether or
  not the extra is installed: it imports uvicorn inside the function and prints
  *"needs the server extra: pip install 'kingfisher[server]'"* when it is not
  there. A subcommand does exactly the same, so the objection described a
  problem the existing script had already solved. Checked in
  `presentation/__main__.py` rather than remembered.

# kingfisher-assets

One working definition of each thing a kingfisher request can activate — a
skill, a subagent, a tool.

    pip install kingfisher-assets
    uv run main.py --seed-assets       # copies them into $KINGFISHER_WORKSPACE

Nothing here is loaded automatically, and nothing here is imported. The files
are copied into a workspace and read from there; a tool is imported *from the
workspace*, never from this distribution.

> **Seeding needs a kingfisher checkout today.** `main.py` is the driver and it
> is not in the framework's wheel, so there is no `kingfisher --seed-assets` for
> someone who only pip-installed. Installing this pack is still what makes the
> definitions *available* — discovery is an entry point, not a path — and any
> caller can seed from it in three lines:
>
> ```python
> from kingfisher.infrastructure import seeding
> for name in seeding.seed(cfg).written:
>     print(f"seeded {name}")
> ```

## Why it is a separate package

Kingfisher loads and composes definitions held as static files. What those
definitions *say* is not the framework's business — every one of these is
content a workspace rewrites on first contact with a real task, which is a
different kind of thing from the code that reads it.

So the framework ships none of them, and finds whatever is installed. It names
no pack anywhere in its source: it asks. A pack you write yourself is
discovered by exactly the same mechanism and is no less first-class than this
one.

    [project.entry-points."kingfisher.assets"]
    my-pack = "my_assets"

Two packs claiming the same file are refused, naming both, before anything is
copied.

## What each one is for

Each exists to demonstrate one feature of one format, not because you need the
capability. Copy the shape and rewrite the content.

| | |
|---|---|
| `skills/code-review` | a skill as a single file — the common shape |
| `skills/release-notes` | a `reference/` file the body points at, read on demand |
| `skills/tabular-qa` | a procedure a delegate re-uses, rather than one the main agent reads |
| `subagents/reviewer` | independence — recomputing a claim without seeing how it was reached |
| `subagents/extractor` | context isolation — reading a large pile, returning a short answer |
| `subagents/second-opinion` | a different model, which is worth nothing until you give it one |
| `subagents/analysis/profiler` | a definition in a folder, still activated by its `name:`; and `where::what`, saying which package a tool comes from |
| `tools/http_fetch` | something the built-in set cannot do at all |
| `tools/sql_query` | narrowing a capability `execute` already had |
| `tools/csv_profile` | a tool that outgrew one file, as a package |

The formats themselves are documented by kingfisher, beside the code that
enforces them.

# Saying where a tool lives, in the file that names it

**Status:** implemented. One row of *What changes* landed differently — see *Corrections*.
**Date:** 2026-08-17

A subagent definition names its tools by name and nothing else:

```yaml
tools: [csv_profile, csv_columns]
```

Those two come from `tools/csv_profile/`, a package. Nothing in the line says
so. Once `tools/` may be nested to any depth -- which it may, since
[nested discovery](2026-08-16-nested-discovery.md) -- a reader holding a
definition has no way to find the file except to grep for the name.

This adds a second, optional spelling that carries the path and is checked:

```yaml
tools:
  - csv_profile::csv_columns
  - sql_query.py::sql_tables
```

## What this is not

It is **not** a way to tell two tools apart. Measured before designing
anything: two tools of one name cannot coexist in a running agent at all.

```
with two tools both named 'clash': ['clash']
which one survived              : From B.
```

The model calls a tool by typing its name, and the agent looks that name up in
a dictionary. A dictionary holds one entry per name, so the second silently
replaces the first. The loader already refuses the pair before that can happen:

```
b/t.py: tool 'clash' is already defined by a/t.py
```

So there is never more than one candidate, and the path never has to choose.
It is a *claim about where the one tool lives*, which is checked -- never a
selector.

## The version that was considered and rejected

Allowing duplicates and letting the path pick between them is coherent, and it
was worked through before being dropped. Two findings killed it.

**The agent loads every workspace tool regardless of the grant.** Measured:

```
request granted only csv_profile
tools actually registered: csv_columns, csv_profile, http_fetch, sql_query, sql_tables
```

A grant is a refusal at call time, not a narrower load. So both `clash` objects
would reach the same agent and one would win before any path could be consulted.
Making the path a selector therefore requires the agent to load only what was
chosen -- which also makes the tool list vary per request, and that list is part
of what gets cached between turns. The cost of that was not measured, and
guessing at it is how the cache regressions in this repo have started.

**A clash would go silent in three places, not one.** A plain name matching two
tools; `tools: ["*"]`; a request that grants nothing and gets everything. Each
needs its own guard, each is a chance to miss one, and the failure is the worst
kind -- a tool with the right name running the wrong code, no error, wrong
answer. Refusing at load is one check in one place that cannot be bypassed.

Delegates cannot rescue it either: `as_subagent` supplies no tool objects, and a
`ToolAllowlist` selects *by name* from what the parent registered. Two delegates
cannot each hold a different `clash`, because the parent can only hold one.

## Decisions

| # | Decision | Why |
|---|---|---|
| Q1 | **The path is checked, not ignored.** A definition naming `csv_profile::csv_columns` fails if that tool now lives elsewhere. | An unchecked prefix is worse than the comment the profiler preset already carries: it looks authoritative and can quietly stop being true. The cost is real and chosen -- moving a tool breaks every definition that named its location. |
| Q2 | **The real path with `::`, not a dotted module name.** | `foo.tool_name` is ambiguous once folders nest: `research.find_company` is either the module `research/find_company.py` or a tool `find_company` under `research/`. Only `research.find_company.find_company` resolves it, and one tool four folders down needs five segments. The path form is what the loader already records and what `--list` already prints, so nothing has to be translated and nothing can drift. `::` because a Windows path can carry a single colon, and pytest taught everyone this shape. |
| Q3 | **No trailing slash on a package.** `csv_profile::csv_columns`, not `csv_profile/::csv_columns`. | `.py` already says "file"; its absence says "folder". The slash adds only noise, and `/::` reads badly. A pasted slash is accepted and normalised away, so copying out of `--list` is not a near-miss. `--list` changes to print the reference form, so what you read and what you type match. |
| Q4 | **Both spellings work.** `tools: [csv_columns]` is unchanged. | Requiring the long form means rewriting every definition here and in every workspace using kingfisher, to impose a check on authors who never asked for it. It cannot be a clean rule anyway: `tools: []` and `tools: ["*"]` have no path to carry. Presets and the README use the long form, so it is what people copy. |
| Q5 | **A wrong path fails at construction.** | `Catalogue.warm()` already reads all three repositories at startup so that "a subagent with an unknown field" fails there rather than on the first turn. A tool path that no longer resolves is the same mistake one layer in, and the seam already exists. Uploaded subagents arrive per request and are checked when provisioned. |
| Q6 | **Requests keep plain names.** `--tools` and `capabilities.tools` do not take the long form. | A definition is written once and read many times, often by someone who did not write it -- that is where a location pays. A flag is typed once and discarded. And `capabilities.tools` arrives from a caller who has no idea what these folders look like: asking them for a path makes the layout part of the API, and moving a file a breaking change for people who never saw it. Files carry paths; callers carry names. |

## The shapes, for reference

Every preset tool, and how each is written long-form:

```
tool           source                            reference
csv_profile    csv_profile/                      csv_profile::csv_profile
csv_columns    csv_profile/                      csv_profile::csv_columns
http_fetch     http_fetch.py                     http_fetch.py::http_fetch
sql_tables     sql_query.py                      sql_query.py::sql_tables
sql_query      sql_query.py                      sql_query.py::sql_query
read_file      —                                 (built-in: never qualified)
```

Three of five repeat themselves, which is a fair summary of how much the long
form buys: it is worth writing where the name and the file differ, and merely
tidy where they do not. That is an argument for Q4 rather than against the
feature.

Built-ins take no path at all. They have no file, and they live on the separate
`builtin_tools:` axis -- which is also why nothing here has to decide what a
qualified `read_file` would mean.

## What changes

| File | Change |
|---|---|
| `domain/subagent.py` | parse `source::name`, keep the pair on the spec |
| `infrastructure/catalogue.py`, `infrastructure/agent.py` | check the claimed source against the real one |
| `infrastructure/catalogue.py` | `warm()` checks every catalogue definition's paths |
| `domain/tool.py` | `split_reference`, `reference`, and `Offering.refuse_moved` |
| `infrastructure/tool_store.py` | reference form for a `Found` (no trailing slash) |
| `main.py` | `--list` names the source each tool came from |
| `kingfisher_assets/subagents/analysis/profiler.yaml` | the long form, demonstrated |
| `reference/README.md` | the long form, and why a request does not use it |

The last two moved while this shipped: the definitions are a separate
distribution now and `presets/` is `reference/`. See
*2026-08-17-assets-as-packages*.

A catalogue whose definitions all use plain names behaves exactly as it does
today, which is every catalogue that exists right now.

## Still undecided

- **Whether the long form should ever become the only one.** The way to find
  out is to use it in the presets and see whether the check helps or nags.
  Going straight to required makes that experiment unrepeatable, which is why
  Q4 is what it is.
- **Whether a plain name should be reported anywhere.** A definition using the
  short form is not wrong, so it should not warn -- but a listing that showed
  which definitions carry a checked path would say how much the feature is
  actually used. Not built; worth knowing before deciding the point above.

## Corrections

**`--list` names the source; it does not print the reference form.** The plan
said it would, and what shipped is `csv_columns  (csv_profile)` rather than
`csv_profile::csv_columns`. The column is readable and tells you what to put on
the left, but it is not a line you can copy into a definition, which is what
"prints the reference form" promised. Worth knowing before reading
`profiler.yaml`, whose comment says `--list` prints the left-hand side *"so it
can be pasted"* — true of the source, not of the whole reference.

**The check landed in `catalogue.py` and `agent.py`, not `delegation.py`.**
`Offering.refuse_moved` is the rule and it lives in the domain; the two callers
are `warm()`, which checks every definition a catalogue holds, and the build
path, which checks the ones a request activated. Splitting it that way is why a
moved tool is caught both at startup and at the moment it would be used.

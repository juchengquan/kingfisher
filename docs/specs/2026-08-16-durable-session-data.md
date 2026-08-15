# Durable session data — spec

**Status:** implemented
**Date:** 2026-08-16

## The problem

There was no supported way to put a file into a session's `/data`.

`--input` exists, but it is explicitly *not* this. `Turn.input_dir`'s docstring says so: *"Never `/data`: they arrive fresh each round and leave with the turn."* Inputs land in `<session>/runs/<turn>/input/` and are invisible to the next turn.

`/data` is the durable tier — the one the system prompt describes as the caller's data, the one `protect_data` hardens. Nothing could write to it except `evals`, via `seed_sample_data`.

### This had already caused a real incident

A user with two PDFs to analyse had no flag for it, so they copied the files in by hand. `protect_data` had made `/data` read-only, so the copy was refused, so it was retried with `sudo`. That produced two root-owned files inside a workspace owned by uid 501.

`protect_data` then aborted on `chmod` → `EPERM`, and because it runs before everything else in `stream()`, it aborted **every subsequent run of that session**. One missing flag cost a bricked session and PR #20.

PR #20 stopped the brick. It did not remove the reason someone reached for `sudo`. This does.

## What it matches

`#22` established the shape for session-scoped provisioning: `Request` carries it, an adapter materialises it into the session before the turn exists, and refusals are specific and early. Durable data is the same kind of thing, differing only in that it comes from a **local path** rather than a `DefinitionStore` id.

## Design

### CLI

```
uv run main.py "Compare these" --data ~/Downloads/a.pdf --data ~/Downloads/b.pdf
uv run main.py --session 7f3a "and now the trend?"     # a.pdf and b.pdf still there
```

`--input` and `--data` differ only in lifetime, which is the only reason both exist. `--help` states it per flag:

```
--input PATH   a file for this turn only, in /runs/<turn>/input; repeatable
--data  PATH   a file kept for the whole session, in /data (read-only); repeatable
```

### Library

`Request.data: tuple[Path, ...]`, normalised in `__post_init__` exactly as `inputs` is.

Named `data` rather than `session_data`: it maps 1:1 to `/data`, the vocabulary the agent already uses, and the docstring carries the lifetime — which is how `inputs` already works.

### Where the copy happens

`workspace_fs.place_data(sources, session_dir) -> DataPlacement`, called from `Kingfisher.stream` after `ensure_session_layout` and `protect_data`, and **before** `provision`, the agent, and the turn directory.

It writes through `writable_data`, whose `finally` re-hardens `/data` — including when a copy raises. **No caller ever chmods `/data` by hand.** That is the behaviour that had to become impossible rather than merely discouraged.

### Rules

| Case | Behaviour |
|---|---|
| Path missing or not a file | Refuse, before anything is copied |
| Two sources with the same basename in one request | Refuse, naming both sources |
| Basename already in `/data` | Overwrite, and report it as replaced |
| Basename is empty, `.` or `..` | Refuse |

Everything is validated before anything is copied, so a request naming one bad file does not leave the good ones behind for a later turn to find.

### Reporting

Overwriting durable data silently is the one genuinely dangerous case, so it is named:

```
[data_placed] a.pdf, b.pdf (1 replaced)
```

The turn message also names what arrived, since the agent may already have looked at `/data` this session:

```
New files in /data: a.pdf, b.pdf.
```

In the turn message, never the system prompt — the same reason `turn.virtual_dir` is: the cached prefix must not move.

## Decisions, with reasons

**A local path, not a `DefinitionStore` ref.** Skills come from a reviewed catalogue and are fetched by id. Data is the caller's own file, and requiring an upload first would make the common case — "analyse this PDF on my disk" — the hard one.

**Files only; no directories.** Directories bring recursion, nested collisions, and the symlink-escape problem `uploads._write` had to solve. `--data dir/*.pdf` covers most of it through the shell.

**Copied, not linked.** Consistent with `inputs`, and it is what made the incident recoverable — the originals survived the workspace being trashed.

**Overwrite rather than refuse.** `--data` is the only supported way to write there, so refusing would make updating a dataset impossible without hand-editing a read-only directory — the exact trap this removes. The risk is a typo clobbering durable data, which is why replacement is reported rather than assumed.

**No size cap.** The motivating case is a 41 MB annual report, and nothing else in the workspace is capped.

## Not included

- **Removing a file from `/data`.** `/data` is read-only to the agent by design and sessions are reaped by `keep_runs` eventually. A `--forget NAME` flag is a plausible follow-up, but deletion of durable data deserves its own argument rather than being smuggled into the change that adds writing. Shipping "add" without "remove" is incomplete, not broken.
- Directory uploads.
- Listing `/data` from the CLI.

## Test plan, and what it is really checking

- **a supplied file is present on the *next* turn of the same session** — the property `--input` deliberately lacks, and the one that proves this is not `--input` with a different destination
- `/data` is read-only again after `place_data` returns, **and after it raises mid-copy** — `writable_data`'s `finally` is what makes this safe, and a happy-path-only test would not notice the copy moving outside the context manager
- `--data` and `--input` land in different places, and neither leaks into the other's
- two sources with one basename are refused, and nothing is written
- a missing path is refused before the session is touched
- re-supplying a changed file replaces it and reports it
- the turn message names the new files

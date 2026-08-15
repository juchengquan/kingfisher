# Phase 4: The artifact manifest — Implementation Plan

**Goal:** Tell the caller what a turn produced, so a session can be reaped without losing what it was for.

**Spec:** `docs/design/2026-08-16-session-scoped-api.md` (Decision 2)

## The open question, answered

The design doc left manifest granularity open — *"by mtime, by content hash, or by recording writes as they happen"* — and one fact rules out the third:

**`execute` bypasses the file tools.** `CompositeBackend` delegates execution to its default backend and routes only file-tool paths, so a script the agent runs writes without any tool seeing it. Running a script is how most of `/derived` gets produced. Recording writes at the tool layer would therefore miss precisely the writes the manifest exists for.

So it has to be a filesystem view. Between the two that remain:

- **Diff by mtime and size** — cheap, but a same-mtime same-size edit is a silent omission, and a silent omission here is lost work.
- **List what is present** — one walk, no hashing, and it cannot lose anything.

**Decision: list what is present**, at the end of each turn, under `/derived` and `/memory`, relative to the session root. A caller doing incremental persistence diffs against the previous turn's manifest, which it already holds — and that diff also tells it what was *deleted*, which a change-list would not.

This is a deviation: the spec said "the paths changed". It delivers "the paths present", because completeness is what the caller actually needs and change-detection is either unsound or expensive.

## Why these two directories

`/data` is read-only and came from the caller. `/runs` is scratch the prompt already describes as disposable. `/derived` and `/memory` are the two the agent is told will outlive the run, so they are exactly what a reaped session would lose.

## Tasks

1. `domain/layout.py`: `ARTIFACT_DIRS = ("derived", "memory")` — policy, beside `SESSION_DIRS`.
2. `adapters/workspace_fs.py`: `collect_artifacts(session_dir) -> tuple[str, ...]`, sorted, relative, files only.
3. `domain/result.py`: `RunResult.artifacts: tuple[str, ...] = ()`.
4. `app/service.py`: collect after the graph finishes, before the result is yielded.
5. `prompts/system.md`: say plainly that what is left in `/derived` is what comes back.

## Verification

```bash
uv run pytest -q && uv run ruff check . && uv run ty check
```

By hand: a turn that writes to `/derived` reports it; a turn that writes only to its run directory reports nothing; a second turn sees the first turn's artifact still listed.

# Phase 3: Uploaded definitions — Implementation Plan

**Goal:** Let a request bring its own skills and subagents, fetched by id from a catalogue service, without kingfisher knowing that service exists.

**Architecture:** A `DefinitionStore` port in `domain/ports.py`, injected at the entrypoint like `ThreadStore`. `Request` grows `skill_refs` and `subagent_refs` carrying ids; the service fetches them, materialises them into the session, and `build_agent` discovers them from disk beside the catalogue. Names collide loudly.

**Spec:** `docs/design/2026-08-16-session-scoped-api.md` (Decisions 4, 5, 6)

## Global Constraints

- `uv run pytest -q`, `uv run ruff check .`, `uv run ty check` all pass before every commit.
- `domain/` imports nothing foreign and nothing from `app/` or `adapters/`.
- Line length 100; exception messages assigned before `raise`.

## Shape

```
Request(skill_refs=("skl_a",), subagent_refs=("sub_b",))
   │
   ├─ DefinitionStore.fetch("skl_a") -> {"SKILL.md": b"...", "reference/x.md": b"..."}
   │     materialised at <session>/skills/uploaded/<name>/
   │     name comes from SKILL.md's frontmatter, because deepagents requires
   │     the directory to be named after the skill
   │
   └─ DefinitionStore.fetch("sub_b") -> {"reviewer.md": b"..."}
         materialised at <session>/subagents/<name>.md

routes:  /skills/           -> catalogue           (shared, read-only)
         /skills/uploaded/  -> <session>/skills/uploaded   (longer prefix wins)
sources: [("/skills/", "Catalogue"), ("/skills/uploaded/", "Uploaded")]
```

## Deviations from the spec

**1. Ids are plain strings, not a `SkillRef` record.** The spec sketched `SkillRef(id: str)`. A one-field frozen dataclass is ceremony, and `Request.inputs` is already a bare tuple. If a ref ever needs a version or an etag, promoting a `tuple[str, ...]` to a record is a contained change.

**2. One port, not two.** Skills and subagents differ in where they land, not in how they are fetched. `DefinitionStore.fetch(id) -> Mapping[str, bytes]` — "the files that make up this definition, by relative path" — covers a multi-file skill and a single-file subagent alike.

**3. Uploads are materialised, not held in memory.** The agent reads skills through the backend, so they have to exist on a filesystem it can route to. Subagents could be passed in memory, but writing both keeps `build_agent` disk-driven and side-effect-free, which is what it already is.

## Tasks

### Task 1: Frontmatter parsing, extracted

`domain/subagent.py` owns a frontmatter parser that skills now need too. Extract it to `domain/frontmatter.py` (`parse(text) -> tuple[dict[str, str], str] | None`), leaving each format to raise its own error. Add `domain/skill.py` with `name_of(text) -> str`, which is the rule that a skill's directory is named after its frontmatter.

### Task 2: The port and the request fields

`DefinitionStore` in `domain/ports.py`; `skill_refs` / `subagent_refs` on `Request`.

### Task 3: Materialising uploads

`adapters/uploads.py`: fetch each ref, work out its name, write it under the session. Refuse a name that collides with the catalogue, and refuse the reserved name `uploaded`.

### Task 4: Wiring

`/skills/uploaded/` route; two skill sources; `_available_skills` and `load_all` see catalogue plus uploads; `Kingfisher` takes a `definitions` collaborator and materialises before building the agent.

## Verification

```bash
uv run pytest -q && uv run ruff check . && uv run ty check
```

Then by hand: a request with one uploaded skill sees it, a second session does not, and a collision with the catalogue is refused.

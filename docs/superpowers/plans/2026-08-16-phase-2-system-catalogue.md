# Phase 2: System catalogue — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Let the skill and subagent definitions live anywhere on the host, so one reviewed catalogue can serve every workspace instead of a copy per workspace that nobody can audit centrally.

**Architecture:** Two new roots on `Config` — `skills_dir` and `subagents_dir` — read through properties in the same style as `state_dir` and `scratch_dir`. Both default to their current location inside the workspace, so nothing changes unless you point them elsewhere. `/skills/` routes to `skills_dir`; `load_all` is handed a directory instead of deriving one.

**Spec:** `docs/design/2026-08-16-session-scoped-api.md` (Decision 3)

## Global Constraints

- `uv run pytest -q`, `uv run ruff check .` and `uv run ty check` must all pass before every commit.
- `domain/` imports nothing foreign and nothing from outer layers; `adapters/` imports nothing from `app/`. Enforced by `tests/test_architecture.py`.
- Line length 100; exception messages assigned to a variable before `raise`.

## Deviations from the design doc

Recorded here rather than silently: the spec was written before Phase 1 landed.

**1. Defaults are the workspace, not package-bundled.** The spec said the catalogue defaults to directories bundled in the package. There is no bundled catalogue, and inventing skill content is not what this phase is for. Defaulting to `<workspace>/skills` and `<workspace>/subagents` preserves today's behaviour exactly, and the delta this phase actually delivers is that the catalogue *can* now live outside the workspace at all.

**2. `/skills/` keeps its path; `/skills/system/` is dropped.** The spec split the virtual path into `/skills/system/` and `/skills/uploaded/`. Phase 3 can route `/skills/uploaded/` as a *longer* prefix underneath `/skills/` — `CompositeBackend` matches longest-first — so uploads land without renaming the system path. Renaming now would be agent-visible churn for a benefit that does not exist until uploads do. The one cost: a system skill named `uploaded` would shadow the route, which Phase 3 must reject.

**3. No caching.** The spec called for parsing the catalogue once per process. Two reasons not to. The spec's own analysis says so — *"'Loaded once' is not the win… the reason to centralise is provenance"* — and `app/service.py` deliberately rebuilds the agent per request because *"a cached one would serve a stale view of a directory the user can edit between turns."* Caching would contradict a recent, deliberate decision to buy a few milliseconds against a model call of seconds. Keying a cache on directory mtime would not even be sound: editing a file inside a directory does not change the directory's mtime.

---

### Task 1: Two configurable catalogue roots

**Files:**
- Modify: `src/kingfisher/config.py` (fields beside `state_root`, properties beside `state_dir`)
- Modify: `src/kingfisher/app/config.py` (`from_env`)
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `Config.skills_root: Path | None`, `Config.subagents_root: Path | None`; `Config.skills_dir -> Path`, `Config.subagents_dir -> Path`. Env: `KINGFISHER_SKILLS_DIR`, `KINGFISHER_SUBAGENTS_DIR`.

- [ ] **Step 1: Write the failing tests**

```python
def test_the_catalogue_defaults_inside_the_workspace():
    """Unset changes nothing: definitions stay where they have always been."""
    cfg = from_env({**BASE_ENV, "KINGFISHER_API_STYLE": "anthropic"})

    assert cfg.skills_dir == cfg.workspace / "skills"
    assert cfg.subagents_dir == cfg.workspace / "subagents"


def test_the_catalogue_can_be_shared_between_workspaces(tmp_path):
    """The point of the phase: one reviewed set, not a copy per workspace."""
    cfg = from_env({
        **BASE_ENV,
        "KINGFISHER_API_STYLE": "anthropic",
        "KINGFISHER_SKILLS_DIR": str(tmp_path / "catalogue" / "skills"),
        "KINGFISHER_SUBAGENTS_DIR": str(tmp_path / "catalogue" / "subagents"),
    })

    assert cfg.skills_dir == tmp_path / "catalogue" / "skills"
    assert cfg.subagents_dir == tmp_path / "catalogue" / "subagents"
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_config.py -k catalogue -v`, expected `AttributeError: 'Config' object has no attribute 'skills_dir'`.

- [ ] **Step 3: Implement** — add the two fields and two properties to `config.py`, and two `_optional_path` reads to `from_env`.

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Commit.**

---

### Task 2: Read the catalogue from those roots

**Files:**
- Modify: `src/kingfisher/adapters/subagent_store.py` (`load_all` takes the directory)
- Modify: `src/kingfisher/adapters/agent.py:62-66, 271, 289`
- Modify: `src/kingfisher/adapters/backend.py` (`/skills/` route)
- Test: `tests/test_capability_wiring.py`, `tests/test_subagent.py`, `tests/test_examples.py`

**Interfaces:**
- Consumes: `Config.skills_dir`, `Config.subagents_dir` from Task 1.
- Produces: `load_all(directory: Path) -> dict[str, SubagentSpec]` — the directory itself, not its parent. `_available_skills(directory: Path)` likewise.

- [ ] **Step 1: Write the failing test**

```python
def test_skills_and_subagents_come_from_the_catalogue_not_the_workspace(cfg, session_dir, tmp_path):
    """A catalogue outside the workspace is what lets one set serve many."""
    catalogue = tmp_path / "catalogue"
    (catalogue / "skills" / "shared").mkdir(parents=True)
    (catalogue / "skills" / "shared" / "SKILL.md").write_text(
        "---\nname: shared\ndescription: A shared procedure.\n---\nBody.\n"
    )
    relocated = replace(cfg, skills_root=catalogue / "skills", skills_enabled=True)

    backend = build_backend(relocated, session_dir)

    assert str(backend.routes["/skills/"].cwd) == str((catalogue / "skills").resolve())
    assert backend.read("/skills/shared/SKILL.md")
```

- [ ] **Step 2: Run to verify it fails.**

- [ ] **Step 3: Implement** — route `/skills/` at `cfg.skills_dir`; pass `cfg.skills_dir` to `_available_skills` and `cfg.subagents_dir` to `load_all`; change `load_all` to read the directory it is given.

- [ ] **Step 4: Run the whole suite** — call sites in `test_examples.py` become `load_all(EXAMPLES / "subagents")`.

- [ ] **Step 5: Commit.**

---

## Verification

```bash
uv run pytest -q && uv run ruff check . && uv run ty check
```

Then prove the phase's point by hand — two workspaces, one catalogue:

```bash
uv run python -c "
import tempfile, pathlib
from dataclasses import replace
from kingfisher.config import Config
from kingfisher.adapters.workspace_fs import ensure_layout, ensure_session_layout
from kingfisher.adapters.backend import build_backend

root = pathlib.Path(tempfile.mkdtemp())
cat = root / 'catalogue' / 'skills'
(cat / 'shared').mkdir(parents=True)
(cat / 'shared' / 'SKILL.md').write_text('---\nname: shared\ndescription: d\n---\nb\n')

for name in ('ws-a', 'ws-b'):
    ws = ensure_layout(root / name)
    cfg = Config(workspace=ws, api_style='anthropic', base_url='u', api_key='k',
                 model='m', skills_root=cat)
    s = ensure_session_layout(ws / 'sessions' / 's')
    print(name, 'sees the shared skill:', bool(build_backend(cfg, s).read('/skills/shared/SKILL.md')))
"
```

Expected: `True` for both.

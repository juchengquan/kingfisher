# Phase 1: Session-rooted workspace — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move `/data`, `/derived`, `/memory` and `/runs` under a per-session directory, so every virtual path resolves inside one session and the agent never sees a session id.

**Architecture:** The backend roots at `<workspace>/sessions/<session_id>/` instead of `<workspace>/`. Nothing about the agent's vocabulary changes — `/data` still means `/data` — so the system prompt stays byte-identical across sessions and prompt caching keeps hitting. `/runs/<session>/<turn>` collapses to `/runs/<turn>` because the session is implicit in the root.

**Tech Stack:** Python 3.12, deepagents 0.7.6, pytest, ruff, ty. Package managed with `uv`.

**Spec:** `docs/design/2026-08-16-session-scoped-api.md`

## Global Constraints

- Run `uv run pytest -q`, `uv run ruff check .` and `uv run ty check` before every commit. All three must pass; `ty` is configured with `error-on-warning = true`.
- Layer rules are enforced by `tests/test_architecture.py`: `domain/` imports no `langchain`/`langgraph`/`deepagents` and nothing from `app/` or `adapters/`; `adapters/` imports nothing from `app/`.
- Line length 100. Exception messages must be assigned to a variable before `raise` (ruff `EM`).
- Docstrings explain *why*, not *what* — match the density of the surrounding code.
- One conceptual change per commit.

---

## Rebased onto the DDD restructure (#11, #13, #14, #15)

This plan was written against the pre-restructure layout. Main has since moved
five commits and the tasks below are corrected accordingly. What moved:

| Was | Is now |
|---|---|
| `domain/workspace.py` | split: `domain/layout.py` (policy, as data) + `adapters/workspace_fs.py` (mkdir, chmod) + `adapters/workspace_git.py` |
| `domain/config.py` | `kingfisher/config.py` — package root, neither domain nor app |
| wiring inside `stream()` | `app/service.py`: `Kingfisher(cfg)`, wired once, with module-level `run`/`stream` over a default instance |
| — | `domain/ports.py`: `ThreadStore`, `SessionDirs` Protocols |
| — | `domain/retention.py`: `plan()` decides, `apply()` acts |
| — | `adapters/scoping.py`, `adapters/subagent_store.py` |

Three consequences for this phase:

1. **`Session.open(workspace, session_id, dirs)` and `allocate_turn(dirs, …)`
   now take a `SessionDirs` port.** Tasks 1 and 2 keep those parameters; only
   the paths change.
2. **`Kingfisher.__init__` hoists `ensure_layout(cfg.workspace)`.** Layout is
   per-session now, so it moves back onto the request path. That is a partial
   reversal of #15's hoisting — but #15 hoisted *wiring* (checkpointer, ports),
   and this is one directory call whose input is not known until the request
   names its session.
3. **Retention points at `workspace/runs`.** Sessions move to
   `workspace/sessions`, so `Kingfisher.stream`'s `retention.plan(...)` must
   follow them or it will sweep nothing. Phase 5 removes it from the request
   path entirely; Task 5 here only keeps it pointing at the right directory.

Decision 6's `SkillStore` port now has an obvious home — `domain/ports.py`,
alongside `ThreadStore` — which is Phase 3, not this one.

---

### Task 1: Session directory location

**Files:**
- Modify: `src/kingfisher/domain/session.py:63-67`
- Test: `tests/test_session.py`

**Interfaces:**
- Produces: `Session.open(workspace: Path, session_id: str) -> Session` — unchanged signature, now creating `<workspace>/sessions/<session_id>/` rather than `<workspace>/runs/<session_id>/`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_session_lives_under_sessions_not_runs(tmp_path):
    """The session directory is the backend root, so it holds the whole
    workspace vocabulary — not just that session's runs."""
    session = Session.open(tmp_path, "s1")

    assert session.directory == tmp_path / "sessions" / "s1"
    assert session.directory.is_dir()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session.py::test_a_session_lives_under_sessions_not_runs -v`
Expected: FAIL — `assert PosixPath('.../runs/s1') == PosixPath('.../sessions/s1')`

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/domain/session.py`, change `Session.open`:

```python
    @classmethod
    def open(cls, workspace: Path, session_id: str) -> Session:
        directory = Path(workspace) / "sessions" / session_id
        directory.mkdir(parents=True, exist_ok=True)
        return cls(id=session_id, directory=directory)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session.py -v`
Expected: PASS. Other tests in this file may fail — they are fixed in Task 2.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/domain/session.py tests/test_session.py
git commit -m "Move session directories under sessions/"
```

---

### Task 2: Turns live under the session's runs/

**Files:**
- Modify: `src/kingfisher/domain/session.py:32-53, 69-98`
- Test: `tests/test_session.py`

**Interfaces:**
- Consumes: `Session.open` from Task 1.
- Produces: `Session.runs_dir -> Path` (`<session>/runs`); `Turn.directory` is `<session>/runs/<turn_id>`; `Turn.virtual_dir -> str` returns `/runs/<turn_id>` with no session segment; `Turn.virtual_input_dir -> str` returns `/runs/<turn_id>/input`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_turn_is_addressed_without_its_session(tmp_path):
    """The session is the backend root, so naming it in a virtual path would
    be addressing outside the root — and would put the session id into the
    prompt, changing the cached prefix on every session."""
    turn = Session.open(tmp_path, "s1").allocate_turn()

    assert turn.id == "t001"
    assert turn.directory == tmp_path / "sessions" / "s1" / "runs" / "t001"
    assert turn.virtual_dir == "/runs/t001"
    assert turn.virtual_input_dir == "/runs/t001/input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_session.py::test_a_turn_is_addressed_without_its_session -v`
Expected: FAIL — `turn.directory` is `.../sessions/s1/t001` (no `runs/`), and `virtual_dir` is `/runs/s1/t001`.

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/domain/session.py`, change `Turn.virtual_dir`:

```python
    @property
    def virtual_dir(self) -> str:
        """The directory as the agent addresses it.

        No session segment: the session directory *is* the backend root, so
        the agent has no name for it and cannot address outside it.
        """
        return f"/runs/{self.id}"
```

Add a `runs_dir` property to `Session` and use it in `allocate_turn`:

```python
    @property
    def runs_dir(self) -> Path:
        """Turn directories. Distinct from the session root, which now also
        holds `data`, `derived`, `memory` and `skills`."""
        return self.directory / "runs"
```

In `allocate_turn`, replace every `self.directory` with `self.runs_dir`, and create it first:

```python
    def allocate_turn(self, turn_id: str | None = None) -> Turn:
        """Create the next turn's directory and return it.

        A caller-supplied id wins and is idempotent: the same id returns the
        same directory, so a retried request reuses its turn rather than
        forking a second one. A service should pass its own request id — only
        the caller knows where the request boundary is.

        Otherwise the next sequential id is allocated by `mkdir`, which fails
        if the name is taken. Scanning for the highest id and *then* creating
        it is the race this avoids.
        """
        runs = self.runs_dir
        runs.mkdir(parents=True, exist_ok=True)

        if turn_id:
            path = runs / turn_id
            path.mkdir(exist_ok=True)
            return Turn(session_id=self.id, id=turn_id, directory=path)

        existing = [p.name for p in runs.iterdir() if p.is_dir()]
        number = max(
            (int(n[1:]) for n in existing if n.startswith("t") and n[1:].isdigit()),
            default=0,
        )
        while True:
            number += 1
            candidate = runs / f"t{number:03d}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue  # lost the race for this id; take the next one
            return Turn(session_id=self.id, id=candidate.name, directory=candidate)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_session.py -v`
Expected: PASS for all tests in the file. Fix any remaining assertions that hardcode the old `runs/<session>/<turn>` shape.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/domain/session.py tests/test_session.py
git commit -m "Nest turns under the session's runs/, and drop the session from virtual paths"
```

---

### Task 3: The session layout replaces the workspace layout

**Files:**
- Modify: `src/kingfisher/domain/workspace.py:44-56, 118-139`
- Test: `tests/test_workspace.py`

**Interfaces:**
- Produces: `ensure_session_layout(session_dir: Path) -> Path` — creates `data`, `derived`, `memory`, `runs` and `skills/uploaded` inside a session directory and seeds `memory/AGENTS.md`. Replaces `ensure_layout`.

Note: `skills` and `subagents` leave `LAYOUT_DIRS` — the system catalogue arrives in Phase 2, and `skills/uploaded` is the session's own.

- [ ] **Step 1: Write the failing test**

```python
def test_a_session_gets_the_whole_vocabulary(tmp_path):
    """Everything the agent addresses is inside one session directory."""
    session = ensure_session_layout(tmp_path / "sessions" / "s1")

    for name in ("data", "derived", "memory", "runs", "skills/uploaded"):
        assert (session / name).is_dir(), name


def test_memory_is_scaffolded_not_empty(tmp_path):
    """The memory prompt tells the agent to save with `edit_file`, which
    replaces existing text — an empty file offers nothing to anchor against."""
    session = ensure_session_layout(tmp_path / "sessions" / "s1")

    assert "Project memory" in (session / "memory" / "AGENTS.md").read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_workspace.py::test_a_session_gets_the_whole_vocabulary -v`
Expected: FAIL with `ImportError: cannot import name 'ensure_session_layout'`

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/domain/workspace.py`, replace `LAYOUT_DIRS` and `ensure_layout`:

```python
#: Created inside every session directory. `skills/uploaded` is this session's
#: own; the system catalogue lives outside the workspace entirely.
SESSION_DIRS: tuple[str, ...] = (
    "data",
    "derived",
    "memory",
    "runs",
    "skills/uploaded",
)


def ensure_session_layout(session_dir: Path) -> Path:
    """Create one session's layout. Idempotent.

    This is the backend root, so it holds the whole vocabulary the agent
    addresses. There is no workspace-level layout any more: two sessions share
    a parent directory and nothing else.
    """
    session_dir = Path(session_dir).expanduser().resolve()
    for name in SESSION_DIRS:
        (session_dir / name).mkdir(parents=True, exist_ok=True)

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = session_dir / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return session_dir
```

Delete `ensure_layout`, `MARKER`, `is_new_workspace`, `WORKSPACE_GITIGNORE` and `TRACKED_PATHS`, plus `pre_run_commit`, `is_repo` and `ensure_repo`. The git tier described a durable project directory; a session directory that the caller reaps has nothing to restore to. Remove the corresponding tests in `tests/test_workspace.py`.

`tests/conftest.py:7,45-46` imports `ensure_layout` for its `workspace` fixture. The workspace is now just a parent directory with no layout of its own, so replace it:

```python
@pytest.fixture
def workspace(tmp_path):
    """Only a parent for session directories now — it has no layout itself."""
    path = tmp_path / "ws"
    path.mkdir()
    return path
```

Remove the `from kingfisher.domain.workspace import ensure_layout` import with it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_workspace.py -v`
Expected: PASS. `tests/test_run.py` and `tests/test_smoke.py` will fail — they are fixed in Task 5.

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/domain/workspace.py tests/test_workspace.py
git commit -m "Give each session the whole layout, and drop the workspace git tier"
```

---

### Task 4: The backend roots at the session

**Files:**
- Modify: `src/kingfisher/adapters/backend.py:56-100`
- Test: `tests/test_backend.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `build_backend(cfg: Config, session_dir: Path) -> BackendProtocol` — a second positional parameter. `WorkspaceScopedBackend.workspace` becomes the session root, so the host-path guard from PR #10 now rejects paths under the session directory too.

- [ ] **Step 1: Write the failing test**

```python
def test_the_backend_roots_at_the_session_not_the_workspace(cfg, tmp_path):
    """One line decides the whole model: every virtual path resolves inside
    the session, so /data means this session's data."""
    session = tmp_path / "sessions" / "s1"
    session.mkdir(parents=True)

    backend = build_backend(cfg, session)

    assert str(backend.default.cwd) == str(session.resolve())
    assert str(backend.routes["/data/"].cwd) == str((session / "data").resolve())


def test_two_sessions_cannot_reach_each_other(cfg, tmp_path):
    """Isolation is structural: there is no path from one root to the other."""
    first, second = tmp_path / "sessions" / "a", tmp_path / "sessions" / "b"
    for path in (first, second):
        (path / "data").mkdir(parents=True)

    build_backend(cfg, first).write("/derived/note.md", "from a")

    assert (first / "derived" / "note.md").is_file()
    assert not (second / "derived" / "note.md").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_backend.py::test_the_backend_roots_at_the_session_not_the_workspace -v`
Expected: FAIL with `TypeError: build_backend() takes 1 positional argument but 2 were given`

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/adapters/backend.py`, change `build_backend`:

```python
def build_backend(cfg: Config, session_dir: Path) -> BackendProtocol:
    """Build the backend rooted at one session.

    `virtual_mode` is left at its default (`True`), so file tools address
    virtual paths anchored to this session and `..` / `~` are blocked. The
    session directory being the root is what makes `/data` mean the same thing
    in every session while pointing somewhere different in each — and what
    makes cross-session access structurally impossible rather than merely
    denied.

    A `CompositeBackend` is required rather than merely convenient.
    `FilesystemMiddleware` refuses `permissions=` outright when the backend
    supports execution — unless every rule path is scoped to a route. Routing
    `/data/` to its own backend is what makes the write-deny rule legal while
    `execute` still works, because CompositeBackend delegates execution to its
    default backend.
    """
    prepare_scratch(cfg)
    (session_dir / "data").mkdir(parents=True, exist_ok=True)

    shell = LocalShellBackend(
        root_dir=str(session_dir),
        env=shell_env(cfg),
        timeout=cfg.timeout_s,
    )
    return WorkspaceScopedBackend(
        default=shell,
        routes={DATA_ROUTE: FilesystemBackend(root_dir=str(session_dir / "data"))},
        workspace=session_dir,
    )
```

Remove `SKILLS_ROUTE` and its route entry — the skills route is rebuilt in Phase 2 against the system catalogue. Delete `test_skills_is_routed_for_the_same_reason` in `tests/test_backend.py`; Phase 2 replaces it.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_backend.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/adapters/backend.py tests/test_backend.py
git commit -m "Root the backend at the session directory"
```

---

### Task 5: Wire the session through the run

**Files:**
- Modify: `src/kingfisher/app/run.py:83-100, 130-140`
- Modify: `src/kingfisher/adapters/agent.py:201-220`
- Test: `tests/test_run.py`

**Interfaces:**
- Consumes: `Session.open` (Task 1), `ensure_session_layout` (Task 3), `build_backend(cfg, session_dir)` (Task 4).
- Produces: `build_agent(cfg, *, session_dir: Path | None = None, …)` — builds its own backend from `session_dir` when no backend is injected.

- [ ] **Step 1: Write the failing test**

```python
def test_a_run_writes_inside_its_own_session(cfg):
    """The whole point: two sessions share a workspace and nothing else."""
    agent = StubAgent("done")
    result = run(Request("t", session_id="s1"), cfg=cfg, agent=agent,
                 checkpointer=StubCheckpointer())

    session = cfg.workspace / "sessions" / "s1"
    assert result.run_dir == session / "runs" / "t001"
    assert (session / "data").is_dir()
    assert not (cfg.workspace / "data").exists()


def test_the_task_message_names_the_turn_without_the_session(cfg):
    """A session id in the message would change the prompt every session."""
    agent = StubAgent("done")
    run(Request("profile it", session_id="s1"), cfg=cfg, agent=agent,
        checkpointer=StubCheckpointer())

    # StubAgent records what it was streamed as `self.state`.
    message = agent.state["messages"][0]["content"]
    assert "/runs/t001" in message
    assert "s1" not in message
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_run.py::test_a_run_writes_inside_its_own_session -v`
Expected: FAIL — `run_dir` is `<workspace>/runs/s1/t001`, and `<workspace>/data` exists.

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/app/run.py`, replace the workspace setup in `stream()`:

```python
    session_id = request.session_id or new_session_id()
    session = Session.open(cfg.workspace, session_id)
    ensure_session_layout(session.directory)
    protect_data(session.directory)  # kernel-level; the deny rule covers only file tools
    checkpointer = checkpointer if checkpointer is not None else build_checkpointer(cfg)
```

Delete the `sweep(...)` call and the `pre_run_commit(...)` call along with the `swept` and `commit` values they produced; pass `swept=()` and `commit=None` to `RunResult` for now. Phase 5 removes those fields.

Pass the session directory when building the agent:

```python
    graph = agent if agent is not None else build_agent(
        cfg,
        capabilities=request.capabilities,
        session_dir=session.directory,
        checkpointer=checkpointer,
    )
```

In `src/kingfisher/adapters/agent.py`, change `build_agent`'s signature and backend resolution:

```python
def build_agent(
    cfg: Config,
    *,
    capabilities: Capabilities | None = None,
    session_dir: Path | None = None,
    model: Any | None = None,
    backend: Any | None = None,
    checkpointer: Any | None = None,
) -> CompiledStateGraph:
```

```python
    if backend is not None:
        resolved_backend = backend
    elif session_dir is not None:
        resolved_backend = build_backend(cfg, session_dir)
    else:
        msg = "build_agent needs either a backend or a session_dir"
        raise ValueError(msg)
```

`turn.virtual_dir` already returns `/runs/<turn>` from Task 2, so the task message at `run.py:135` needs no change.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest -q`
Expected: PASS. Update `tests/test_smoke.py` and `main.py` for the new `run_dir` shape; `seed_sample_data` now writes into a session's `data/`.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "Run each request inside its own session directory"
```

---

### Task 6: Tell the agent what it can see

**Files:**
- Modify: `src/kingfisher/prompts/system.md:8-19`
- Test: `tests/test_prompt.py`

**Interfaces:**
- Consumes: `Turn.virtual_dir` (Task 2).

- [ ] **Step 1: Write the failing test**

```python
def test_the_prompt_does_not_promise_cross_session_durability():
    """/derived survives the run, not the session — saying otherwise would be
    a promise the manifest has to keep, and Phase 4 has not built it yet."""
    prompt = system_prompt()

    assert "/derived" in prompt
    assert "survives between sessions" not in prompt
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_prompt.py::test_the_prompt_does_not_promise_cross_session_durability -v`
Expected: FAIL — `system.md:11` currently says `/derived` "survives between sessions and is never swept".

- [ ] **Step 3: Write minimal implementation**

In `src/kingfisher/prompts/system.md`, replace the `/derived` bullet:

```markdown
- `/derived` — everything you produce that should outlive this run: cleaned data,
  fitted models, caches, written findings. It is collected when the session ends;
  anything left elsewhere is not. There is no separate place for reports, so
  whatever should be kept goes here, whatever it is called.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_prompt.py -v && uv run pytest -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/kingfisher/prompts/system.md tests/test_prompt.py
git commit -m "Stop promising the agent durability the session model cannot give"
```

---

## Verification

After Task 6, all three checks must pass:

```bash
uv run pytest -q      # every test
uv run ruff check .   # lint
uv run ty check       # types
```

Then verify the model change by hand, since the tests use a fake agent:

```bash
uv run python -c "
from pathlib import Path
from kingfisher.domain.session import Session
from kingfisher.domain.workspace import ensure_session_layout
from kingfisher.adapters.backend import build_backend
from kingfisher.domain.config import Config
import tempfile

ws = Path(tempfile.mkdtemp())
cfg = Config(workspace=ws, api_style='anthropic', base_url='u', api_key='k', model='m')
a = ensure_session_layout(Session.open(ws, 'a').directory)
b = ensure_session_layout(Session.open(ws, 'b').directory)
build_backend(cfg, a).write('/derived/note.md', 'from a')
print('a sees it :', (a / 'derived' / 'note.md').is_file())
print('b does not:', not (b / 'derived' / 'note.md').exists())
"
```

Expected: both `True`.

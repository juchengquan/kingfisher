"""Workspace layout, git bookkeeping, and run-directory retention.

The workspace splits by lifetime, not by session:

  durable, shared   /data /derived /skills /memory /reports
  per-session       /runs/<session_id>/          transcript, and its turns
  per-turn          /runs/<session_id>/<turn>/   this request's inputs, scratch, answer
  harness-owned     /.kingfisher/   (thread db, run logs, tmp)

A turn is one request. Its inputs are supplied fresh each time and are not
project data; its conclusions are durable. Nesting turns inside their session
means expiring a conversation takes its turns with it, with no lookup, while
expiring stale inputs never touches the conversation.

and by durability, which is what makes retention safe:

  tracked in git    /reports, /skills, /memory, runs/*/*/report.md, runs/*/*/result.json
  ignored, swept    everything else under runs/*/
  ignored, kept     /data, /derived, /.kingfisher
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

LAYOUT_DIRS: tuple[str, ...] = (
    "data",
    "derived",
    "skills",
    "memory",
    "reports",
    "runs",
    ".kingfisher/runs",
    ".kingfisher/tmp",
)

MARKER = ".kingfisher/WORKSPACE"

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

# Tracked-tier paths. `pre_run_commit` stages only these, never `git add -A`,
# so pointing kingfisher at a directory that already holds unrelated work
# cannot sweep that work into a commit.
TRACKED_PATHS: tuple[str, ...] = (
    ".gitignore",
    "PROMPT.md",
    "reports",
    "skills",
    "memory",
    "runs",
)

WORKSPACE_GITIGNORE = """\
# Managed by kingfisher. Durability tiers, not preferences.

# Inputs: irreplaceable, never committed (and write-denied at the tool level).
data/

# Derived: regenerable but expensive. Never committed, never swept.
derived/

# Harness state: thread db, run logs, tmp. Local-only by design.
.kingfisher/

# Run output: conclusions are tracked, everything else is disposable scratch.
# Two levels: runs/<session>/<turn>/. Every parent must be re-included or git
# never descends far enough to see the negated files.
runs/**
!runs/
!runs/*/
!runs/*/*/
!runs/*/*/report.md
!runs/*/*/result.json
"""


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[str, ...]
    kept: int


def is_new_workspace(workspace: Path) -> bool:
    """True when this path has never been used as a workspace.

    Surfaced by callers so a silently relocated workspace — an unstable `~`,
    a changed env var — reads as "created new" rather than as a first run.
    """
    return not (Path(workspace) / MARKER).exists()


def ensure_layout(workspace: Path) -> Path:
    """Create the workspace layout and its .gitignore. Idempotent."""
    workspace = Path(workspace).expanduser().resolve()
    for name in LAYOUT_DIRS:
        (workspace / name).mkdir(parents=True, exist_ok=True)

    gitignore = workspace / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(WORKSPACE_GITIGNORE, encoding="utf-8")

    marker = workspace / MARKER
    if not marker.exists():
        marker.write_text("kingfisher workspace\n", encoding="utf-8")

    # Scaffolded rather than empty: the memory prompt directs the agent to save
    # knowledge with `edit_file`, which replaces existing text — an empty file
    # offers nothing to anchor against.
    agents_md = workspace / "memory" / "AGENTS.md"
    if not agents_md.exists() or not agents_md.read_text(encoding="utf-8").strip():
        agents_md.write_text(AGENTS_SCAFFOLD, encoding="utf-8")

    return workspace


def _drop_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode & ~0o222)


def _add_write_bits(path: Path) -> None:
    path.chmod(path.stat().st_mode | 0o200)


def protect_data(workspace: Path) -> None:
    """Make `data/` read-only at the OS level. Idempotent.

    This is the layer the tool-level deny rule cannot provide: the kernel
    enforces it against `execute` too, and filesystem permissions in deepagents
    are applied only to the built-in file tools. Directories are included
    because deletion is governed by the *directory's* write bit, not the file's.
    """
    data = Path(workspace) / "data"
    if not data.is_dir():
        return
    for path in sorted(data.rglob("*"), reverse=True):
        _drop_write_bits(path)
    _drop_write_bits(data)


@contextmanager
def writable_data(workspace: Path) -> Iterator[Path]:
    """Temporarily make `data/` writable, for loading inputs.

        with writable_data(ws) as data:
            shutil.copy(source, data / "sales.csv")
    """
    data = Path(workspace) / "data"
    data.mkdir(parents=True, exist_ok=True)
    _add_write_bits(data)
    for path in data.rglob("*"):
        _add_write_bits(path)
    try:
        yield data
    finally:
        protect_data(workspace)


def session_dir(workspace: Path, session_id: str) -> Path:
    """Absolute path of a session's directory, which contains its turns."""
    return Path(workspace) / "runs" / session_id


def run_dir(workspace: Path, session_id: str, turn_id: str) -> Path:
    """Absolute path of one turn's directory (host-side)."""
    return session_dir(workspace, session_id) / turn_id


def allocate_turn_dir(
    workspace: Path,
    session_id: str,
    turn_id: str | None = None,
) -> tuple[str, Path]:
    """Create this turn's directory and return `(turn_id, path)`.

    A caller-supplied `turn_id` wins, and is idempotent: handing back the same
    id returns the same directory, so a retried request does not fork a second
    turn. Services should pass their own request id — it is the only way the
    turn boundary can match the request boundary.

    The fallback allocates the next sequential id — `t001`, `t002` — so a
    conversation reads in order on disk. Allocation is done by `mkdir` rather
    than by scanning and then creating: `mkdir` fails if the name is taken, so
    two concurrent callers cannot both decide they are `t001`. Scanning first
    and creating second is precisely that race.
    """
    parent = session_dir(workspace, session_id)
    parent.mkdir(parents=True, exist_ok=True)

    if turn_id:
        path = parent / turn_id
        path.mkdir(exist_ok=True)
        return turn_id, path

    existing = [p.name for p in parent.iterdir() if p.is_dir()]
    number = max(
        (int(n[1:]) for n in existing if n.startswith("t") and n[1:].isdigit()),
        default=0,
    )
    while True:
        number += 1
        candidate = parent / f"t{number:03d}"
        try:
            candidate.mkdir()
        except FileExistsError:
            continue  # lost the race for this id; take the next one
        return candidate.name, candidate


def virtual_run_dir(session_id: str, turn_id: str) -> str:
    """The same directory as the agent sees it — virtual, machine-independent."""
    return f"/runs/{session_id}/{turn_id}"


def virtual_input_dir(session_id: str, turn_id: str) -> str:
    """Where files supplied with this request are mounted, as the agent sees it."""
    return f"{virtual_run_dir(session_id, turn_id)}/input"


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(workspace), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def is_repo(workspace: Path) -> bool:
    return _git(workspace, "rev-parse", "--git-dir").returncode == 0


def ensure_repo(workspace: Path) -> bool:
    """Initialise a git repo in the workspace if there isn't one."""
    if is_repo(workspace):
        return True
    return _git(workspace, "init").returncode == 0


def pre_run_commit(workspace: Path, message: str) -> str | None:
    """Commit the tracked tier before a run, so there is always a restore point.

    Returns the commit sha, or None if there was nothing to commit (or git is
    unavailable / has no identity configured — a missing commit is not worth
    failing a run over).
    """
    if not ensure_repo(workspace):
        return None

    existing = [p for p in TRACKED_PATHS if (workspace / p).exists()]
    if not existing:
        return None

    _git(workspace, "add", "--", *existing)
    if _git(workspace, "diff", "--cached", "--quiet").returncode == 0:
        return None  # nothing staged

    if _git(workspace, "commit", "-m", message).returncode != 0:
        return None
    return _git(workspace, "rev-parse", "HEAD").stdout.strip() or None


def sweep(workspace: Path, keep: int, checkpointer=None) -> SweepResult:
    """Keep the `keep` most recent run directories; delete the rest.

    A swept session loses its directory *and* its thread together, so the
    checkpointer can never reference files that no longer exist — resuming a
    swept session fails cleanly instead of producing an agent that cites
    deleted paths.
    """
    runs = workspace / "runs"
    if not runs.is_dir() or keep < 0:
        return SweepResult(removed=(), kept=0)

    dirs = sorted(
        (p for p in runs.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    doomed = dirs[keep:]

    removed: list[str] = []
    for path in doomed:
        session_id = path.name
        shutil.rmtree(path, ignore_errors=True)
        if checkpointer is not None:
            try:
                checkpointer.delete_thread(session_id)
            except Exception:  # noqa: BLE001 -- housekeeping must not fail a run
                pass
        removed.append(session_id)

    return SweepResult(removed=tuple(removed), kept=len(dirs) - len(removed))

"""Workspace layout, git bookkeeping, and run-directory retention.

The workspace splits by lifetime, not by session:

  durable, shared   /data /derived /skills /subagents /memory
  per-session       /runs/<session_id>/          transcript, and its turns
  per-turn          /runs/<session_id>/<turn>/   this request's inputs, scratch, answer
  harness-owned     /.kingfisher/   (thread db, run logs, tmp)

A turn is one request. Its inputs are supplied fresh each time and are not
project data; its conclusions are durable. Nesting turns inside their session
means expiring a conversation takes its turns with it, with no lookup, while
expiring stale inputs never touches the conversation.

and by durability, which is what makes retention safe

  tracked in git    /skills, /subagents, /memory, PROMPT.md
  ignored, swept    everything under runs/*/
  ignored, kept     /data, /derived, /.kingfisher

Git tracks what a person authored; `/derived` holds what the agent produced and
wants to keep. There is no directory for reports, because "a report" is one
kind of output among many -- anything a run should outlive goes to `/derived`,
whatever it is called.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kingfisher.domain.session import Session

LAYOUT_DIRS: tuple[str, ...] = (
    "data",
    "derived",
    "skills",
    "subagents",
    "memory",
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
    "skills",
    "subagents",
    "memory",
)

WORKSPACE_GITIGNORE = """\
# Managed by kingfisher. Durability tiers, not preferences.

# Inputs: irreplaceable, never committed (and write-denied at the tool level).
data/

# Derived: regenerable but expensive. Never committed, never swept.
derived/

# Harness state: thread db, run logs, tmp. Local-only by design.
.kingfisher/

# Run output: disposable scratch, all of it. A run that produces something
# worth keeping puts it in derived/, which is never swept.
runs/
"""


@dataclass(frozen=True)
class SweepResult:
    removed: tuple[str, ...]
    kept: int
    #: Sessions that could not be fully removed, with why. Surfaced rather
    #: than swallowed: a checkpointer that cannot delete at all should be
    #: visible, not silently tolerated on every run.
    failures: tuple[str, ...] = ()


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


def _git(workspace: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # S607: `git` by name, resolved through PATH. An absolute path would be
    # wrong on a machine that installs it elsewhere, which is most of them.
    return subprocess.run(  # noqa: S603
        ["git", "-C", str(workspace), *args],  # noqa: S607
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


def sweep(workspace: Path, keep: int, checkpointer: Any | None = None) -> SweepResult:
    """Keep the `keep` most recent run directories; delete the rest.

    A swept session loses its directory *and* its thread. There is no
    transaction across a filesystem and sqlite, so the order is chosen to make
    the surviving failure benign:

      thread first, then directory
        a failure leaves a directory whose thread still exists — the session
        is intact and the next sweep retries it
      directory first, then thread  (what this used to do)
        a failure leaves a thread pointing at deleted files, which is exactly
        the state that makes an agent cite paths that are not there

    Nothing is half-deleted: if the thread will not go, the directory stays.
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
    failures: list[str] = []
    for path in doomed:
        # The aggregate owns the ordering; this service only chooses victims.
        failure = Session(id=path.name, directory=path).discard(checkpointer)
        if failure:
            failures.append(failure)
        else:
            removed.append(path.name)

    return SweepResult(
        removed=tuple(removed),
        kept=len(dirs) - len(removed),
        failures=tuple(failures),
    )

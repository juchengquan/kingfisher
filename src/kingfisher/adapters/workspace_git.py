"""Git bookkeeping for the workspace.

The restore point before each run. Which paths are worth committing is policy
and lives in `domain.layout.TRACKED_PATHS`; running git is mechanism and lives
here. `git add -A` is never used -- pointing kingfisher at a directory that
already holds unrelated work must not sweep that work into a commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from kingfisher.domain.layout import TRACKED_PATHS


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

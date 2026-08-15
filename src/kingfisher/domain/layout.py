"""The workspace layout, as data.

Every name and tier here is policy: which directories exist, which are git's
responsibility, which are disposable. None of it creates anything -- making the
layout real is `adapters.workspace_fs`, and this is what it is told to make.

The split matters because the tiers are a decision that wants reviewing, and it
was previously buried among mkdir calls and subprocess invocations.

  durable, shared   /data /derived /skills /subagents /memory
  per-session       runs/<session_id>/
  per-turn          runs/<session_id>/<turn>/
  harness-owned     .kingfisher/

  tracked in git    /skills, /subagents, /memory, PROMPT.md
  ignored, kept     /data, /derived, /.kingfisher
  ignored, swept    everything under runs/*/

Git tracks what a person authored; `/derived` holds what the agent produced and
wants to keep. There is no directory for reports, because "a report" is one kind
of output among many.
"""

from __future__ import annotations

LAYOUT_DIRS: tuple[str, ...] = (
    "data",
    "derived",
    "skills",
    "subagents",
    "memory",
    "runs",
    # `.kingfisher` holds the marker. Its `runs/` and `tmp/` subdirectories are
    # not created here: both are relocatable (`KINGFISHER_STATE_DIR`,
    # `KINGFISHER_SCRATCH_DIR`) and each is created by whatever opens it, so
    # creating them here would leave empty decoys behind when they are moved.
    ".kingfisher",
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

"""The workspace layout, as data.

Every name and tier here is policy: which directories exist, which are git's
responsibility, which are disposable. None of it creates anything -- making the
layout real is `adapters.workspace_fs`, and this is what it is told to make.

The split matters because the tiers are a decision that wants reviewing, and it
was previously buried among mkdir calls and subprocess invocations.

  shared by all     /skills /subagents        definitions, authored by a person
  per-session       sessions/<id>/            /data /derived /memory /runs
  per-turn          sessions/<id>/runs/<turn>/
  harness-owned     .kingfisher/

  authored          /skills, /subagents, PROMPT.md   -- version them if you like
  harness-owned     /.kingfisher
  disposable        everything under sessions/

A session directory is the backend root, which is why it holds every name the
agent addresses: `/data` means the same thing in every session while pointing
somewhere different in each. Git tracks what a person authored; what a session
produced leaves through the run's result. There is no directory for reports,
because "a report" is one kind of output among many.
"""

from __future__ import annotations

#: Created once in the workspace. What remains here is what a session does not
#: own: the definitions the sessions share, and the harness's own directory.
LAYOUT_DIRS: tuple[str, ...] = (
    "skills",
    "subagents",
    # Sessions are the unit of isolation; each one is a backend root.
    "sessions",
    # `.kingfisher` holds the marker. Its `runs/` and `tmp/` subdirectories are
    # not created here: both are relocatable (`KINGFISHER_STATE_DIR`,
    # `KINGFISHER_SCRATCH_DIR`) and each is created by whatever opens it, so
    # creating them here would leave empty decoys behind when they are moved.
    ".kingfisher",
)

#: Created inside every session directory, which is the backend root. These are
#: the names the agent addresses — `/data`, `/derived`, `/memory`, `/runs` — so
#: they mean the same thing in every session while pointing somewhere different
#: in each. That is what makes one prompt serve every session.
SESSION_DIRS: tuple[str, ...] = (
    "data",
    "derived",
    "memory",
    "runs",
)

#: What a run produces and would lose. `/data` is read-only and came from the
#: caller; `/runs` is scratch the prompt already calls disposable. These two are
#: the ones the agent is told will outlive the run, so these are what a reaped
#: session takes with it unless the caller is handed a list.
ARTIFACT_DIRS: tuple[str, ...] = (
    "derived",
    "memory",
)

MARKER = ".kingfisher/WORKSPACE"

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

WORKSPACE_GITIGNORE = """\
# Managed by kingfisher. Durability tiers, not preferences.

# Harness state: thread db, run logs, tmp. Local-only by design.
.kingfisher/

# Session state: inputs, derived output, memory and run scratch. All of it
# belongs to one session and none of it is a person's work, so what a session
# produces and wants kept leaves through the run's result, not through git.
sessions/
"""

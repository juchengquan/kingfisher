"""The workspace layout, as data.

Every name and tier here is policy: which directories exist, which hold what a
person wrote, which are disposable. None of it creates anything -- making the
layout real is `infrastructure.workspace_fs`, and this is what it is told to make.

The split matters because the tiers are a decision that wants reviewing, and it
was previously buried among mkdir calls and subprocess invocations.

  shared by all     /agents /skills           definitions, authored by a person
                    /subagents /tools
  per-session       sessions/<id>/            /data /derived /memory /runs
  per-turn          sessions/<id>/runs/<turn>/
  harness-owned     .kingfisher/

  authored          /agents, /skills, /subagents, /tools, PROMPT.md
  harness-owned     /.kingfisher
  disposable        everything under sessions/

A session directory is the backend root, which is why it holds every name the
agent addresses: `/data` means the same thing in every session while pointing
somewhere different in each. What a session produced leaves through the run's
result. There is no directory for reports, because "a report" is one kind of
output among many.

The tiers used to name git as the mechanism for the authored one, and kingfisher
used to run it: a `pre_run_commit` snapshotted the tracked paths before each
turn. That went with `adapters/`, and nothing here runs git now. The tiers still
hold -- they are about durability, not about a tool -- and versioning the
authored tier is an operator's business, best done wherever
`KINGFISHER_SKILLS_DIR` points rather than around 200MB of sessions.
"""

from __future__ import annotations

#: Created once in the workspace. What remains here is what a session does not
#: own: the definitions the sessions share, and the harness's own directory.
LAYOUT_DIRS: tuple[str, ...] = (
    "skills",
    "subagents",
    # Python this process imports, not content the agent reads. It is created
    # here so the place to put one is obvious, and routed nowhere.
    "tools",
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

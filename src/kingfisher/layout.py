"""The workspace layout, as data. Belongs to no layer, which is why it sits here.

The tiers are about durability, not about a tool. Versioning the authored tier is an
operator's business, best done wherever `KINGFISHER_SKILLS_DIR` points rather than
around 200MB of sessions.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Created once in the workspace: the definitions the sessions share, and the
#: harness's own directory.
LAYOUT_DIRS: tuple[str, ...] = (
    "skills",
    "subagents",
    # Python this process imports, not content the agent reads. Created here so
    # the place to put one is obvious, and routed nowhere.
    "tools",
    # Sessions are the unit of isolation; each one is a backend root.
    "sessions",
    # `.kingfisher` holds the marker. Its `runs/` subdirectory is not created
    # here: it is relocatable (`KINGFISHER_STATE_DIR`) and is created by
    # whatever opens it, so creating it here would leave an empty decoy behind
    # when it is moved.
    ".kingfisher",
)

#: Created inside every session directory, which is the backend root. These are
#: the names the agent addresses, so they mean the same thing in every session
#: while pointing somewhere different in each. That is what makes one prompt serve
#: every session. Named one at a time so nothing below has to spell them again.
DATA = "data"
DERIVED = "derived"
MEMORY = "memory"
RUNS = "runs"

SESSION_DIRS: tuple[str, ...] = (DATA, DERIVED, MEMORY, RUNS)

#: The agent's HOME, and where a caller's uploaded skills land. Created inside
#: every session like `SESSION_DIRS`, and kept apart from it because that tuple
#: means "the names the agent addresses" and these are plumbing: `.home` exists so
#: a pip cache lands inside the session that caused it and counts toward its
#: quota, and uploads are reached through a route rather than by this path.
AGENT_HOME = ".home"

#: Where skills live, and the reserved name an upload goes under inside a session.
#: A catalogue skill called `uploaded` would shadow the route and hide every
#: upload, which is why the name is reserved rather than merely used.
SKILLS = "skills"
UPLOADED_SKILL_DIR = "uploaded"

UPLOADED_SKILLS = f"{SKILLS}/{UPLOADED_SKILL_DIR}"

#: The agent's `TMPDIR`, for the reason `.home` is here: one shared scratch
#: directory for the whole workspace was swept by nothing, counted against no
#: session's quota, and readable by every other session's shell -- so what one
#: caller derived sat where another caller's agent could read it. Per session,
#: it is deleted with the session and counted by `session_bytes`, and neither
#: fence has to grant anything beyond the session directory it already grants.
AGENT_TMP = ".tmp"

#: What the harness keeps about a session, inside the session and out of the
#: agent's reach: the agent it opened with, its conversation, the lock a turn
#: holds, and its run log. Every one of these used to live under `state_dir`,
#: where nothing deleted it when the session went and nothing counted it against
#: the session that caused it -- one file per session that ever existed, kept
#: forever. Inside, `reap` and `session_bytes` cover them the way they already
#: cover everything else a session holds.
#:
#: Reachable at `/.harness` -- the shell backend roots at the session -- which is
#: why it is denied twice: a `Route` carrying read and write denies for the file
#: tools, and a rule in the sandbox profile for the shell, which bypasses them.
HARNESS = ".harness"

#: The four things it holds. Named here rather than by the modules that write
#: them, because `.harness` is a layout decision and those modules were each
#: spelling a path of their own under `state_dir` before this.
PINNED_AGENT = "agent.yaml"
TRANSCRIPT_FILE = "transcript.jsonl"
CLAIM = "claim"
#: Not `runs.jsonl`: `/runs` already means per-turn scratch the agent addresses,
#: and two things called "runs" in one session directory is how the last pair of
#: names in this file drifted apart.
RUNLOG = "runlog.jsonl"

SESSION_PLUMBING: tuple[str, ...] = (
    AGENT_HOME,
    AGENT_TMP,
    HARNESS,
    UPLOADED_SKILLS,
)

#: What a run produces and would lose. `/data` is read-only and came from the
#: caller; `/runs` is scratch the prompt already calls disposable. These two are
#: the ones the agent is told will outlive the run, so these are what a reaped
#: session takes with it unless the caller is handed a list.
ARTIFACT_DIRS: tuple[str, ...] = (DERIVED, MEMORY)


#: The folder name a catalogue may not use for its own skills, because a
#: subagent's bundled skills already mean something under this root.
RESERVED_SKILL_FOLDER = "subagents"


def _route(*parts: str) -> str:
    """A path the agent addresses, with both slashes, from names above."""
    return "/" + "/".join(parts) + "/"


DATA_ROUTE = _route(DATA)
MEMORY_ROUTE = _route(MEMORY)
SKILLS_ROUTE = _route(SKILLS)
UPLOADED_SKILLS_ROUTE = _route(UPLOADED_SKILLS)
BUNDLED_SKILLS_ROUTE = _route(SKILLS, RESERVED_SKILL_FOLDER)

#: The paths that are *not* routed, kept in the table rather than left out of it:
#: they reach the default backend -- the shell's, rooted at the session -- and a
#: reader asking "what happens to /derived" should find the answer here rather
#: than by noticing an absence.
DERIVED_ROUTE = _route(DERIVED)
RUNS_ROUTE = _route(RUNS)

#: A route the agent may not read or write, which is the only reason it is one:
#: `FilesystemMiddleware` refuses `permissions=` outright unless every rule path
#: sits under a route, so a path with no mount cannot carry a rule at all. What
#: it holds is in `HARNESS`.
HARNESS_ROUTE = _route(HARNESS)


@dataclass(frozen=True)
class Route:
    """One path the agent addresses, and what is true of it."""

    #: The path, with both slashes, as `CompositeBackend` matches it.
    path: str
    #: The scope whose deny rule covers this path, or `None` where the agent may
    #: write. Several routes share one scope on purpose: `/skills/**` covers the
    #: catalogue, uploads and every bundle, and one rule per mount would make the
    #: rule count depend on how many bundles a catalogue happens to have.
    deny_write_under: str | None = None
    #: The scope whose *read* deny covers this path, for the one route that is
    #: mounted so it can be refused rather than so it can be reached. Denied
    #: entries are filtered out of `ls`, `glob` and `grep` results rather than
    #: erroring, so this makes a path invisible instead of visibly forbidden --
    #: which is the difference between a model ignoring it and a model retrying
    #: against it.
    deny_read_under: str | None = None
    #: Whether the composite gives this path a backend of its own. `False` means
    #: it reaches the default, which is the shell's backend rooted at the session
    #: directory.
    routed: bool = True
    #: Whether this is a prefix whose members are generated per catalogue rather
    #: than listed. One entry, many mounts: a bundle's skills are keyed by where
    #: the bundle sits, so the names are not known until a catalogue is read.
    family: bool = False


#: What the agent addresses, and what is true of each. Read by `harness.backend`
#: for the mounts and by `harness.agent` for the deny rules, so the two cannot
#: disagree about which paths exist.
ROUTES: tuple[Route, ...] = (
    # A caller's inputs, and nothing else holds a copy: never re-derivable from
    # the workspace, and kingfisher versions nothing. The rule here binds the file
    # tools only -- the shell bypasses them entirely, which is why
    # `workspace.permissions.protect_data` drops the write bits underneath it.
    Route(DATA_ROUTE, deny_write_under=f"{DATA_ROUTE}**"),
    # Routed so a request that declines the memory a deployment wired has
    # somewhere to hang a deny rule, not because memory needs isolating.
    Route(MEMORY_ROUTE),
    # Instructions the agent follows, which makes this the one route where a write
    # outlasts the request that made it: a skill edited during one request is read
    # by every later one, in every deployment sharing that directory.
    Route(SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
    # A request's own skills. A *longer* prefix than the catalogue's, and the
    # composite matches longest-first, so this wins beneath it while everything
    # else under `/skills/` still reaches the shared set.
    #
    # Refused writes too: an agent able to rewrite an uploaded skill could rewrite
    # the instructions it was about to follow.
    Route(UPLOADED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
    # One mount per subagent bundle that ships skills, keyed by where the bundle
    # sits under the catalogue so two folders may each hold a `surveyor`.
    #
    # **Under `/skills/` rather than beside it, and that is the whole of why this
    # prefix and not a shorter one.** Two things make the catalogue read-only --
    # the deny rule below and the sandbox profile -- and both are scoped to this
    # prefix. A route at `/subagent-skills/` would have been a writable skills
    # mount: the exact hole measured in `test_skills_read_only`.
    Route(BUNDLED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**", family=True),
    # Unrouted, and deliberately. What a run produces is the agent's to write, and
    # it reaches the default backend along with everything else the session holds.
    Route(DERIVED_ROUTE, routed=False),
    Route(RUNS_ROUTE, routed=False),
    # The one route that exists to be refused. Denied both ways: a run able to
    # write here could rewrite the agent definition it is running under, or the
    # conversation the next turn is rebuilt from, halfway through the
    # conversation those produced; a run able to read here gains nothing it was
    # not already told.
    Route(
        HARNESS_ROUTE,
        deny_write_under=f"{HARNESS_ROUTE}**",
        deny_read_under=f"{HARNESS_ROUTE}**",
    ),
)


def denied_scopes() -> tuple[str, ...]:
    """Every scope a write is refused under, once each, in a stable order.

    Deduplicated because `/skills/**` covers four routes and more when a catalogue
    ships bundles. Sorted so the rules a deployment gets do not depend on the
    order this table happens to be written in.
    """
    return tuple(sorted({r.deny_write_under for r in ROUTES if r.deny_write_under}))


def denied_read_scopes() -> tuple[str, ...]:
    """Every scope a read is refused under, the same way.

    A second function rather than a parameter on the first, because the two
    answers go to two rules and a caller asking for one never wants the other:
    a read deny on `/data/` would break the thing `/data/` is for.
    """
    return tuple(sorted({r.deny_read_under for r in ROUTES if r.deny_read_under}))


def routed_paths() -> tuple[str, ...]:
    """The paths the composite mounts itself, families excluded."""
    return tuple(r.path for r in ROUTES if r.routed and not r.family)

MARKER = ".kingfisher/WORKSPACE"

#: What the marker says, and the whole of the compatibility story. The file has
#: always held `kingfisher workspace\n` and nothing has ever read its contents --
#: `is_new_workspace` asks only whether it exists -- so it is where a version can
#: go without costing anything.
#:
#: It is here because the alternative was worse. When per-session state moved
#: into the session, a workspace laid out the old way did not *break*: it went
#: quiet and wrong. A pin the new code cannot find means the session silently
#: re-pins and may change agent mid-conversation; a transcript read from the new
#: path means the conversation comes back empty. Fallback readers would paper
#: over both and have nothing to make anyone remove them -- the day the old paths
#: are gone, nothing says so. A refusal is loud, and the next layout change
#: inherits this rather than needing its own.
LAYOUT_VERSION = 2
MARKER_TEXT = f"kingfisher workspace\nlayout {LAYOUT_VERSION}\n"

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

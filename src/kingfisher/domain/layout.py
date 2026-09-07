"""The workspace layout, as data.

Every name and tier here is policy: which directories exist, which hold what a
person wrote, which are disposable. None of it creates anything -- making the
layout real is `infrastructure.workspace.layout`, and this is what it is told to make.

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

from dataclasses import dataclass

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
#: Named one at a time so nothing below has to spell them again. `ARTIFACT_DIRS`
#: did, and the routes in `harness/backend.py` did it a third time with slashes
#: on -- which is how `/data/**` in a permission came to sit in another module
#: with nothing tying it to the route it is scoped to.
DATA = "data"
DERIVED = "derived"
MEMORY = "memory"
RUNS = "runs"

SESSION_DIRS: tuple[str, ...] = (DATA, DERIVED, MEMORY, RUNS)

#: The agent's HOME, and where a caller's uploaded skills land. Created inside
#: every session like `SESSION_DIRS`, and kept apart from it because that tuple
#: means "the names the agent addresses" and these are plumbing: `.home` exists
#: so a pip cache lands inside the session that caused it and counts toward its
#: quota, and uploads are reached through a route rather than by this path.
#: Folding them in would make one name mean two things, and a reader asking what
#: a prompt can refer to would have to filter the answer.
#:
#: Composed from the two leaves below rather than spelled again: a second
#: spelling is how the two halves of this layout drifted apart in the first
#: place.
AGENT_HOME = ".home"

#: Where skills live, and the reserved name an upload goes under inside a
#: session. Declared here rather than in `skills.spec`, which is where they
#: were: they are facts about the layout, and this module *is* the layout as
#: data. While both lived in `domain/` nothing had to choose; `skills` becoming
#: a module of its own made the domain import it to learn where a directory
#: was, which is the wrong way round and is what named the owner.
#:
#: A catalogue skill called `uploaded` would shadow the route and hide every
#: upload, which is why the name is reserved rather than merely used.
SKILLS = "skills"
UPLOADED_SKILL_DIR = "uploaded"

UPLOADED_SKILLS = f"{SKILLS}/{UPLOADED_SKILL_DIR}"

SESSION_PLUMBING: tuple[str, ...] = (
    AGENT_HOME,
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
    """A path the agent addresses, with both slashes, from names above.

    A function rather than five f-strings so the shape is stated once. Both
    slashes matter: `CompositeBackend` matches a route by prefix, and a route
    without its trailing slash would match `/database/` as well as `/data/`.
    """
    return "/" + "/".join(parts) + "/"


DATA_ROUTE = _route(DATA)
MEMORY_ROUTE = _route(MEMORY)
SKILLS_ROUTE = _route(SKILLS)
UPLOADED_SKILLS_ROUTE = _route(UPLOADED_SKILLS)
BUNDLED_SKILLS_ROUTE = _route(SKILLS, RESERVED_SKILL_FOLDER)

#: The paths that are *not* routed, kept in the table rather than left out of
#: it: they reach the default backend -- the shell's, rooted at the session --
#: and a reader asking "what happens to /derived" should find the answer here
#: rather than by noticing an absence.
DERIVED_ROUTE = _route(DERIVED)
RUNS_ROUTE = _route(RUNS)


@dataclass(frozen=True)
class Route:
    """One path the agent addresses, and what is true of it.

    Policy, like everything else in this module: nothing here builds a backend
    or a permission. `infrastructure.harness.backend` turns each entry into a
    mount and `infrastructure.harness.agent` turns the scopes into deny rules,
    which is the same division this file's first line describes.

    It exists because these facts were in two modules and coupled by a string
    typed twice. `FilesystemMiddleware` refuses `permissions=` outright unless
    every rule is scoped to a route, so a path and its rule have to agree -- and
    nothing made them agree except care, with the failure arriving at a turn
    rather than at the definition.

    No `why` field. The reasoning is in the comments beside each entry, where
    this codebase keeps reasoning; a string field nobody reads would be prose in
    a second form, able to disagree with the first.
    """

    #: The path, with both slashes, as `CompositeBackend` matches it.
    path: str
    #: The scope whose deny rule covers this path, or `None` where the agent may
    #: write. Several routes share one scope on purpose: `/skills/**` covers the
    #: catalogue, uploads and every bundle, which is one rule rather than one per
    #: mount -- and one per mount would make the rule count depend on how many
    #: bundles a catalogue happens to have.
    deny_write_under: str | None = None
    #: Whether the composite gives this path a backend of its own. `False` means
    #: it reaches the default, which is the shell's backend rooted at the
    #: session directory.
    routed: bool = True
    #: Whether this is a prefix whose members are generated per catalogue rather
    #: than listed. One entry, many mounts: a bundle's skills are keyed by where
    #: the bundle sits, so the names are not known until a catalogue is read.
    family: bool = False


#: What the agent addresses, and what is true of each. Read by
#: `harness.backend` for the mounts and by `harness.agent` for the deny rules,
#: so the two cannot disagree about which paths exist.
ROUTES: tuple[Route, ...] = (
    # A caller's inputs, and nothing else holds a copy: never re-derivable from
    # the workspace, and kingfisher versions nothing. The rule here binds the
    # file tools only -- the shell bypasses them entirely, which is why
    # `workspace.permissions.protect_data` drops the write bits underneath it.
    Route(DATA_ROUTE, deny_write_under=f"{DATA_ROUTE}**"),
    # Routed so a request that declines the memory a deployment wired has
    # somewhere to hang a deny rule, not because memory needs isolating. The
    # same is true of `/skills/`: `FilesystemMiddleware` refuses `permissions=`
    # outright unless every rule path is scoped to a route, so a route is what
    # makes a rule expressible at all.
    Route(MEMORY_ROUTE),
    # Instructions the agent follows, which makes this the one route where a
    # write outlasts the request that made it: a skill edited during one request
    # is read by every later one, in every deployment sharing that directory.
    Route(SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
    # A request's own skills. A *longer* prefix than the catalogue's, and the
    # composite matches longest-first, so this wins beneath it while everything
    # else under `/skills/` still reaches the shared set. That is why the
    # catalogue kept its plain path: uploads nest underneath rather than forcing
    # it to be renamed.
    #
    # Refused writes too, and deliberately. This half belongs to the session
    # rather than to the deployment, but kingfisher writes it host-side for the
    # same reason, and an agent able to rewrite an uploaded skill could rewrite
    # the instructions it was about to follow.
    Route(UPLOADED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
    # One mount per subagent bundle that ships skills, keyed by where the bundle
    # sits under the catalogue so two folders may each hold a `surveyor`.
    #
    # **Under `/skills/` rather than beside it, and that is the whole of why this
    # prefix and not a shorter one.** Two things make the catalogue read-only --
    # the deny rule below and the sandbox profile -- and both are scoped to this
    # prefix. A route at `/subagent-skills/` would have been a writable skills
    # mount: the exact hole measured in `test_skills_read_only`, where
    # `backend.write("/skills/demo/PWNED.md")` created a file and `backend.edit`
    # tampered with one, reopened for the newest kind of skill.
    Route(BUNDLED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**", family=True),
    # Unrouted, and deliberately. What a run produces is the agent's to write,
    # and it reaches the default backend along with everything else the session
    # holds.
    Route(DERIVED_ROUTE, routed=False),
    Route(RUNS_ROUTE, routed=False),
)


def denied_scopes() -> tuple[str, ...]:
    """Every scope a write is refused under, once each, in a stable order.

    Deduplicated because `/skills/**` covers four routes and more when a
    catalogue ships bundles. Sorted so the rules a deployment gets do not depend
    on the order this table happens to be written in.
    """
    return tuple(sorted({r.deny_write_under for r in ROUTES if r.deny_write_under}))


def routed_paths() -> tuple[str, ...]:
    """The paths the composite mounts itself, families excluded.

    A family's members are generated from a catalogue, so they cannot be listed
    here; `family_prefixes` is how a caller recognises one.
    """
    return tuple(r.path for r in ROUTES if r.routed and not r.family)

MARKER = ".kingfisher/WORKSPACE"

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

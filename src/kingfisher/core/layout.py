"""The workspace layout, as data. Belongs to no layer, which is why it sits here.

The tiers are about durability, not about a tool. Versioning the authored tier is an
operator's business, best done in `KINGFISHER_DEFINITIONS_DIR`, which is kept apart
from the 200MB of sessions for that reason among others.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The harness's own directory in a workspace: not a session's, and not the
#: agent's. Declared before `LAYOUT_DIRS`, which creates it, and named again
#: by `MARKER`, the sandbox profile and the skills view that live in it.
HARNESS_OWNED = ".kingfisher"

#: Where a workspace keeps its sessions, one folder each: `Config.sessions_root`.
SESSIONS = "sessions"

#: Created in the workspace whatever else is configured: where sessions are, and
#: the harness's own directory. No definition kind belongs here, because a kind is
#: made where its setting points and that is not always the workspace -- a kind
#: listed here gets a folder beside the sessions that nothing reads.
LAYOUT_DIRS: tuple[str, ...] = (
    # Sessions are the unit of isolation; each one is a backend root.
    SESSIONS,
    # The harness's own, which the agent may not write: the marker, the sandbox
    # profile that says what the shell may do, and the skills view the shell reads
    # a mounted catalogue through -- the one thing here it is told about. It held
    # per-session state too -- run logs, claims, pinned agents, scratch -- and
    # `KINGFISHER_STATE_DIR` existed to move all of that somewhere else. What is
    # left describes the workspace rather than any session in it, and there is
    # nothing left to relocate.
    HARNESS_OWNED,
)

#: The sandbox profile's name, which `profile_path` puts a digest of its rules inside:
#: `shell.<digest>.sb`.
SANDBOX_PROFILE = "shell.sb"

#: Where the shell's view of a catalogue with mounts is built, one directory per
#: arrangement of it. Inside `HARNESS_OWNED` because the shell must read through it
#: and never write it, and that directory is already both.
SKILLS_VIEW = "skills-view"

#: Created inside every session directory, which is the backend root. These are
#: the names the agent addresses, so they mean the same thing in every session
#: while pointing somewhere different in each. That is what makes one prompt serve
#: every session. Named one at a time so nothing below has to spell them again.
#:
#: The two that carry files between the caller and the agent are named for which
#: way files go, because the name is most of what the agent knows about a folder:
#: it reads `inputs` and may not write there, and what it writes to `outputs` is
#: handed back.
INPUTS = "inputs"
OUTPUTS = "outputs"
MEMORY = "memory"
#: Where the agent works. Disposable by contract: never returned to the caller,
#: never saved, swept when the session goes.
#:
#: It was `.tmp`, plumbing the agent was never told about, while a parallel
#: `runs/<turn>` held the same kind of file under a name the agent *was* told. Two
#: scratch directories with one purpose, and the per-turn one accumulated for the
#: life of a session because nothing ever swept it. The dot went with the silence:
#: a name the agent addresses belongs in this tuple rather than beside the lock.
SCRATCH = "scratchpad"

SESSION_DIRS: tuple[str, ...] = (INPUTS, OUTPUTS, MEMORY, SCRATCH)

#: Where a recorded tool result is kept, under the scratchpad: the agent reads one
#: back with `read_file` and searches them with `grep`, and they go with the session.
#: Not `results`, the name anyone gives a folder of their own work: the agent and its
#: tools write in the scratchpad too, and this folder is the recorder's. Beside
#: deepagents' `large_tool_results/`, which keeps those too long to show, the two
#: read as one pair.
TOOL_RESULTS = "tool_results"

#: deepagents' own, beside it: where a result too long to show the model whole is
#: offloaded, and the path a row names once `_repointed` has moved it there. Spelled
#: here because deepagents builds it as a private attribute, in two places, from the
#: artifacts root -- there is no constant to read it off.
OFFLOADS = "large_tool_results"

#: Where skills live.
SKILLS = "skills"

#: What the harness keeps about a session, inside the session and out of the
#: agent's reach, named below. The agent it opened with, its conversation and the
#: lock a turn holds used to live under `state_dir`,
#: where nothing deleted it when the session went and nothing counted it against
#: the session that caused it -- one file per session that ever existed, kept
#: forever. Inside, `reap` and `session_bytes` cover them the way they already
#: cover everything else a session holds.
#:
#: Reachable at `/.harness` -- the shell backend roots at the session -- which is
#: why it is denied twice: a `Route` carrying read and write denies for the file
#: tools, and a rule in the sandbox profile for the shell, which bypasses them.
HARNESS = ".harness"

#: What it holds. Named here rather than by the modules that write them, because
#: `.harness` is a layout decision and those modules were each spelling a path of
#: their own under `state_dir` before this.
PINNED_AGENT = "agent.yaml"
TRANSCRIPT_FILE = "transcript.jsonl"
#: What the session's tools returned, one row a result and a closing line a turn.
#: Here rather than beside the results it lists: a row names its file, and the agent
#: must not be able to rewrite what a later turn reads back as true.
LEDGER_FILE = "ledger.jsonl"
CLAIM = "claim"
#: Written only by a turn that stopped at an approval gate, and deleted the moment
#: one is answered or superseded. Its presence *is* the mark that a session is
#: waiting -- a second file saying so is a second thing to keep in step, and the
#: two would disagree the first time a write failed between them.
PAUSED_STATE = "paused.state"
#: What the checkpoint beside it was built against, which a resume checks before
#: loading it. Apart from the state because it has to be readable when the state
#: is not: a checkpoint written by libraries that have since moved is exactly the
#: case this answers, and answering it means reading this without reading that.
PAUSED_MARK = "paused.json"

#: The folder name a catalogue may not use for its own skills, because a
#: subagent's bundled skills already mean something under this root.
RESERVED_SKILL_FOLDER = "subagents"


def _route(*parts: str) -> str:
    """A path the agent addresses, with both slashes, from names above."""
    return "/" + "/".join(parts) + "/"


INPUTS_ROUTE = _route(INPUTS)
MEMORY_ROUTE = _route(MEMORY)
SKILLS_ROUTE = _route(SKILLS)
BUNDLED_SKILLS_ROUTE = _route(SKILLS, RESERVED_SKILL_FOLDER)

#: The paths that are *not* routed, kept in the table rather than left out of it:
#: they reach the default backend -- the shell's, rooted at the session -- and a
#: reader asking "what happens to /outputs" should find the answer here rather
#: than by noticing an absence.
#:
#: `/scratchpad` is among them, and that is what lets the shell and the file tools
#: write the same place: `TMPDIR` points at it on disk, and the virtual path is
#: the same name with a slash.
OUTPUTS_ROUTE = _route(OUTPUTS)
SCRATCH_ROUTE = _route(SCRATCH)
TOOL_RESULTS_ROUTE = _route(SCRATCH, TOOL_RESULTS)
OFFLOADS_ROUTE = _route(SCRATCH, OFFLOADS)

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
    #: catalogue and every bundle, and one rule per mount would make the
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


#: What the agent addresses, and what is true of each. Read by
#: `core.agent.backend` for the mounts and by
#: `core.agent.assembly` for the deny rules, so the two cannot disagree
#: about which paths exist.
ROUTES: tuple[Route, ...] = (
    # A caller's inputs, and nothing else holds a copy: never re-derivable from
    # the workspace, and kingfisher versions nothing. The rule here binds the file
    # tools only -- the shell bypasses them entirely, which is why
    # `plugins.sessions.permissions.protect_inputs` drops the write bits
    # underneath it.
    Route(INPUTS_ROUTE, deny_write_under=f"{INPUTS_ROUTE}**"),
    # Routed so a request that declines the memory a deployment wired has
    # somewhere to hang a deny rule, not because memory needs isolating.
    Route(MEMORY_ROUTE),
    # Instructions the agent follows, which makes this the one route where a write
    # outlasts the request that made it: a skill edited during one request is read
    # by every later one, in every deployment sharing that directory.
    Route(SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
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
    Route(OUTPUTS_ROUTE, routed=False),
    Route(SCRATCH_ROUTE, routed=False),
    # Read-only to the file tools, and routed only so that rule is expressible. A row
    # names its file and `prompts/system.md` tells the model to read that file back
    # rather than call the tool again -- so a turn that could write here could forge
    # what a later turn reads as true, which is the reason the ledger itself sits under
    # `.harness`. Measured before this: the row stayed at its original size and summary
    # while the file held `forged`.
    #
    # The shell is not covered by this rule: filesystem permissions are deepagents'
    # and `execute` bypasses them. **Nor is it covered by the sweep**, which this said
    # for a while -- the sweep skips every path a row names, so it cannot see an
    # overwrite at all. What the narrowing above it earns is that a draft a shell left
    # here is not deleted in the turn that wrote it, which is a different property.
    #
    # The shell's half belongs to the fences, which take it from `Layout.saved_results`:
    # `sandbox-exec` and bubblewrap refuse this folder the way they refuse `.harness`,
    # while Landlock cannot while the folder sits inside a scratchpad it grants whole,
    # since a read-only grant under a writable parent is stepped over. Two of three is
    # not a boundary, so nothing here claims one.
    Route(TOOL_RESULTS_ROUTE, deny_write_under=f"{TOOL_RESULTS_ROUTE}**"),
    # The same rule for the folder a row names once the result was too long to show
    # whole. Denying one and not the other refused the file tools a 32-byte result and
    # handed them a 101 KB one -- measured, with the row still naming its size.
    Route(OFFLOADS_ROUTE, deny_write_under=f"{OFFLOADS_ROUTE}**"),
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

    Deduplicated because `/skills/**` covers the catalogue's route and one more for
    every bundle a catalogue ships. Sorted so the rules a deployment gets do not depend on the
    order this table happens to be written in.
    """
    return tuple(sorted({r.deny_write_under for r in ROUTES if r.deny_write_under}))


def denied_read_scopes() -> tuple[str, ...]:
    """Every scope a read is refused under, the same way.

    A second function rather than a parameter on the first, because the two
    answers go to two rules and a caller asking for one never wants the other:
    a read deny on `/inputs/` would break the thing `/inputs/` is for.
    """
    return tuple(sorted({r.deny_read_under for r in ROUTES if r.deny_read_under}))


def routed_paths() -> tuple[str, ...]:
    """The paths the composite mounts itself, families excluded."""
    return tuple(r.path for r in ROUTES if r.routed and not r.family)


#: A name a deployment may not give a per-session leaf: `inputs`, `outputs`, `memory`,
#: `scratchpad` and `tool_results` share a session with the harness and sit beside (or
#: under) deepagents' own folders, so colliding with one of these would overwrite it.
_RESERVED_LEAF_NAMES = frozenset(
    {HARNESS, SESSIONS, SKILLS, OFFLOADS, "conversation_history", "blobs"}
)
_LEAF = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Layout:
    """The per-session folder names the agent addresses, which a deployment may rename.

    The fields are `inputs`, `outputs`, `memory`, `scratchpad` and `tool_results`: the
    leaves a client renames at startup, through `Config(layout=...)`. Everything derived
    -- the directories a session is laid out with, the routes the backend mounts, the
    deny scopes the file tools get, and through those the sandbox rules and the prompt --
    comes off an instance, so a renamed leaf moves all of them together rather than
    leaving a prompt or a deny rule still spelling the old name.

    Fixed, and not here: the `sessions` root (the agent never addresses it, and
    `core.session.model` derives session paths without being able to import this module);
    `.harness` and `skills`; and deepagents' own leaves under the scratchpad
    (`large_tool_results`, `conversation_history`, `blobs`), which deepagents builds from
    the scratchpad's name -- only the parent moves.

    The defaults are the names kingfisher has always used, so `Layout()` reproduces the
    module-level constants exactly; `test_the_default_layout_is_the_module_constants`
    pins that, so the two cannot drift.
    """

    inputs: str = INPUTS
    outputs: str = OUTPUTS
    memory: str = MEMORY
    scratchpad: str = SCRATCH
    tool_results: str = TOOL_RESULTS

    def __post_init__(self) -> None:
        named = {
            "inputs": self.inputs, "outputs": self.outputs, "memory": self.memory,
            "scratchpad": self.scratchpad, "tool_results": self.tool_results,
        }
        for field_name, value in named.items():
            if not _LEAF.match(value):
                msg = (f"layout {field_name}={value!r} is not a bare folder name; it must "
                       f"match {_LEAF.pattern} -- no slash, no leading dot, not empty")
                raise ValueError(msg)
            if value in _RESERVED_LEAF_NAMES:
                msg = (f"layout {field_name}={value!r} is a name kingfisher or deepagents "
                       f"already uses for a session folder; choose another")
                raise ValueError(msg)
        if len(set(named.values())) != len(named):
            msg = f"layout names collide: {named}; each session folder needs its own name"
            raise ValueError(msg)

    @property
    def session_dirs(self) -> tuple[str, ...]:
        """The directories a session is laid out with, which the agent addresses."""
        return (self.inputs, self.outputs, self.memory, self.scratchpad)

    @property
    def artifact_dirs(self) -> tuple[str, ...]:
        """What a reaped session hands back: produced, not given or disposable.

        The inputs folder is read-only and came from the caller; the scratchpad is
        disposable and the prompt says so. These two are what the agent is told will
        outlive the run, so these are what a reaped session takes with it unless the
        caller is handed a list.
        """
        return (self.outputs, self.memory)

    @property
    def inputs_route(self) -> str:
        return _route(self.inputs)

    @property
    def memory_route(self) -> str:
        return _route(self.memory)

    @property
    def outputs_route(self) -> str:
        return _route(self.outputs)

    @property
    def scratch_route(self) -> str:
        return _route(self.scratchpad)

    @property
    def tool_results_route(self) -> str:
        return _route(self.scratchpad, self.tool_results)

    @property
    def offloads_route(self) -> str:
        """deepagents' own, under this layout's scratchpad: the leaf is fixed, the parent moves."""
        return _route(self.scratchpad, OFFLOADS)

    @property
    def offloads(self) -> str:
        """deepagents' folder for a result too long to send, under the scratchpad. Fixed."""
        return OFFLOADS

    @property
    def harness(self) -> str:
        """What kingfisher keeps about the session, which no layout renames."""
        return HARNESS

    @property
    def harness_route(self) -> str:
        return HARNESS_ROUTE

    @property
    def skills_route(self) -> str:
        return SKILLS_ROUTE

    @property
    def saved_results(self) -> tuple[str, ...]:
        """The two folders a ledger row can name, as paths inside the session: what the
        fences refuse the shell, beside `.harness` and the inputs."""
        return (f"{self.scratchpad}/{self.tool_results}", f"{self.scratchpad}/{OFFLOADS}")

    @property
    def routes(self) -> tuple[Route, ...]:
        """The route table for this layout: the renamable leaves spelled from the
        instance, the fixed skills and harness routes unchanged."""
        return (
            Route(self.inputs_route, deny_write_under=f"{self.inputs_route}**"),
            Route(self.memory_route),
            Route(SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
            Route(BUNDLED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**", family=True),
            Route(self.outputs_route, routed=False),
            Route(self.scratch_route, routed=False),
            Route(self.tool_results_route, deny_write_under=f"{self.tool_results_route}**"),
            Route(self.offloads_route, deny_write_under=f"{self.offloads_route}**"),
            Route(HARNESS_ROUTE, deny_write_under=f"{HARNESS_ROUTE}**",
                  deny_read_under=f"{HARNESS_ROUTE}**"),
        )

    def denied_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({r.deny_write_under for r in self.routes if r.deny_write_under}))

    def denied_read_scopes(self) -> tuple[str, ...]:
        return tuple(sorted({r.deny_read_under for r in self.routes if r.deny_read_under}))

    def routed_paths(self) -> tuple[str, ...]:
        return tuple(r.path for r in self.routes if r.routed and not r.family)


#: The session folder names a deployment did not rename -- the names kingfisher has
#: always used. One shared frozen instance, so the many defaults that fall back to it
#: share one object rather than each constructing a `Layout()` in an argument default,
#: which the linter refuses. `config` re-exports it for callers that reach layout
#: through `Config`.
DEFAULT_LAYOUT = Layout()


MARKER = f"{HARNESS_OWNED}/WORKSPACE"

#: What the marker says. Nothing reads its contents, so it is a bare identity line.
#: It once carried a `layout N` number and `ensure_layout` refused a workspace laid
#: out by an older one, because moving per-session state into the session made an
#: old workspace go quiet and wrong rather than break. That refusal is gone while the
#: layout is still being reshaped week to week and sessions are disposable; the
#: version would ratchet on every change for a compatibility nobody wants yet. When
#: workspaces outlive config churn, a fingerprint of the configurable names belongs
#: here and a mismatch refused.
MARKER_TEXT = "kingfisher workspace\n"

AGENTS_SCAFFOLD = """\
# Project memory

Durable facts about this project and how to work in it. Add entries below.

## Conventions

(none recorded yet)
"""

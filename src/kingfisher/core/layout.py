"""The workspace layout, as data.

Versioning what people author is an operator's business, best done in
`KINGFISHER_DEFINITIONS_DIR`, which is kept apart from the sessions partly for that reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The harness's own directory in a workspace: not a session's, and not the agent's.
HARNESS_OWNED = ".kingfisher"

#: Where a workspace keeps its sessions, one folder each: `Config.sessions_root`.
SESSIONS = "sessions"

#: Created in the workspace whatever else is configured. No definition kind belongs here:
#: a kind is made where its setting points, so one listed here gets a folder nothing reads.
LAYOUT_DIRS: tuple[str, ...] = (
    # Sessions are the unit of isolation; each one is a backend root.
    SESSIONS,
    # The harness's own, which the agent may not write: the marker, the sandbox profile
    # and the skills view the shell reads a mounted catalogue through.
    HARNESS_OWNED,
)

#: Created inside every session directory, the backend root: the same names in every
#: session, so one prompt serves them all. `inputs` and `outputs` are named for which way
#: files go, because the name is most of what the agent knows about a folder.
INPUTS = "inputs"
OUTPUTS = "outputs"
MEMORY = "memory"
#: Where the agent works. Disposable by contract: never returned to the caller, never
#: saved, swept when the session goes.
SCRATCH = "scratchpad"

#: Where a recorded tool result is kept, under the scratchpad. Not `results`, the name
#: anyone gives a folder of their own work: the agent writes there too, and this is the
#: recorder's.
TOOL_RESULTS = "tool_results"

#: deepagents' own folder for a result too long to show the model whole. Nothing writes
#: it: `standing_middleware` replaces the middleware that would, so a long result is
#: previewed and kept where every other one is. Still reserved below, because a deployment
#: may replace that middleware back and a layout holding the name would overwrite it.
OFFLOADS = "large_tool_results"

#: The conversation deepagents keeps aside: what a summary left behind, and a message too
#: long to send. Written in every deployment, and the prompt sends the model here to read
#: some back, which is what makes a write here forgery rather than editing.
HISTORY = "conversation_history"

#: An attachment's payload, where deepagents keeps one it replaced in a message with a
#: reference. Nothing writes it either -- `offload_binary_content` stays at its default --
#: and a deployment turning it on would have the bytes of an attachment read back from a
#: name that is their own hash.
BLOBS = "blobs"

#: Where skills live.
SKILLS = "skills"

#: What the harness keeps about a session, inside it so `reap` and `session_bytes` cover
#: it. Reachable at `/.harness`, so denied twice: by a `Route` for the file tools, and by
#: the sandbox profile for the shell, which bypasses them.
HARNESS = ".harness"

#: What it holds, named here because `.harness` is a layout decision.
PINNED_AGENT = "agent.yaml"
#: The datazone of the session's first zoned turn, beside the agent rather than in it:
#: the pin is the agent's document as written.
PINNED_ZONE = "datazone"
TRANSCRIPT_FILE = "transcript.jsonl"
#: The session's tool results, a row each and a closing line a turn. Here, not beside
#: them, so the agent cannot rewrite what a later turn reads back as true.
LEDGER_FILE = "ledger.jsonl"
#: Written only by a turn stopped at an approval gate, deleted once answered or superseded.
#: Its presence *is* the mark a session is waiting: a second marker could disagree with it.
PAUSED_STATE = "paused.state"
#: What the paused checkpoint was built against, checked before loading it. Apart from it
#: so it is readable when a checkpoint written by libraries that have since moved is not.
PAUSED_MARK = "paused.json"

#: The folder name a catalogue may not use for its own skills, because a
#: subagent's bundled skills already mean something under this root.
RESERVED_SKILL_FOLDER = "subagents"


def _route(*parts: str) -> str:
    """A path the agent addresses, with both slashes."""
    return "/" + "/".join(parts) + "/"


INPUTS_ROUTE = _route(INPUTS)
SKILLS_ROUTE = _route(SKILLS)
BUNDLED_SKILLS_ROUTE = _route(SKILLS, RESERVED_SKILL_FOLDER)

#: `/outputs` and `/scratchpad` reach the default backend, the shell's, so the shell,
#: whose `TMPDIR` is the scratchpad, and the file tools write the same place.
OUTPUTS_ROUTE = _route(OUTPUTS)
SCRATCH_ROUTE = _route(SCRATCH)

#: Routed only so it can be denied: `FilesystemMiddleware` refuses `permissions=` unless
#: every rule path sits under a route.
HARNESS_ROUTE = _route(HARNESS)


@dataclass(frozen=True)
class Route:
    """One path the agent addresses, and what is true of it."""

    #: The path, with both slashes, as `CompositeBackend` matches it.
    path: str
    #: The scope whose deny rule covers this path, or `None` where the agent may write.
    #: Shared on purpose: `/skills/**` covers every bundle, so rules do not grow per bundle.
    deny_write_under: str | None = None
    #: The scope whose *read* deny covers this path. Denied entries are filtered out of
    #: `ls`, `glob` and `grep`, so the path is invisible, not forbidden, and not retried.
    deny_read_under: str | None = None
    #: Whether the composite gives this path a backend of its own, rather than the
    #: default: the shell's, rooted at the session.
    routed: bool = True
    #: A prefix whose mounts are generated per catalogue: a bundle's skills are keyed by
    #: where the bundle sits.
    family: bool = False


#: Names a renamed session leaf may not take: it shares the session with these folders,
#: kingfisher's and deepagents', and would overwrite one.
_RESERVED_LEAF_NAMES = frozenset(
    {HARNESS, SESSIONS, SKILLS, OFFLOADS, HISTORY, BLOBS}
)
_LEAF = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Layout:
    """The per-session folder names the agent addresses, which a deployment may rename.

    Renamed at startup through `Config(layout=...)`. Everything derived -- the session's
    directories, the routes, the deny scopes, and through those the sandbox rules and the
    prompt -- comes off an instance, so a renamed leaf moves them all together.

    Fixed: the `sessions` root, which the agent never addresses; `.harness` and `skills`;
    and deepagents' own leaves under the scratchpad, which only move with their parent.
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
            if not _LEAF.fullmatch(value):
                msg = (f"layout {field_name}={value!r} is not a bare folder name; it must "
                       f"match {_LEAF.pattern} -- no slash, no leading dot, not empty")
                raise ValueError(msg)
            # Folded, here and below: macOS's default filesystem takes `DATA` for `data`.
            if value.casefold() in {name.casefold() for name in _RESERVED_LEAF_NAMES}:
                msg = (f"layout {field_name}={value!r} is a name kingfisher or deepagents "
                       f"already uses for a session folder; choose another")
                raise ValueError(msg)
        if len({value.casefold() for value in named.values()}) != len(named):
            msg = f"layout names collide: {named}; each session folder needs its own name"
            raise ValueError(msg)

    @property
    def session_dirs(self) -> tuple[str, ...]:
        """The directories a session is laid out with, which the agent addresses."""
        return (self.inputs, self.outputs, self.memory, self.scratchpad)

    @property
    def artifact_dirs(self) -> tuple[str, ...]:
        """What a reaped session hands back: what the agent is told outlives the run."""
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
    def kept_record_routes(self) -> tuple[str, ...]:
        """Each kept-records folder as the agent addresses it, in the same order."""
        return tuple(_route(*path.split("/")) for path in self.kept_records)

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
        """The folder a ledger row names, as a path inside the session: what the fences
        refuse the shell, beside `.harness` and the inputs."""
        return (f"{self.scratchpad}/{self.tool_results}",)

    @property
    def history(self) -> str:
        """Where the conversation kept aside goes, as a path inside the session."""
        return f"{self.scratchpad}/{HISTORY}"

    @property
    def kept_records(self) -> tuple[str, ...]:
        """Every folder of kept records, as a path inside the session: what says what
        happened rather than what the agent made, so a write there is forgery.

        Wider than `saved_results`, which is the folder a ledger row names and the unused
        results walk -- a history file is no row's, and the two deepagents folders nothing
        writes hold no rows either. Those two are listed all the same: a deployment that
        replaces `FilesystemMiddleware` back turns them on, and a rule written for what
        writes today would not be there on the day it did.
        """
        return (*self.saved_results, self.history, f"{self.scratchpad}/{BLOBS}",
                f"{self.scratchpad}/{OFFLOADS}")

    @property
    def made_dirs(self) -> tuple[str, ...]:
        """Every directory a session is made with: the agent's, `.harness`, the saved
        results, and the history.

        The history is made here although deepagents makes it on the first eviction
        anyway, because a fence binds it read-only only if it is there when the fence is
        built: `present` drops an absent path, so without this the shell could make the
        folder itself in one turn and have the next turn's fence bind the forgery. The
        other two kept records stay absent, since nothing writes them and a mount does not
        make a folder.
        """
        return (*self.session_dirs, HARNESS, *self.saved_results, self.history)

    @property
    def shell_read_only(self) -> tuple[str, ...]:
        """What every fence keeps the shell from writing inside a session: the caller's
        inputs, what kingfisher keeps about the session, and the kept records."""
        return (self.inputs, HARNESS, *self.kept_records)

    @property
    def routes(self) -> tuple[Route, ...]:
        """What the agent addresses: one table for both the mounts and the deny rules, so
        the two cannot disagree about which paths exist."""
        return (
            # The only copy of a caller's inputs. This binds the file tools only; the shell
            # bypasses them, so `plugins.sessions.permissions.protect_inputs` drops the
            # write bits.
            Route(self.inputs_route, deny_write_under=f"{self.inputs_route}**"),
            # Routed so a request that declines the memory a deployment wired has
            # somewhere to hang a deny rule, not because memory needs isolating.
            Route(self.memory_route),
            # Instructions the agent follows: a skill edited in one request is read by
            # every later one, in every deployment sharing the directory.
            Route(SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**"),
            # One mount per subagent bundle that ships skills, keyed by where it sits.
            # Under `/skills/`, not beside it: the deny rule and the sandbox profile that
            # keep skills read-only are both scoped to that prefix.
            Route(BUNDLED_SKILLS_ROUTE, deny_write_under=f"{SKILLS_ROUTE}**", family=True),
            # Unrouted, and listed so a reader finds that here: the agent's to write.
            Route(self.outputs_route, routed=False),
            Route(self.scratch_route, routed=False),
            # The kept records: read-only to the file tools, and mounted only so the rules
            # can be written, since deepagents refuses a rule whose path is under no route.
            # A row names its file and the prompt sends the model to the history, so a
            # write in either forges what a later turn reads as true. The shell is the
            # fences' to refuse, via `shell_read_only`, and Landlock cannot inside a
            # scratchpad it grants whole, so no boundary is claimed.
            *(
                Route(route, deny_write_under=f"{route}**")
                for route in self.kept_record_routes
            ),
            # Denied both ways: a write could rewrite the pinned agent or the conversation
            # the next turn is rebuilt from, and a read gains nothing the run was not
            # already told.
            Route(HARNESS_ROUTE, deny_write_under=f"{HARNESS_ROUTE}**",
                  deny_read_under=f"{HARNESS_ROUTE}**"),
        )

    def denied_scopes(self) -> tuple[str, ...]:
        """Every scope a write is refused under, once each, sorted so table order is moot."""
        return tuple(sorted({r.deny_write_under for r in self.routes if r.deny_write_under}))

    def denied_read_scopes(self) -> tuple[str, ...]:
        """Every scope a read is refused under, the same way.

        Not a parameter on `denied_scopes`: the answers go to two rules, and a read deny on
        `/inputs/` would break what it is for.
        """
        return tuple(sorted({r.deny_read_under for r in self.routes if r.deny_read_under}))

    def routed_paths(self) -> tuple[str, ...]:
        """The paths the composite mounts itself, families excluded."""
        return tuple(r.path for r in self.routes if r.routed and not r.family)


#: The names a deployment did not rename, shared so no argument default constructs a
#: `Layout()`, which the linter refuses.
DEFAULT_LAYOUT = Layout()


MARKER = f"{HARNESS_OWNED}/WORKSPACE"

#: What the marker says. Nothing reads its contents, so it is a bare identity line.
MARKER_TEXT = "kingfisher workspace\n"

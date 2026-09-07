"""What the domain needs the world to do for it.

Narrow by intention. A port earns its place only where a rule genuinely depends
on a primitive: `allocate_turn` is atomic *because* `mkdir` fails on an existing
name, and expressing that as "scan, then create" in a caller would reintroduce
the race the loop exists to avoid. Where no primitive is load-bearing, the domain
returns a decision instead and the caller acts on it.

Protocols rather than base classes: an adapter satisfies these by shape, and a
test satisfies them with a dict.

The repository ports are narrow for a different reason -- they carry only what a
*replacement* must provide. Everything a local directory can also answer stays on
the local implementation, because a store that is not a directory has no answer
to give and should not be made to pretend.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.domain.agent import AgentSpec
from kingfisher.subagents.spec import SubagentSpec
from kingfisher.tools.spec import Found


@runtime_checkable
class AssetRepository(Protocol):
    """Something a deployment's definitions can be read from.

    One member, because `names` is all four kinds have in common. What a kind is
    made of -- a directory listing, parsed documents, imported Python -- differs
    so completely that a shared `load` would unify the word and none of the
    meaning.
    """

    @property
    def names(self) -> tuple[str, ...]:
        """Every definition held, by the name a request grants it, in a stable order.

        Stable because the agent is built from this. Two processes reading the
        same definitions must offer the model the same list in the same order, or
        a prompt differs between them for no reason a reader could find.
        """
        ...


@runtime_checkable
class SkillRepository(AssetRepository, Protocol):
    """Skills: their names, and the files each one is made of."""

    def files(self, name: str) -> Mapping[str, str]:
        """The files making up one skill, keyed by path relative to the skill.

        `skill.FILENAME` is always among them -- it is what makes a directory a
        skill -- and anything else the skill ships travels with it. Raises
        `KeyError` for a name this does not hold.

        Text, not bytes, where the neighbouring `DefinitionStore.fetch` answers
        in bytes. A skill is read, never re-written. One shipping something
        genuinely binary is decoded lossily rather than refused: failing a whole
        catalogue over one stray image is the worse trade.
        """
        ...


@runtime_checkable
class AgentRepository(AssetRepository, Protocol):
    """Agent definitions, parsed.

    The one kind with no session layer over it. A request may upload skills and
    subagents because those are its own text; an agent decides where every prompt
    in the session goes and is pinned for that session's whole life, so it comes
    from the catalogue or not at all.
    """

    @property
    def specs(self) -> Mapping[str, AgentSpec]:
        """Every agent defined here, by name."""
        ...


@runtime_checkable
class SubagentRepository(AssetRepository, Protocol):
    """Subagent definitions, parsed. Where the text came from is the
    implementation's business."""

    @property
    def specs(self) -> Mapping[str, SubagentSpec]:
        """Every subagent defined here, by name."""
        ...


@runtime_checkable
class ToolRepository(AssetRepository, Protocol):
    """Workspace tools, imported, each with the file it came from.

    The one kind that cannot escape the host filesystem: a tool is Python that
    gets *imported*, and `importlib.spec_from_file_location` needs a real file. An
    implementation backed by anything else has to stage to disk first. That is a
    constraint on the implementation, not on this port.
    """

    @property
    def found(self) -> tuple[Found, ...]:
        """Every tool held, paired with where it is defined."""
        ...


@runtime_checkable
class ThreadStore(Protocol):
    """The checkpointer, seen from the domain: something that forgets a thread."""

    def delete_thread(self, thread_id: str) -> None: ...


@runtime_checkable
class SessionDirs(Protocol):
    """The directories a session and its turns live in.

    `create_exclusive` is the reason this port exists. It must fail rather than
    succeed when the name is taken, because that failure *is* how concurrent turn
    allocation stays correct.
    """

    def ensure(self, path: Path) -> None:
        """Create `path` and any parents. Succeeds if it already exists."""
        ...

    def create_exclusive(self, path: Path) -> bool:
        """Create `path`, or return False if something already holds the name."""
        ...

    def mark_used(self, path: Path) -> None:
        """Record that `path` was used just now.

        A port because the rule depends on it. `retention.expired` reads one
        timestamp to decide a session is idle, and a turn writes *inside* a
        session, which on an ordinary filesystem leaves the session's own
        timestamp alone. Measured: a session was still 10,000s idle by that clock
        immediately after a turn completed in it.
        """
        ...

    def children(self, path: Path) -> tuple[str, ...]:
        """Names of the directories directly inside `path`."""
        ...

    def listing(self, path: Path) -> tuple[tuple[str, float], ...]:
        """`(name, modified_at)` for each directory inside `path`."""
        ...

    def remove_tree(self, path: Path) -> str | None:
        """Delete `path` and its contents. Returns a reason on failure."""
        ...


@runtime_checkable
class DefinitionStore(Protocol):
    """Where a request's own skills and subagents are fetched from, by id.

    One port for both: they differ in where they land rather than in how they
    arrive. Ids stop here -- what the agent sees is the name inside the
    definition, and `capabilities` activates by that name.
    """

    def fetch(self, definition_id: str) -> Mapping[str, bytes]:
        """The files making up one definition, keyed by path relative to it."""
        ...


@runtime_checkable
class SessionStore(Protocol):
    """Where a session's files live when the machine may not keep them.

    A session's directory is gone when the process is, and what a turn produced
    has to be somewhere the next turn can find it.

    **A local directory is a perfectly good implementation of this port.** What
    the constraint forbids is kingfisher *assuming* a local disk, not a deployment
    choosing one.

    Keys are paths relative to the session root, the same vocabulary `artifacts()`
    returns: a caller diffing two turns needs names it can compare, and an
    absolute path names a machine.

    `runtime_checkable` because this is one of the two ports a deployment names
    from configuration, and a name resolved at startup produces an object nothing
    has type-checked. The check is shallow -- method names, not signatures --
    which catches the mistake worth catching: a factory that returned the wrong
    thing entirely, told when it was wired rather than at the first turn that
    tried to save anything.
    """

    def fetch(self, session_id: str) -> Mapping[str, bytes]:
        """Everything this session kept, keyed by path relative to its root.

        Empty for a session the store has never seen, which is not an error: a
        first turn has nothing to restore.
        """
        ...

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files against this session, replacing any it already had.

        The *changed* ones, not all of them. A caller that sends everything each
        time is correct and pays for the whole session on every call; which files
        changed is something only the caller can know cheaply.

        This port deliberately cannot express deletion -- see `forget`.
        """
        ...

    def knows(self, session_id: str) -> bool:
        """Whether this store holds anything for this session.

        A *security* question rather than a convenience one. A supplied session id
        may resume and may not create -- the id is proof the session is the
        caller's only because it cannot be guessed -- and that proof used to be a
        directory. Where the machine may not keep directories, this is what is
        left to ask, and it holds because a caller cannot make a store know an id
        it never saved.
        """
        ...

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent.

        Separate from `save` because deletion is the one operation a caller must
        be unable to perform by accident.
        """
        ...


@runtime_checkable
class FileStore(Protocol):
    """Where a request's files are fetched from, by id.

    A remote caller has no host paths, so `Request.inputs` and `data` name an id
    instead and a store the deployment wired resolves it. Kingfisher never
    receives bytes over its own wire and never holds them beyond the turn that
    asked.

    A mapping rather than plain bytes, so one ref may name a small bundle. The
    keys are paths relative to wherever the files land, and a caller-supplied key
    is exactly what `references.within` refuses to let escape.

    `runtime_checkable` for the reason `SessionStore` is.
    """

    def fetch(self, file_id: str) -> Mapping[str, bytes]:
        """The files this reference names, keyed by path relative to it.

        Raises `references.UnknownReferenceError` for a ref it cannot resolve and
        `references.UnsafeReferenceError` for one that names somewhere it was not
        allowed to. Part of the contract rather than each adapter's own choice: a
        bare `FileNotFoundError` cannot be told from the deployment's own disk
        being wrong, and would answer 500 to a caller's typo.
        """
        ...


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced.

    Kingfisher's own vocabulary rather than the harness's, which would put the
    framework into a contract a deployment implements.

    `exit_code` is not optional. The harness's equivalent allows `None`, and a
    caller deciding whether a command worked has nothing to do with that but
    guess.
    """

    output: str
    exit_code: int
    #: Set when `output` is not all of it. A runner that cuts long output says so,
    #: because a caller cannot tell a truncated result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    One method, and no path in it. The shell backend it sits behind is also the
    filesystem for everything unrouted, so handing over "the shell" would hand
    over file access with it.

    `command` arrives already confined **when the runner is local**, which is the
    default. Applying the confinement stays on kingfisher's side so a runner
    cannot forget to -- but the confinement names paths on *this* host, so a
    runner shipping the command elsewhere must set `local` to False, and then
    receives the command as the model wrote it.
    """

    #: Whether the command runs on this machine.
    #:
    #: Read with a default of True, so an object that never meets a type checker
    #: still gets the safe answer: forgetting it yields *more* confinement than
    #: needed, never less.
    #:
    #: The case this exists for is not the remote one. A runner that adds resource
    #: limits, or runs as another user, or records timings, is still here -- and
    #: under a rule of "a supplied runner means no local fence" every one of those
    #: would quietly lose `sandbox-exec` on macOS.
    local: bool = True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception: `exit_code` 124, the shell's own,
        with output saying so. Raising would make every runner's failure the
        model's problem rather than a tool result it can read and retry.
        """
        ...


class SessionRoot(Protocol):
    """Where one session's files are, for the length of one turn.

    The one directory the agent addresses everything from. `SessionDirs` is the
    neighbour it is easy to confuse this with: that one is the *rules* about
    session directories, this one is *where the directory is*.

    A directory, not a backend. The file tools and the shell are two views of one
    directory, and the harness resolves the root once and checks containment per
    access -- so a provider returns a path and never imports the harness at all.

    The one rule that follows: **a symlink out of the root is refused**, because
    that containment check resolves before it compares. A session has to be a real
    directory, or a mount that presents as one.

    One turn, because a session here deliberately spans machines and a mount held
    between turns assumes the process that made it is still there for the next
    one. A context manager so that what was mounted is released when the turn
    ends, including when it ends badly.

    Kingfisher creates the layout inside what it is handed. A provider that had to
    create `data`, `memory` and the rest would break every time this repository
    adds a directory.

    **Nothing here is ever closed by kingfisher.** Whoever constructs one owns
    shutting it down: kingfisher does not decide when the service stops, so it
    cannot decide when a connection to the storage does. Anything set up per
    *turn* belongs inside `hold`; anything set up when the provider was *built* is
    released by the deployment that built it.
    """

    def hold(self, session_id: str) -> AbstractContextManager[Path]:
        """The directory this session's turn runs in, for as long as it runs.

        Called once per turn, before anything reads or writes the session --
        restoring from the store writes into it, and keeping from it reads it
        afterwards, so both happen inside.
        """
        ...

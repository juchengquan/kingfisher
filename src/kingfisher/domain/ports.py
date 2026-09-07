"""What the domain needs the world to do for it."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.agents.spec import AgentSpec
from kingfisher.subagents.spec import SubagentSpec
from kingfisher.tools.spec import Found


@runtime_checkable
class AssetRepository(Protocol):
    """Something a deployment's definitions can be read from."""

    @property
    def names(self) -> tuple[str, ...]:
        """Every definition held, by the name a request grants it, in a stable order."""
        ...


@runtime_checkable
class SkillRepository(AssetRepository, Protocol):
    """Skills: their names, and the files each one is made of."""

    def files(self, name: str) -> Mapping[str, str]:
        """The files making up one skill, keyed by path relative to the skill."""
        ...


@runtime_checkable
class AgentRepository(AssetRepository, Protocol):
    """Agent definitions, parsed."""

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
    """Workspace tools, imported, each with the file it came from."""

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
    """The directories a session and its turns live in."""

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
    """Where a request's own skills and subagents are fetched from, by id."""

    def fetch(self, definition_id: str) -> Mapping[str, bytes]:
        """The files making up one definition, keyed by path relative to it."""
        ...


@runtime_checkable
class SessionStore(Protocol):
    """Where a session's files live when the machine may not keep them.

    **A local directory is a perfectly good implementation of this port.** What the
    constraint forbids is kingfisher *assuming* a local disk, not a deployment
    choosing one.
    """

    def fetch(self, session_id: str) -> Mapping[str, bytes]:
        """Everything this session kept, keyed by path relative to its root."""
        ...

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files against this session, replacing any it already had."""
        ...

    def knows(self, session_id: str) -> bool:
        """Whether this store holds anything for this session."""
        ...

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent."""
        ...


@runtime_checkable
class FileStore(Protocol):
    """Where a request's files are fetched from, by id."""

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
    """What running one command produced."""

    output: str
    exit_code: int
    #: Set when `output` is not all of it. A runner that cuts long output says so,
    #: because a caller cannot tell a truncated result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    `command` arrives already confined **when the runner is local**, which is the
    default. Applying the confinement stays on kingfisher's side so a runner cannot
    forget to -- but the confinement names paths on *this* host, so a runner shipping
    the command elsewhere must set `local` to False, and then receives the command as
    the model wrote it.
    """

    #: Whether the command runs on this machine.
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

    The one rule that follows: **a symlink out of the root is refused**, because that
    containment check resolves before it compares. A session has to be a real
    directory, or a mount that presents as one.

    **Nothing here is ever closed by kingfisher.** Whoever constructs one owns
    shutting it down: kingfisher does not decide when the service stops, so it cannot
    decide when a connection to the storage does. Anything set up per *turn* belongs
    inside `hold`; anything set up when the provider was *built* is released by the
    deployment that built it.
    """

    def hold(self, session_id: str) -> AbstractContextManager[Path]:
        """The directory this session's turn runs in, for as long as it runs."""
        ...

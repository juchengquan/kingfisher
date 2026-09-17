"""What the domain needs the world to do for it."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from kingfisher.kinds.agents.spec import AgentSpec
from kingfisher.kinds.subagents.spec import SubagentSpec
from kingfisher.kinds.tools.spec import Found


@runtime_checkable
class AssetRepository(Protocol):
    """Something a deployment's definitions can be read from.

    Internal, and not a port a deployment replaces: definitions kept elsewhere are
    staged into directories, and these are what kingfisher builds from them. So each
    repository below declares everything the rest of kingfisher reads from it, and a
    member a repository may not have is declared with what it answers instead -- a
    `getattr` with a default would answer nothing, silently, where it should fail.
    """

    @property
    def names(self) -> tuple[str, ...]:
        """Every definition held, by the name a request grants it, in a stable order."""
        ...

    @property
    def root(self) -> Path | None:
        """The directory these are read from, or `None` for a repository with none."""
        ...


@runtime_checkable
class SkillRepository(AssetRepository, Protocol):
    """Skills, in a directory.

    A directory rather than anything that can hand over files, because deepagents reads
    skills off a filesystem and the shell runs a skill's scripts from where they sit.
    """

    @property
    def root(self) -> Path:
        """The directory the skills are in."""
        ...

    @property
    def misplaced(self) -> tuple[str, ...]:
        """Skills written where deepagents will not look for them, for a listing."""
        ...


@runtime_checkable
class AgentRepository(AssetRepository, Protocol):
    """Agent definitions, parsed."""

    @property
    def specs(self) -> Mapping[str, AgentSpec]:
        """Every agent defined here, by name."""
        ...

    @property
    def documents(self) -> Mapping[str, str]:
        """The text each agent was read from, which is what a session is pinned to."""
        ...

    @property
    def sources(self) -> Mapping[str, str]:
        """Where each agent is defined, by name."""
        ...


@runtime_checkable
class SubagentRepository(AssetRepository, Protocol):
    """Subagent definitions, parsed. Where the text came from is the
    implementation's business."""

    @property
    def specs(self) -> Mapping[str, SubagentSpec]:
        """Every subagent defined here, by name."""
        ...

    @property
    def bundles(self) -> Mapping[str, Bundle]:
        """What each subagent brings of its own, for the ones that bring anything."""
        ...

    @property
    def sources(self) -> Mapping[str, str]:
        """Where each subagent is defined, by name."""
        ...

    @property
    def orphaned_assets(self) -> tuple[str, ...]:
        """Folders holding a bundle's directories that no definition is named for."""
        ...


class Bundle(Protocol):
    """One subagent's own tools and skills: a folder it is named after, or what it
    carried.

    Described here rather than imported, because the one implementation is in a kind's
    `catalogue` and the domain may name a kind's `spec` and nothing below it.
    """

    @property
    def where(self) -> str:
        """The folder, relative to the catalogue, or a label for a carried bundle."""
        ...

    @property
    def root(self) -> Path | None:
        """The folder on this host, or `None` for a bundle that was carried."""
        ...

    @property
    def tools(self) -> ToolRepository | None:
        """This subagent's own tools, when it has any."""
        ...

    @property
    def skills(self) -> Path | None:
        """This subagent's skill directory, when it has one."""
        ...


@runtime_checkable
class ToolRepository(AssetRepository, Protocol):
    """Workspace tools, imported, each with the file it came from."""

    @property
    def found(self) -> tuple[Found, ...]:
        """Every tool held, paired with where it is defined."""
        ...


@runtime_checkable
class MiddlewareRepository(AssetRepository, Protocol):
    """Middleware a workspace defines, as classes a definition may name.

    `classes` rather than `found`, unlike `ToolRepository`: what a caller needs
    is the mapping a registry already is, because the deployment's own registry
    answers the same question and the two are merged. The pair-with-its-file
    lives on the local implementation, where a refusal can reach it.

    The same host constraint `ToolRepository` states applies -- these are Python
    that gets *imported*, so an implementation backed by anything else stages to
    disk first.
    """

    @property
    def classes(self) -> Mapping[str, type]:
        """Every middleware held, by the name a definition would write."""
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

    @property
    def local(self) -> bool:
        """Whether the command runs on this machine. `True` unless a runner says not.

        A read-only property rather than a plain attribute, and the difference is not
        cosmetic: a protocol declaring `local: bool` demands a *settable* one, so an
        adapter written as a frozen dataclass -- the obvious way to write one, and how
        `ReferenceRunner` in the suite is written -- does not satisfy this port. Nothing
        in kingfisher ever assigns to it, so demanding that was asking implementers for
        a guarantee no caller uses.
        """
        return True

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

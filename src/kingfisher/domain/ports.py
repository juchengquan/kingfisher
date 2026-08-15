"""What the domain needs the world to do for it.

Narrow by intention. A port earns its place only where a rule genuinely
depends on a primitive: `allocate_turn` is atomic *because* `mkdir` fails on an
existing name, and expressing that as "scan, then create" in a caller would
reintroduce the race the loop exists to avoid. Where no primitive is load-
bearing, the domain returns a decision instead and the caller acts on it --
`retention.plan` names the sessions to drop and touches nothing.

Protocols rather than base classes: an adapter satisfies these by shape, and
a test satisfies them with a dict.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ThreadStore(Protocol):
    """The checkpointer, seen from the domain: something that forgets a thread."""

    def delete_thread(self, thread_id: str) -> None: ...


@runtime_checkable
class SessionDirs(Protocol):
    """The directories a session and its turns live in.

    `create_exclusive` is the reason this port exists. It must fail rather than
    succeed when the name is taken, because that failure *is* how concurrent
    turn allocation stays correct.
    """

    def ensure(self, path: Path) -> None:
        """Create `path` and any parents. Succeeds if it already exists."""
        ...

    def create_exclusive(self, path: Path) -> bool:
        """Create `path`, or return False if something already holds the name."""
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

    One port for both, because they differ in where they land rather than in
    how they arrive. A skill is several files and a subagent is one, so the
    return is a mapping of relative path to bytes either way.

    Ids stop here. They are how a catalogue service names a definition; what
    the agent sees is the name inside the definition, and `capabilities`
    activates by that name. Neither vocabulary reaches the other.
    """

    def fetch(self, definition_id: str) -> Mapping[str, bytes]:
        """The files making up one definition, keyed by path relative to it."""
        ...

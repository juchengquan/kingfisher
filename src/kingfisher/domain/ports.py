"""What the domain needs the world to do for it.

Narrow by intention. A port earns its place only where a rule genuinely
depends on a primitive: `allocate_turn` is atomic *because* `mkdir` fails on an
existing name, and expressing that as "scan, then create" in a caller would
reintroduce the race the loop exists to avoid. Where no primitive is load-
bearing, the domain returns a decision instead and the caller acts on it --
`retention.expired` names the sessions to drop and touches nothing.

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

    def mark_used(self, path: Path) -> None:
        """Record that `path` was used just now.

        A port because the rule depends on it. `retention.expired` names
        sessions "untouched for longer than X" and reads one timestamp to
        decide -- and a turn writes *inside* a session, into `runs/` and
        `derived/`, which on an ordinary filesystem leaves the session's own
        timestamp alone. Measured: a session was still 10,000s idle by that
        clock immediately after a turn completed in it.
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


class FileStore(Protocol):
    """Where a request's files are fetched from, by id.

    The same shape as `DefinitionStore` and for the same reason. A remote caller
    has no host paths, so `Request.inputs` and `data` cannot express what they
    want -- they name an id instead, and a store the deployment wired resolves
    it. Kingfisher never receives bytes over its own wire and never holds them
    beyond the turn that asked.

    A mapping rather than plain bytes, so one ref may name a small bundle and
    the two ports read alike. The keys are paths relative to wherever the files
    land, and a caller-supplied key is exactly what `layout.within` refuses to
    let escape.

    Ids stop here, as they do for definitions: what the agent sees is a filename
    in `/data` or the turn's `input/`, never the id it was fetched by.
    """

    def fetch(self, file_id: str) -> Mapping[str, bytes]:
        """The files this reference names, keyed by path relative to it.

        Raises `references.UnknownReferenceError` for a ref it cannot resolve
        and `references.UnsafeReferenceError` for one that names somewhere it
        was not allowed to. Part of the contract rather than each adapter's own
        choice: a bare `FileNotFoundError` cannot be told from the deployment's
        own disk being wrong, and would answer 500 to a caller's typo.
        """
        ...

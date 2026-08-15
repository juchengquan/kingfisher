"""The Session aggregate: a conversation and the turns inside it.

Session is the root because that is where the hard invariants cluster — turn
ids unique within a conversation, a turn's inputs confined to its own
directory, and a discarded session taking its thread with it. Workspace is the
context those sessions live in, not a root of its own: an aggregate holding
every file in the project would be a concurrency bottleneck and the
large-aggregate anti-pattern in one.

Retention is deliberately *not* here. Keeping the last N sessions is a policy
*across* sessions, so it is a domain service (`workspace.sweep`) that asks each
session to discard itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain.ports import SessionDirs, ThreadStore


@dataclass(frozen=True)
class Turn:
    """One request within a conversation."""

    session_id: str
    id: str
    directory: Path

    @property
    def virtual_dir(self) -> str:
        """The directory as the agent addresses it — machine-independent."""
        return f"/runs/{self.session_id}/{self.id}"

    @property
    def input_dir(self) -> Path:
        """Files supplied with this request. Never `/data`: they arrive fresh
        each round and leave with the turn."""
        return self.directory / "input"

    @property
    def virtual_input_dir(self) -> str:
        return f"{self.virtual_dir}/input"


@dataclass(frozen=True)
class Session:
    """A conversation. Owns its turns and its own disposal."""

    id: str
    directory: Path

    @classmethod
    def open(cls, workspace: Path, session_id: str, dirs: SessionDirs) -> Session:
        directory = Path(workspace) / "runs" / session_id
        dirs.ensure(directory)
        return cls(id=session_id, directory=directory)

    def allocate_turn(self, dirs: SessionDirs, turn_id: str | None = None) -> Turn:
        """Create the next turn's directory and return it.

        A caller-supplied id wins and is idempotent: the same id returns the
        same directory, so a retried request reuses its turn rather than
        forking a second one. A service should pass its own request id — only
        the caller knows where the request boundary is.

        Otherwise the next sequential id is allocated by `mkdir`, which fails
        if the name is taken. Scanning for the highest id and *then* creating
        it is the race this avoids.
        """
        if turn_id:
            path = self.directory / turn_id
            dirs.ensure(path)
            return Turn(session_id=self.id, id=turn_id, directory=path)

        existing = dirs.children(self.directory)
        number = max(
            (int(n[1:]) for n in existing if n.startswith("t") and n[1:].isdigit()),
            default=0,
        )
        while True:
            number += 1
            candidate = self.directory / f"t{number:03d}"
            if dirs.create_exclusive(candidate):
                return Turn(session_id=self.id, id=candidate.name, directory=candidate)
            # Lost the race for this id; take the next one. The retry lives here
            # rather than in the adapter because it is the rule, not the
            # primitive -- the port only has to refuse a name it cannot claim.

    def discard(self, dirs: SessionDirs, threads: ThreadStore | None = None) -> str | None:
        """Delete this session's thread and directory. Returns a failure, or None.

        There is no transaction across a filesystem and sqlite, so the order is
        chosen to make the surviving failure benign:

          thread first, then directory
            a failure leaves a directory whose thread still exists — the
            session is intact and the next sweep retries it
          directory first, then thread
            a failure leaves a thread pointing at deleted files, which is
            exactly the state that makes an agent cite paths that are not there

        Nothing is half-deleted: if the thread will not go, the directory stays.
        """
        if threads is not None:
            try:
                threads.delete_thread(self.id)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                return f"{self.id}: thread not deleted ({type(exc).__name__})"

        failure = dirs.remove_tree(self.directory)
        return f"{self.id}: {failure}" if failure else None

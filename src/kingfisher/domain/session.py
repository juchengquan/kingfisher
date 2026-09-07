"""The Session aggregate: a conversation and the turns inside it.

Session is the root because that is where the hard invariants cluster -- turn ids
unique within a conversation, a turn's inputs confined to its own directory, and a
discarded session taking its thread with it. Workspace is the context those sessions
live in, not a root of its own: an aggregate holding every file in the project would
be a concurrency bottleneck and the large-aggregate anti-pattern in one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain.ports import SessionDirs, ThreadStore


class UnknownSessionError(ValueError):
    """A request named a session that does not exist."""


class SessionBusyError(ValueError):
    """A turn is already running in this session.

    Two turns on one session share a conversation, and the checkpointer writes it
    whole: both read the same history, both append, and the last write wins.
    Measured, a turn simply vanished -- both callers got an answer and a run
    directory, and the conversation kept no record that one of them happened.
    """


class QuotaExceededError(ValueError):
    """A session is already holding more than the deployment allows."""


@dataclass(frozen=True)
class Turn:
    """One request within a conversation."""

    session_id: str
    id: str
    directory: Path

    @property
    def virtual_dir(self) -> str:
        """The directory as the agent addresses it -- machine-independent."""
        return f"/runs/{self.id}"

    @property
    def shell_dir(self) -> str:
        """The same directory as `execute` addresses it.

        The shell starts in the session root, which is what virtual `/` names, so
        this is `virtual_dir` without its leading slash. Worth a name because the
        agent has to be *told*: measured over ten runs of one task, it passed the
        virtual path to the shell 4 times out of 10, and every one failed with `No
        such file or directory` and cost about three times the whole task to
        recover from. The 6 that started with this form never failed once.
        """
        return self.virtual_dir.lstrip("/")

    @property
    def input_dir(self) -> Path:
        """Files supplied with this request. Never `/data`: they arrive fresh each
        round and leave with the turn."""
        return self.directory / "input"

    @property
    def virtual_input_dir(self) -> str:
        return f"{self.virtual_dir}/input"


def sessions_root(workspace: Path | str) -> Path:
    """Where a workspace keeps its sessions."""
    return Path(workspace) / "sessions"


@dataclass(frozen=True)
class SessionInfo:
    """One session, as something outside kingfisher asks about it."""

    id: str
    #: When a turn last ran here, as a unix timestamp.
    #:
    #: A turn writes *inside* a session, so this timestamp is meaningful only
    #: because a turn now records it explicitly -- left to the filesystem, a
    #: conversation in daily use looked untouched, which is what made retention
    #: sweep live sessions.
    last_used: float


def known(entries: Sequence[tuple[str, float]]) -> tuple[SessionInfo, ...]:
    """Every session there is, most recently used first."""
    return tuple(
        SessionInfo(id=name, last_used=modified)
        for name, modified in sorted(entries, key=lambda entry: -entry[1])
    )

def still_held(
    entries: Sequence[tuple[str, float]], *, stale_after: float, now: float
) -> tuple[str, ...]:
    """Which of these claims somebody could still be holding.

    The rule `claim` applies to one claim, said once so retention can apply it to all
    of them. Treating every claim name as a turn in progress is what let a claim left
    behind by a dead process exempt its session from retention permanently --
    measured at ten years idle, still there.
    """
    return tuple(name for name, taken in entries if now - taken < stale_after)


@dataclass(frozen=True)
class Session:
    """A conversation. Owns its turns and its own disposal."""

    id: str
    directory: Path

    @classmethod
    def open(cls, workspace: Path, session_id: str, dirs: SessionDirs) -> Session:
        """Open (creating if needed) one session's directory."""
        return cls.at(session_id, sessions_root(workspace) / session_id, dirs)

    @classmethod
    def at(cls, session_id: str, directory: Path, dirs: SessionDirs) -> Session:
        """The same, for a directory chosen by something other than a workspace."""
        dirs.ensure(directory)
        return cls(id=session_id, directory=directory)

    @property
    def runs_dir(self) -> Path:
        """Where this session's turns live, one level inside its root."""
        return self.directory / "runs"

    def claim(
        self, dirs: SessionDirs, claims: Path, *, stale_after: float, now: float
    ) -> Path:
        """Take this session's turn slot, or refuse because someone holds it."""
        path = claims / self.id
        if dirs.create_exclusive(path):
            return path

        held = dict(dirs.listing(claims))
        # A claim that vanished between the two calls counts as held: `now` makes
        # its age zero, so a race resolves toward refusing rather than toward
        # taking over a slot whose owner may be about to write.
        mine = ((self.id, held.get(self.id, now)),)
        if self.id in still_held(mine, stale_after=stale_after, now=now):
            msg = (
                f"session {self.id} already has a turn running; "
                f"wait for it to finish or start another session"
            )
            raise SessionBusyError(msg)

        dirs.remove_tree(path)
        if dirs.create_exclusive(path):
            return path
        msg = f"session {self.id} already has a turn running"
        raise SessionBusyError(msg)

    def release(self, dirs: SessionDirs, claims: Path) -> None:
        """Give the slot back. Safe to call when it was never taken."""
        dirs.remove_tree(claims / self.id)

    def allocate_turn(self, dirs: SessionDirs, turn_id: str | None = None) -> Turn:
        """Create the next turn's directory and return it."""
        runs = self.runs_dir
        dirs.ensure(runs)

        if turn_id:
            path = runs / turn_id
            dirs.ensure(path)
            return Turn(session_id=self.id, id=turn_id, directory=path)

        existing = dirs.children(runs)
        number = max(
            (int(n[1:]) for n in existing if n.startswith("t") and n[1:].isdigit()),
            default=0,
        )
        while True:
            number += 1
            candidate = runs / f"t{number:03d}"
            if dirs.create_exclusive(candidate):
                return Turn(session_id=self.id, id=candidate.name, directory=candidate)
            # Lost the race for this id; take the next one. The retry lives here
            # rather than in the adapter because it is the rule, not the primitive
            # -- the port only has to refuse a name it cannot claim.

    def discard(self, dirs: SessionDirs, threads: ThreadStore | None = None) -> str | None:
        """Delete this session's thread and directory. Returns a failure, or None."""
        if threads is not None:
            try:
                threads.delete_thread(self.id)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                return f"{self.id}: thread not deleted ({type(exc).__name__})"

        failure = dirs.remove_tree(self.directory)
        return f"{self.id}: {failure}" if failure else None

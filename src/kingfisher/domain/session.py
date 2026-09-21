"""The Session aggregate: a conversation and the turns inside it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

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
    """One request within a conversation.

    A name and nothing else. It held a directory until `/runs` went: a turn's
    working files are the session's `/scratch` and its outputs the session's
    `/derived`, so there was nothing left for a per-turn folder to hold -- and
    the folders had been the turn counter, which is why the id is now made
    rather than counted.
    """

    session_id: str
    id: str


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

    def claim(
        self, dirs: SessionDirs, path: Path, *, stale_after: float, now: float
    ) -> Path:
        """Take this session's turn slot, or refuse because someone holds it.

        The slot's *path* rather than the directory every session's slot sits in,
        which is what this took while there was one. It is inside the session
        now, and where inside is a layout question -- which this layer does not
        get to ask, for the reason `layout.py` sits outside `domain/`.

        Moving it there removes a failure mode rather than merely tidying: a
        claim can no longer outlive the session it names, so there is nothing to
        sweep and `_discard_dead_claims` goes.
        """
        if dirs.create_exclusive(path):
            return path

        # A claim that vanished between the two calls counts as held: `now` makes
        # its age zero, so a race resolves toward refusing rather than toward
        # taking over a slot whose owner may be about to write.
        held = dict(dirs.listing(path.parent))
        mine = ((path.name, held.get(path.name, now)),)
        if path.name in still_held(mine, stale_after=stale_after, now=now):
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

    def release(self, dirs: SessionDirs, path: Path) -> None:
        """Give the slot back. Safe to call when it was never taken."""
        dirs.remove_tree(path)

    def allocate_turn(self, turn_id: str | None = None) -> Turn:
        """Name the next turn. A caller's own id wins, or one is made.

        It used to read `runs/` and take the number after the highest, which made a
        directory listing the counter and meant a session restored on another host
        -- where nothing restores `runs/` -- silently began again at `t001`. Nothing
        reads the sequence: the id reaches a printed line, the run log and the
        result, and none of them compares two.

        A caller's id is still honoured, because a service passes its own request id
        to tie a run back to the request that asked for it. What it no longer buys is
        de-duplication on retry -- that was `ensure` on the same directory, and the
        directory is gone.
        """
        return Turn(session_id=self.id, id=turn_id or f"t{uuid4().hex[:8]}")

    def discard(self, dirs: SessionDirs, threads: ThreadStore | None = None) -> str | None:
        """Delete this session's thread and directory. Returns a failure, or None."""
        if threads is not None:
            try:
                threads.delete_thread(self.id)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                return f"{self.id}: thread not deleted ({type(exc).__name__})"

        failure = dirs.remove_tree(self.directory)
        return f"{self.id}: {failure}" if failure else None

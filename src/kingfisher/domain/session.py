"""The Session aggregate: a conversation and the turns inside it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4


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
    working files are the session's `/scratchpad` and its outputs the session's
    `/outputs`, so there was nothing left for a per-turn folder to hold -- and
    the folders had been the turn counter, which is why the id is now made
    rather than counted.
    """

    session_id: str
    id: str


def sessions_root(workspace: Path | str) -> Path:
    """Where a workspace keeps its sessions."""
    return Path(workspace) / "sessions"


def unknown_session(session_id: str) -> UnknownSessionError:
    """The refusal for an id nobody issued, and for a session this caller may not touch.
    One wording for both, so that holding a real id teaches nothing -- and for an id
    that could not name a session, which nobody could have issued either.
    """
    return UnknownSessionError(f"no session {session_id!r}; omit session_id to start one")


def is_one_path_segment(session_id: str) -> bool:
    """Whether a session id names one entry in a directory, and nowhere else."""
    # A POSIX file name, and nothing narrower: every folder under `sessions/` is listed
    # as a session, and one whose name this refused could never be opened -- `reap`
    # would stop at it and sweep nothing.
    return session_id not in {"", ".", ".."} and "/" not in session_id and "\0" not in session_id


def session_dir(workspace: Path | str, session_id: str) -> Path:
    """Where a workspace keeps one session."""
    # Here rather than at a door, because every path built from an id is built here --
    # `open` makes folders where it points and `delete` removes them. Joined as given,
    # `""` is every session, `..` the workspace, and an absolute id replaces the path.
    if not is_one_path_segment(session_id):
        raise unknown_session(session_id)
    return sessions_root(workspace) / session_id


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
    """A conversation. Owns its turns and its own disposal.

    An id and nothing else: where a session is kept is its backend's to know, and
    kingfisher's own backend works that out with `session_dir`.
    """

    id: str

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

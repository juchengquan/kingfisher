"""What a session is inside kingfisher: its conversation and turns, the refusals that
name one, and where kingfisher's own sessions keep their folders."""

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


#: The session a request naming none runs in. An ordinary id: naming it reaches the
#: same session, and it is listed and reaped like any other.
DEFAULT_SESSION = "default"


def unknown_session(session_id: str) -> UnknownSessionError:
    """The refusal for a session that is not there, and for one this caller may not
    touch. One wording for both, so that holding a real id teaches nothing.
    """
    return UnknownSessionError(f"no session {session_id!r}")


def unnamable_session(session_id: str) -> UnknownSessionError:
    """The refusal for an id that cannot name a session's folder."""
    return UnknownSessionError(
        f"{session_id!r} cannot name a session: a session id is one name, not starting "
        "with '.', holding neither '/' nor NUL"
    )


def names_a_session(session_id: str) -> bool:
    """Whether an id can name a session's folder: one visible entry in a directory."""
    # One entry and nowhere else: joined as given, `""` is every session, `..` the
    # workspace, and an absolute id replaces the path. Not hidden: `.harness` is the
    # name the macOS profile guards in every session, so a session called that could
    # not write its own files. The listing reads only what passes this, so nothing it
    # lists is a session `open` would refuse and `reap` would stop at.
    return (
        bool(session_id)
        and not session_id.startswith(".")
        and "/" not in session_id
        and "\0" not in session_id
    )


def session_dir(workspace: Path | str, session_id: str) -> Path:
    """Where a workspace keeps one session: a folder of its own under `sessions/`, named
    for its id.
    """
    # Here rather than at a door, because every path built from an id is built here --
    # `open` makes folders where it points and `delete` removes them.
    if not names_a_session(session_id):
        raise unnamable_session(session_id)
    return sessions_root(workspace) / session_id


def known(entries: Sequence[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    """Every `(id, last_used)` the sessions list, most recently used first."""
    return tuple(sorted(entries, key=lambda entry: -entry[1]))


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
class Conversation:
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

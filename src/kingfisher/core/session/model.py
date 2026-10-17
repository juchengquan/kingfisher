"""What a session is inside kingfisher: its conversation and turns, the refusals that
name one, and where kingfisher's own sessions keep their folders."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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


#: The session a request naming none runs in. An ordinary id: naming it reaches the
#: same session, and it is listed and reaped like any other.
DEFAULT_SESSION = "default"


def unknown_session(session_id: str) -> UnknownSessionError:
    """The refusal for a session that is not there, and for one this caller may not
    touch. One wording for both, so that holding a real id teaches nothing.
    """
    return UnknownSessionError(f"no session {session_id!r}")


def known(entries: Sequence[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    """Every `(id, last_used)` the sessions list, most recently used first."""
    return tuple(sorted(entries, key=lambda entry: -entry[1]))


@dataclass(frozen=True)
class Conversation:
    """A conversation. Owns its turns and its own disposal.

    An id and nothing else: where a session is kept is its backend's to know, and
    kingfisher's own keep it under `Config.sessions_root`.
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

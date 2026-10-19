"""What a session is inside kingfisher: its conversation and turns, the refusals that
name one, and where kingfisher's own sessions keep their folders."""

from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary


class UnknownSessionError(ValueError):
    """A request named a session that does not exist."""


class SessionBusyError(ValueError):
    """A turn is already running in this session.

    Two turns on one session share a conversation written whole: both append to the
    same history, the last write wins, and the other turn leaves no record.
    """


class QuotaExceededError(ValueError):
    """A session is already holding more than the deployment allows."""


@dataclass(frozen=True)
class Turn:
    """One request within a conversation."""

    session_id: str
    id: str


#: The session a request naming none runs in: an ordinary id, listed and reaped like any.
DEFAULT_SESSION = "default"


def unknown_session(session_id: str) -> UnknownSessionError:
    """The refusal for a session that is not there, and for one this caller may not
    touch. One wording for both, so that holding a real id teaches nothing.
    """
    return UnknownSessionError(f"no session {session_id!r}")


#: Which session each backend kingfisher opened is the one of. Weak, so a backend the
#: caller drops is not kept open by having been asked about.
_OPENED_FOR: WeakKeyDictionary[Any, str] = WeakKeyDictionary()


def opened_for(files: Any, session_id: str) -> None:
    """Record that `files` is `session_id`'s backend, for `refuse_files_of_another`."""
    # A backend that cannot be weakly keyed goes unrecorded, and is taken at the
    # caller's word as every backend was: refusing it would refuse every `files=`.
    with suppress(TypeError):
        _OPENED_FOR[files] = session_id


def refuse_files_of_another(files: Any, session_id: str) -> None:
    """Refuse a turn in `session_id` handed the backend kingfisher opened for another.

    Run on, it writes this session's conversation into that one and claims this one,
    leaving the other free for a second turn.
    """
    try:
        owner = _OPENED_FOR.get(files)
    except TypeError:
        return
    if owner is not None and owner != session_id:
        msg = (
            f"files= was opened for session {owner!r}, and this request runs in "
            f"{session_id!r}; open the files of the session the request names"
        )
        raise UnknownSessionError(msg)


def known(entries: Sequence[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    """Every `(id, last_used)` the sessions list, most recently used first."""
    return tuple(sorted(entries, key=lambda entry: -entry[1]))


@dataclass(frozen=True)
class Conversation:
    """A conversation: an id and nothing else, since where it is kept is its backend's."""

    id: str

    def allocate_turn(self, turn_id: str | None = None) -> Turn:
        """Name the next turn. A caller's own id wins, or one is made.

        Made rather than counted, since nothing compares two. A caller's id ties a run to
        the request that asked for it, and buys no de-duplication on retry.
        """
        return Turn(session_id=self.id, id=turn_id or f"t{uuid4().hex[:8]}")

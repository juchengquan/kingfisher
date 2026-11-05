"""What a session is inside kingfisher: its conversation and turns, and the refusals
that name one."""

from __future__ import annotations

from collections.abc import Collection, Sequence
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4
from weakref import WeakKeyDictionary


class UnknownSessionError(ValueError):
    """A request named a session that does not exist."""


class TurnIdError(ValueError):
    """A request named a turn id its session already holds."""


class RewindError(ValueError):
    """A rewind named a turn its session cannot be put back to."""


class Start(Enum):
    """Before a session's first turn, as `Session.rewind` is told to go back to it: a value
    of its own, so an unset variable passed as the turn can never empty a session."""

    START = "start"


START = Start.START


class SessionBusyError(ValueError):
    """A turn holds this session: one still running, or one that died before letting go.

    Two turns on one session share a conversation written whole: both append to the
    same history, the last write wins, and the other turn leaves no record.
    """


_MINUTE = 60


def held_by_a_turn(stale_after: float) -> str:
    """Why a claimed session is refused. A claim cannot tell a running turn from a dead
    one, so this says both, and when a dead one's lapses: computed from the bound the
    claim was asked with, since a remote `Sessions` judges age itself."""
    lapse = (
        f"{stale_after / _MINUTE:g} minutes"
        if stale_after >= _MINUTE
        else f"{stale_after:g} seconds"
    )
    return f"held by a turn that is running, or that died and holds it until {lapse} after it began"


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
    """The refusal for a session that is not there."""
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


def most_recent_first(entries: Sequence[tuple[str, float]]) -> tuple[tuple[str, float], ...]:
    """Every `(id, last_used)` the sessions list, most recently used first."""
    return tuple(sorted(entries, key=lambda entry: -entry[1]))


@dataclass(frozen=True)
class Conversation:
    """A conversation: an id and nothing else, since where it is kept is its backend's."""

    id: str

    def allocate_turn(self, turn_id: str | None = None, *, taken: Collection[str] = ()) -> Turn:
        """Name the next turn. A caller's own id wins, or one is made that `taken`, the
        ids the session already holds, does not.

        A caller's id ties a run to the request that asked for it, and buys no
        de-duplication on retry.
        """
        if turn_id:
            return Turn(session_id=self.id, id=turn_id)
        # Drawn again, not trusted: eight hex digits do repeat over enough turns, and the
        # ledger names a turn only by its id.
        while (made := f"t{uuid4().hex[:8]}") in taken:
            pass
        return Turn(session_id=self.id, id=made)

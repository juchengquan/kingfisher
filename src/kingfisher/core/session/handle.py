"""One session, as the caller it was handed to reaches it: what it is, and what can be
asked of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from kingfisher.core.session.steps import adrive, drive

if TYPE_CHECKING:
    from kingfisher.core.result import PendingDecision


@dataclass(frozen=True)
class Session:
    """One session, handed out by `Kingfisher.session` or `Kingfisher.sessions`.

    The caller is checked once, when the session is handed out. Its agent is fixed when
    it opens and the caller's source ids are fixed then, so no method could ask again
    and be answered differently. A session deleted since answers `UnknownSessionError`,
    as a wrong id does -- except to `delete`, which finds nothing left to do.
    """

    id: str
    #: When a turn last ran here, as a unix timestamp.
    #:
    #: A turn writes *inside* a session, so this timestamp is meaningful only
    #: because a turn now records it explicitly -- left to the filesystem, a
    #: conversation in daily use looked untouched, which is what made retention
    #: sweep live sessions.
    last_used: float
    _service: Any = field(repr=False, compare=False)
    #: The backend, once opened -- by the check that handed this out, or by the first
    #: method to need it -- and reused, because on a remote store each opening is a
    #: sandbox, and two per query is what a read used to cost.
    _opened: list[Any] = field(default_factory=list, repr=False, compare=False)

    def pending(self) -> tuple[PendingDecision, ...]:
        """What this session's paused turn is waiting on, or nothing.

        Read from the mark the pause wrote rather than by starting a turn: asking what
        is pending must not be a thing that can supersede it, and every other way into
        the session is a turn.
        """
        return drive(self._service._pending_in(self.id, self._opened))

    async def apending(self) -> tuple[PendingDecision, ...]:
        """`pending`, for a caller on an event loop."""
        return await adrive(self._service._pending_in(self.id, self._opened))

    def artifact(self, name: str) -> bytes:
        """One file a turn here produced, fetched through its backend. `name` is as
        `RunResult.artifacts` gave it."""
        return drive(self._service._artifact_in(self.id, name, self._opened))

    async def aartifact(self, name: str) -> bytes:
        """`artifact`, for a caller on an event loop."""
        return await adrive(self._service._artifact_in(self.id, name, self._opened))

    def files(self) -> Any:
        """The backend a turn here is given, built the way a turn builds it.

        For a caller that reaches the session's files around a turn as well: hand it to
        the turn as `files=` and the turn runs on it rather than opening another, which
        on a remote backend is a second sandbox.
        """
        return drive(self._service._files_in(self.id, self._opened))

    async def afiles(self) -> Any:
        """`files`, for a caller on an event loop."""
        return await adrive(self._service._files_in(self.id, self._opened))

    def size(self) -> int:
        """How many bytes this session holds, as its backend counts them."""
        return drive(self._service._size_in(self.id))

    async def asize(self) -> int:
        """`size`, for a caller on an event loop."""
        return await adrive(self._service._size_in(self.id))

    def delete(self) -> str | None:
        """Dispose of this session and everything its backend holds. A failure, or None."""
        return drive(self._service._delete_session_steps(self.id))

    async def adelete(self) -> str | None:
        """`delete`, for a caller on an event loop."""
        return await adrive(self._service._delete_session_steps(self.id))

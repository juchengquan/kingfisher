"""One session, as the caller it was handed to reaches it: what it is, and what can be
asked of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .steps import adrive, drive

if TYPE_CHECKING:
    from kingfisher.core.result import MemoryEntry, PendingDecision


@dataclass(frozen=True)
class Session:
    """One session, handed out by `Kingfisher.session` or `Kingfisher.sessions`.

    The caller is checked once, when this is handed out: its agent and the caller's
    source ids are fixed by then, so asking again could not answer differently. A session
    deleted since answers `UnknownSessionError`, except to `delete`.
    """

    id: str
    #: When a turn last ran here, as a unix timestamp. Recorded by the turn, not read off
    #: the filesystem, which misses writes inside a session.
    last_used: float
    _service: Any = field(repr=False, compare=False)
    #: The backend, once opened, and reused: on a remote store each opening is a sandbox.
    _opened: list[Any] = field(default_factory=list, repr=False, compare=False)

    def pending(self) -> tuple[PendingDecision, ...]:
        """What this session's paused turn is waiting on, or nothing.

        Read from the pause's mark rather than by starting a turn, which would supersede it.
        """
        return drive(self._service._pending_in(self.id, self._opened))

    async def apending(self) -> tuple[PendingDecision, ...]:
        """`pending`, for a caller on an event loop."""
        return await adrive(self._service._pending_in(self.id, self._opened))

    def artifact(self, name: str) -> bytes:
        """One file a turn here produced, by the name `RunResult.artifacts` gave it."""
        return drive(self._service._artifact_in(self.id, name, self._opened))

    async def aartifact(self, name: str) -> bytes:
        """`artifact`, for a caller on an event loop."""
        return await adrive(self._service._artifact_in(self.id, name, self._opened))

    def memory(self) -> tuple[MemoryEntry, ...]:
        """This session's memory entries, by the ids a turn would give them; none without
        a memory file."""
        return drive(self._service._memory_in(self.id, self._opened))

    async def amemory(self) -> tuple[MemoryEntry, ...]:
        """`memory`, for a caller on an event loop."""
        return await adrive(self._service._memory_in(self.id, self._opened))

    def forget(self, entry_id: str) -> None:
        """Remove one entry from this session's memory file, with the lines continuing it.

        `SessionBusyError` while a turn holds the session, and `MemoryEntryError`, naming
        the ids there are, for one the file does not hold.
        """
        drive(self._service._forget_in(self.id, entry_id, self._opened))

    async def aforget(self, entry_id: str) -> None:
        """`forget`, for a caller on an event loop."""
        await adrive(self._service._forget_in(self.id, entry_id, self._opened))

    def files(self) -> Any:
        """The backend a turn here is given, built the way a turn builds it.

        Handed to the turn as `files=`, the turn runs on it rather than opening another,
        which on a remote backend is a second sandbox.
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

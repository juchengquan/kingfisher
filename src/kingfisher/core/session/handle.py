"""One session, as the caller it was handed to reaches it: what it is, and what can be
asked of it."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .steps import adrive, drive

if TYPE_CHECKING:
    from kingfisher.core.result import MemoryEntry, PendingDecision, Rewound, TurnRecord

    from .model import RewindPart, Start


@dataclass(frozen=True)
class Session:
    """One session, handed out by `Kingfisher.session` or `Kingfisher.sessions`.

    No caller is checked: a session is open to whoever holds its id, and who may hold it
    is the deployment's to settle. A session deleted since answers `UnknownSessionError`,
    except to `delete`.
    """

    id: str
    #: When a turn last ran here, as a unix timestamp. Recorded by the turn, not read off
    #: the filesystem, which misses writes inside a session.
    last_used: float
    _service: Any = field(repr=False, compare=False)
    #: The backend, once opened, and reused: on a remote store each opening is a sandbox.
    _opened: list[Any] = field(default_factory=list, repr=False, compare=False)

    def pending(self) -> tuple[PendingDecision, ...]:
        """What this session's paused turn is waiting on, or nothing, as once a resume has
        begun answering it.

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

    def result(self, entry_id: str) -> str:
        """One tool result's text, by the id `RunResult.ledger` gave it, wherever it is
        kept: its file where its row names one, else the conversation.

        `ResultError` for an id this session has no row for, and for a result kept nowhere
        -- expired, or refused by the backend.
        """
        return drive(self._service._result_in(self.id, entry_id, self._opened))

    async def aresult(self, entry_id: str) -> str:
        """`result`, for a caller on an event loop."""
        return await adrive(self._service._result_in(self.id, entry_id, self._opened))

    def memory(self) -> tuple[MemoryEntry, ...]:
        """This session's memory entries, by the ids a turn would give them; none without
        a memory file."""
        return drive(self._service._memory_in(self.id, self._opened))

    async def amemory(self) -> tuple[MemoryEntry, ...]:
        """`memory`, for a caller on an event loop."""
        return await adrive(self._service._memory_in(self.id, self._opened))

    def forget(self, entry_id: str) -> None:
        """Remove one entry from this session's memory file, with the lines continuing it,
        and from every earlier copy of the file the session keeps to put a turn back; and
        drop what was said since the memory writer last ran, which may say it again.

        `SessionBusyError` while a turn holds the session, and `MemoryEntryError`, naming
        the ids there are, for one the file does not hold. With a `MemoryStore` wired, a
        keyed turn replaces the file with the user's memory: forget there instead.
        """
        drive(self._service._forget_in(self.id, entry_id, self._opened))

    async def aforget(self, entry_id: str) -> None:
        """`forget`, for a caller on an event loop."""
        await adrive(self._service._forget_in(self.id, entry_id, self._opened))

    def turns(self) -> tuple[TurnRecord, ...]:
        """Every turn this session kept a log of, oldest first, including those a rewind
        left behind; `current` marks the path to where the session is now."""
        return drive(self._service._turns_in(self.id, self._opened))

    async def aturns(self) -> tuple[TurnRecord, ...]:
        """`turns`, for a caller on an event loop."""
        return await adrive(self._service._turns_in(self.id, self._opened))

    def rewind(self, to: str | Start, only: RewindPart | None = None) -> Rewound:
        """Put this session back where turn `to` ended, or before its first turn for
        `START`: its conversation, which tool results the next turn is told of, what it
        heard of its inputs folder, and its files in `/outputs` and `/memory`. The turns
        after it are kept, so a rewind to one of them returns. A file whose version there
        has no copy is left as it is, named in `Rewound.not_restored`; before the first
        turn nothing records the files, so a rewind to `START` leaves them all.

        `only="files"` takes back the files alone, and the memory writer's notes with the
        memory file, the conversation staying where it is; `only="conversation"` takes back
        the rest and leaves the files. Either way the next turn tells the agent each file
        that is not as its conversation left it, and records it as its own change.

        A pause waiting on a decision is discarded, as a new request discards it.
        `SessionBusyError` while a turn holds the session; `RewindError` for a turn it
        cannot rebuild: one it never ran, one that wrote no conversation to go back to, or
        one of two turns run under one id that lead back into each other, and for every
        rewind where `KINGFISHER_REWIND_ENABLED` is off; `ValueError` for an `only` other
        than those two.
        """
        return drive(self._service._rewind_in(self.id, to, only, self._opened))

    async def arewind(self, to: str | Start, only: RewindPart | None = None) -> Rewound:
        """`rewind`, for a caller on an event loop."""
        return await adrive(self._service._rewind_in(self.id, to, only, self._opened))

    def files(self) -> Any:
        """The backend a turn here is given, built the way a turn builds it.

        Handed to the turn as `files=`, the turn runs on it rather than opening another,
        which on a remote backend is a second sandbox.
        """
        return drive(self._service._existing_files(self.id, self._opened))

    async def afiles(self) -> Any:
        """`files`, for a caller on an event loop."""
        return await adrive(self._service._existing_files(self.id, self._opened))

    def size(self) -> int:
        """How many bytes this session holds, as its backend counts them."""
        return drive(self._service._size_in(self.id))

    async def asize(self) -> int:
        """`size`, for a caller on an event loop."""
        return await adrive(self._service._size_in(self.id))

    def delete(self, *, force: bool = False) -> str | None:
        """Dispose of this session and everything its backend holds. A failure, or None.

        `SessionBusyError` while a turn holds the session, as `reap` spares it; a claim
        gone stale is a dead turn's, and taken over for the delete. `force` takes any claim
        as a dead turn's: for an operator who knows the turn died, since one still running
        loses its files.
        """
        return drive(self._service._delete_session_steps(self.id, force=force))

    async def adelete(self, *, force: bool = False) -> str | None:
        """`delete`, for a caller on an event loop."""
        return await adrive(self._service._delete_session_steps(self.id, force=force))

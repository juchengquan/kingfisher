"""A session existing: naming one, opening it through its backend, holding its claim,
listing them, and what each `Session` method does."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.layout import MEMORY_WRITER
from kingfisher.core.request import Request, Resume

from .file_history import forget_in_copies, kept_bytes, read_file_history, turn_files
from .files import read_artifact
from .handle import Session
from .ledger import read_result
from .memory import forget_entry, memory_entries, memory_entry
from .memory_writer import unread_dropped
from .model import (
    DEFAULT_SESSION,
    Conversation,
    QuotaExceededError,
    SessionBusyError,
    held_by_a_turn,
    most_recent_first,
    unknown_session,
)
from .rewind import rewound, turn_records
from .steps import Steps, adrive, changing, drive, on_host, reading
from .store import (
    HarnessFiles,
    pending_from_mark,
    read_pause_mark,
)
from .turnlog import read_log

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.config import Config
    from kingfisher.core.result import Rewound, TurnRecord
    from kingfisher.core.versions import Versions

    from .model import RewindPart, Start

class SessionMethods:
    """The service's half of `Session`, and the sessions it can name, open and list."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _versions: Versions
    #: Where the sessions are: which there are, how big, when each was used.
    _sessions: Any
    #: The backend one session's files are reached through, as a sequence.
    _open_files: Callable[..., Steps[Any]]

    def _session_for(self, request: Request | Resume) -> Steps[Conversation]:
        """This turn's session: the one it names, or `DEFAULT_SESSION`; its backend makes
        it the first time it is asked.

        Not checked against the listing, since any id names a session and who may touch
        it is the turn's next question. A resume is checked: its session must be there,
        and opening an absent one would make it.
        """
        if isinstance(request, Resume):
            yield from self._refuse_if_absent(request.session_id)
        return Conversation(id=request.session_id or DEFAULT_SESSION)

    def _refuse_if_absent(self, session_id: str) -> Steps[None]:
        """Refuse an id the sessions do not list, before anything opens it."""
        if session_id not in (yield from self._known_steps()):
            raise unknown_session(session_id)

    def _known_steps(self) -> Steps[tuple[str, ...]]:
        """The id of every session the deployment's backends hold."""
        listing = yield reading(self._sessions, "sessions", self.cfg)
        return tuple(name for name, _ in listing)

    def _refuse_if_over_budget(self, session: Conversation, harness: HarnessFiles) -> Steps[None]:
        """Stop a session that is already too large from growing further."""
        if self.cfg.session_max_bytes is None:
            return
        held = yield reading(self._sessions, "size", self.cfg, session.id)
        if held > self.cfg.session_max_bytes:
            # Less the copies of earlier turns' files, which `rewind_max_bytes` bounds: counted
            # here, a session that rewrote one large report every turn would be refused for
            # the history alone.
            held -= yield from kept_bytes(harness)
        if held > self.cfg.session_max_bytes:
            msg = (
                f"session {session.id} holds {held} bytes, over the "
                f"{self.cfg.session_max_bytes} allowed; delete it or raise the bound"
            )
            raise QuotaExceededError(msg)

    def session(self, session_id: str, *, create: bool = False) -> Session | None:
        """One session, or `None` where there is no such session.

        Whoever holds its id: who may use a session is the deployment's to settle before
        it asks, and no agent or datazone that ran in it decides that here.

        `create` makes a session not there yet, as a turn naming it would, so files can
        be placed before the first turn.
        """
        return drive(self._session_steps(session_id, create=create))

    async def asession(self, session_id: str, *, create: bool = False) -> Session | None:
        """`session`, for a caller on an event loop."""
        return await adrive(self._session_steps(session_id, create=create))

    def sessions(self) -> tuple[Session, ...]:
        """Every session there is, most recently used first: the operator's view, since
        kingfisher cannot know whose a session is."""
        return drive(self._sessions_steps())

    async def asessions(self) -> tuple[Session, ...]:
        """`sessions`, for a caller on an event loop."""
        return await adrive(self._sessions_steps())

    def _session_steps(self, session_id: str, *, create: bool) -> Steps[Session | None]:
        listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
        if create and session_id not in listing:
            catalogue = yield on_host(self._versions.current)
            made = yield from self._open_files(session_id, catalogue=catalogue)
            listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
            return Session(session_id, listing[session_id], self, [made])
        if session_id not in listing:
            return None
        return Session(session_id, listing[session_id], self, [])

    def _sessions_steps(self) -> Steps[tuple[Session, ...]]:
        listing = yield reading(self._sessions, "sessions", self.cfg)
        return tuple(
            Session(name, last_used, self, []) for name, last_used in most_recent_first(listing)
        )

    def _existing_files(self, session_id: str, opened: list[Any]) -> Steps[Any]:
        """The backend of a session still there, opened at most once into `opened`;
        `UnknownSessionError` once it is gone, since opening it again would make it."""
        yield from self._refuse_if_absent(session_id)
        if not opened:
            catalogue = yield on_host(self._versions.current)
            opened.append((yield from self._open_files(session_id, catalogue=catalogue)))
        return opened[0]

    def _pending_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._existing_files(session_id, opened)
        mark = yield from read_pause_mark(HarnessFiles(files))
        return pending_from_mark(mark or {})

    def _artifact_in(self, session_id: str, name: str, opened: list[Any]) -> Steps[bytes]:
        files = yield from self._existing_files(session_id, opened)
        return (yield from read_artifact(files, name, self.cfg.layout))

    def _result_in(self, session_id: str, number: str, opened: list[Any]) -> Steps[str]:
        files = yield from self._existing_files(session_id, opened)
        return (yield from read_result(files, number))

    def _memory_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._existing_files(session_id, opened)
        return (yield from memory_entries(files, self.cfg.layout))

    def _claim_or_refuse(self, session_id: str, then: str) -> Steps[Any]:
        """The session's claim, or `SessionBusyError` ending on `then`, what to do instead."""
        claimed = yield changing(
            self._sessions, "claim", self.cfg, session_id,
            stale_after=self.cfg.claim_stale_after,
        )
        if claimed is None:
            msg = f"session {session_id} is {held_by_a_turn(self.cfg.claim_stale_after)}; {then}"
            raise SessionBusyError(msg)
        return claimed

    def _under_claim[T](
        self, session_id: str, then: str, work: Callable[..., Steps[T]], *args: Any
    ) -> Steps[T]:
        """`work(*args)` under the session's claim, given back however it ends. Handed
        uncalled: a sequence called anywhere but under `yield from` reads as one forgotten."""
        claimed = yield from self._claim_or_refuse(session_id, then)
        try:
            return (yield from work(*args))
        finally:
            yield changing(self._sessions, "release", self.cfg, session_id, claimed)

    def _forget_in(self, session_id: str, entry_id: str, opened: list[Any]) -> Steps[None]:
        files = yield from self._existing_files(session_id, opened)
        # Taken, not asked, as a sweep takes it: a turn claiming between the ask and the
        # write reads the entry at its start and may write it back.
        yield from self._under_claim(
            session_id, f"forget {entry_id} once it is free", self._forgotten, files, entry_id
        )

    def _forgotten(self, files: Any, entry_id: str) -> Steps[None]:
        forgotten = yield from memory_entry(files, self.cfg.layout, entry_id)
        # Before the file itself: failing after it, the forget could not be asked again,
        # as the id would be gone from the file.
        harness = HarnessFiles(files)
        yield from forget_in_copies(harness, self.cfg.layout, forgotten)
        yield from unread_dropped(harness, MEMORY_WRITER)
        yield from forget_entry(files, self.cfg.layout, entry_id)

    def _turns_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[TurnRecord, ...]]:
        harness = HarnessFiles((yield from self._existing_files(session_id, opened)))
        log = yield from read_log(harness)
        files = turn_files((yield from read_file_history(harness)))
        return turn_records(log, files)

    def _rewind_in(
        self, session_id: str, to: str | Start, only: RewindPart | None, opened: list[Any]
    ) -> Steps[Rewound]:
        files = yield from self._existing_files(session_id, opened)
        # Taken, as `forget` takes it: a turn starting halfway would read a conversation
        # from one branch and a ledger from another.
        return (
            yield from self._under_claim(
                session_id, "rewind it once it is free", rewound, files, self.cfg.layout, to, only
            )
        )

    def _size_in(self, session_id: str) -> Steps[int]:
        yield from self._refuse_if_absent(session_id)
        return (yield reading(self._sessions, "size", self.cfg, session_id))

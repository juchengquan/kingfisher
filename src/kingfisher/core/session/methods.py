"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.access import ALL, caller_holds, reaches, with_datazone, zoned
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.request import Request, Resume

from .files import read_artifact
from .handle import Session
from .model import (
    DEFAULT_SESSION,
    Conversation,
    QuotaExceededError,
    known,
    unknown_session,
)
from .steps import Steps, adrive, drive, reading
from .store import (
    HarnessFiles,
    pending_from_mark,
    pinned_in,
    read_pause_mark,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.access import Held, Vocabulary
    from kingfisher.core.config import Config


class SessionMethods:
    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    vocabulary: Vocabulary | None
    #: Where the sessions are: which there are, how big, when each was used.
    _sessions: Any
    #: The backend one session's files are reached through, as a sequence.
    _files_for: Callable[..., Steps[Any]]

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

    def _reaches_session(self, harness: Any, held: frozenset[str] | None) -> Steps[bool]:
        """Whether a caller holding `held` may touch the session `harness` belongs to.

        The rule reading a session and running a turn in it share: a caller who cannot
        reach the pinned agent, or is not in the session's datazone, cannot touch the
        session. `None` (no lock anywhere, or an `UNSCOPED` call) reaches everything, as does
        a session with nothing pinned yet.
        """
        if held is None:
            return True
        kept, zone = yield from pinned_in(harness)
        return self._reaches_pin(kept, zone, held)

    def _reaches_pin(self, kept: Any, zone: str | None, held: frozenset[str] | None) -> bool:
        """`_reaches_session`, for the pins already read."""
        if held is None:
            return True
        return reaches(zoned(ALL if kept is None else read(kept).source_ids, zone), held)

    def _known_steps(self) -> Steps[tuple[str, ...]]:
        """The id of every session the deployment's backends hold."""
        listing = yield reading(self._sessions, "sessions", self.cfg)
        return tuple(name for name, _ in listing)

    def _refuse_if_over_budget(self, session: Conversation) -> Steps[None]:
        """Stop a session that is already too large from growing further."""
        if self.cfg.session_max_bytes is None:
            return
        held = yield reading(self._sessions, "size", self.cfg, session.id)
        if held > self.cfg.session_max_bytes:
            msg = (
                f"session {session.id} holds {held} bytes, over the "
                f"{self.cfg.session_max_bytes} allowed; delete it or raise the bound"
            )
            raise QuotaExceededError(msg)

    def session(
        self,
        session_id: str,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        create: bool = False,
    ) -> Session | None:
        """One session, or `None` when this caller has no such session.

        **A session whose pinned agent this caller cannot reach, or whose datazone they
        are not in, answers `None` too**, so a leaked id confirms nothing.

        `create` makes a session not there yet, as a turn naming it would, so files can
        be placed before the first turn. One out of this caller's reach is still `None`.
        """
        caller = with_datazone(source_ids, datazone)
        return drive(self._session_steps(session_id, caller, create=create))

    async def asession(
        self,
        session_id: str,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        create: bool = False,
    ) -> Session | None:
        """`session`, for a caller on an event loop."""
        caller = with_datazone(source_ids, datazone)
        return await adrive(self._session_steps(session_id, caller, create=create))

    def sessions(
        self, *, source_ids: Held | None = None, datazone: str | None = None
    ) -> tuple[Session, ...]:
        """Every session this caller reaches, most recently used first; every session
        there is when no caller is named. Naming one costs a read per session, of its pins.
        """
        return drive(self._sessions_steps(with_datazone(source_ids, datazone)))

    async def asessions(
        self, *, source_ids: Held | None = None, datazone: str | None = None
    ) -> tuple[Session, ...]:
        """`sessions`, for a caller on an event loop."""
        return await adrive(self._sessions_steps(with_datazone(source_ids, datazone)))

    def _session_steps(
        self, session_id: str, source_ids: Held | None, *, create: bool
    ) -> Steps[Session | None]:
        held = caller_holds(self.vocabulary, source_ids)
        listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
        if create and session_id not in listing:
            made = yield from self._files_for(session_id)
            listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
            # Nothing is pinned in a session this call made, so no caller is out of reach.
            return Session(session_id, listing[session_id], self, [made])
        if session_id not in listing:
            return None
        reached, opened = yield from self._reaches(session_id, held)
        return Session(session_id, listing[session_id], self, opened) if reached else None

    def _sessions_steps(self, source_ids: Held | None) -> Steps[tuple[Session, ...]]:
        # Naming nobody is the operator's view of every session, not a caller under a lock.
        held = None if source_ids is None else caller_holds(self.vocabulary, source_ids)
        listing = yield reading(self._sessions, "sessions", self.cfg)
        found = []
        for name, last_used in known(listing):
            reached, opened = yield from self._reaches(name, held)
            if reached:
                found.append(Session(name, last_used, self, opened))
        return tuple(found)

    def _reaches(
        self, session_id: str, held: frozenset[str] | None
    ) -> Steps[tuple[bool, list[Any]]]:
        """`_reaches_session` by id, and the backend opened to decide it, if one was.

        Not opened where nothing narrows: on a remote backend that is a sandbox opened to
        say a session exists.
        """
        if held is None:
            return True, []
        files = yield from self._files_for(session_id)
        reached = yield from self._reaches_session(HarnessFiles(files), held)
        return reached, [files]

    def _files_in(self, session_id: str, opened: list[Any]) -> Steps[Any]:
        """The backend of a session still there, opened at most once into `opened`;
        `UnknownSessionError` once it is gone, since opening it again would make it."""
        yield from self._refuse_if_absent(session_id)
        if not opened:
            opened.append((yield from self._files_for(session_id)))
        return opened[0]

    def _pending_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._files_in(session_id, opened)
        mark = yield from read_pause_mark(HarnessFiles(files))
        return pending_from_mark(mark or {})

    def _artifact_in(self, session_id: str, name: str, opened: list[Any]) -> Steps[bytes]:
        files = yield from self._files_in(session_id, opened)
        return (yield from read_artifact(files, name, self.cfg.layout))

    def _size_in(self, session_id: str) -> Steps[int]:
        yield from self._refuse_if_absent(session_id)
        return (yield reading(self._sessions, "size", self.cfg, session_id))

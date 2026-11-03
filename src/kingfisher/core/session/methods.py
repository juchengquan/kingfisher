"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from kingfisher.core.access import ALL, caller_holds, reaches, with_datazone, zones_held
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.request import Request, Resume

from .files import read_artifact
from .handle import Session
from .ledger import read_result
from .memory import forget_entry, memory_entries
from .model import (
    DEFAULT_SESSION,
    Conversation,
    QuotaExceededError,
    SessionBusyError,
    held_by_a_turn,
    known,
    unknown_session,
)
from .steps import Steps, adrive, changing, drive, on_host, reading
from .store import (
    HarnessFiles,
    pending_from_mark,
    pinned_in,
    pinned_name,
    read_pause_mark,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from kingfisher.core.access import Held
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.catalogue import Definitions
    from kingfisher.core.versions import Versions

_log = logging.getLogger(__name__)


class SessionMethods:
    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _versions: Versions
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

    def _reaches_session(
        self,
        harness: Any,
        held: frozenset[str] | None,
        *,
        session_id: str,
        catalogue: Definitions,
    ) -> Steps[bool]:
        """Whether a caller holding `held` may touch the session `harness` belongs to.

        The rule reading a session and running a turn in it share: a caller who cannot
        reach the pinned agent, or is not in exactly the session's datazones, cannot touch
        the session. `None` (no lock anywhere, or an `UNSCOPED` call) reaches everything, as does
        a session with nothing pinned yet.
        """
        if held is None:
            return True
        kept, zones = yield from pinned_in(harness)
        return self._reaches_pin(kept, zones, held, session_id=session_id, catalogue=catalogue)

    def _reaches_pin(
        self,
        kept: Any,
        zones: frozenset[str] | None,
        held: frozenset[str] | None,
        *,
        session_id: str,
        catalogue: Definitions,
    ) -> bool:
        """`_reaches_session`, for the pins already read, against the agent as `catalogue`
        defines it now."""
        if held is None:
            return True
        audience = ALL
        if kept is not None:
            try:
                name = pinned_name(kept)
            except AgentError as exc:
                # A pin that names no agent reaches no scoped caller. Raised, it stopped every
                # scoped listing, and told a caller who may not reach the session its lock.
                _log.warning(
                    "session %s: its pin names no agent, so only an unscoped call reaches "
                    "it: %s",
                    session_id,
                    exc,
                )
                return False
            if (spec := catalogue.agents.specs.get(name)) is None:
                # Renamed or deleted, the agent takes its sessions out of every scoped
                # caller's reach; the operator's listing still has them.
                _log.info(
                    "session %s: its agent %r is no longer defined, so only an unscoped "
                    "call reaches it",
                    session_id,
                    name,
                )
                return False
            audience = spec.source_ids
        # The same zones, not every one of them and more: a caller also in another zone would
        # leave what its tools returned here, for callers who are not.
        return reaches(audience, held) and (zones is None or zones_held(held) == zones)

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
        extra_datazones: Sequence[str] = (),
        create: bool = False,
    ) -> Session | None:
        """One session, or `None` when this caller has no such session.

        **A session whose pinned agent this caller cannot reach, or whose datazones are
        not exactly theirs, answers `None` too**, so a leaked id confirms nothing.

        `create` makes a session not there yet, as a turn naming it would, so files can
        be placed before the first turn. One out of this caller's reach is still `None`.
        """
        caller = with_datazone(source_ids, datazone, extra_datazones)
        return drive(self._session_steps(session_id, caller, create=create))

    async def asession(
        self,
        session_id: str,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
        create: bool = False,
    ) -> Session | None:
        """`session`, for a caller on an event loop."""
        caller = with_datazone(source_ids, datazone, extra_datazones)
        return await adrive(self._session_steps(session_id, caller, create=create))

    def sessions(
        self,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
    ) -> tuple[Session, ...]:
        """Every session this caller reaches, most recently used first; every session
        there is when no caller is named. Naming one costs a read per session, of its pins.
        """
        caller = with_datazone(source_ids, datazone, extra_datazones)
        return drive(self._sessions_steps(caller))

    async def asessions(
        self,
        *,
        source_ids: Held | None = None,
        datazone: str | None = None,
        extra_datazones: Sequence[str] = (),
    ) -> tuple[Session, ...]:
        """`sessions`, for a caller on an event loop."""
        caller = with_datazone(source_ids, datazone, extra_datazones)
        return await adrive(self._sessions_steps(caller))

    def _session_steps(
        self, session_id: str, source_ids: Held | None, *, create: bool
    ) -> Steps[Session | None]:
        catalogue = yield on_host(self._versions.current)
        held = caller_holds(catalogue.vocabulary, source_ids)
        listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
        if create and session_id not in listing:
            made = yield from self._files_for(session_id, catalogue=catalogue)
            # Checked although nothing is pinned in a session this call made: a listing
            # that missed one opens a session that was already there.
            if not (yield from self._reaches_session(
                HarnessFiles(made), held, session_id=session_id, catalogue=catalogue
            )):
                return None
            listing = dict((yield reading(self._sessions, "sessions", self.cfg)))
            return Session(session_id, listing[session_id], self, [made])
        if session_id not in listing:
            return None
        reached, opened = yield from self._reaches(session_id, held, catalogue)
        return Session(session_id, listing[session_id], self, opened) if reached else None

    def _sessions_steps(self, source_ids: Held | None) -> Steps[tuple[Session, ...]]:
        # Naming nobody is the operator's view of every session, not a caller under a lock.
        catalogue = yield on_host(self._versions.current)
        held = None if source_ids is None else caller_holds(catalogue.vocabulary, source_ids)
        listing = yield reading(self._sessions, "sessions", self.cfg)
        found = []
        for name, last_used in known(listing):
            reached, opened = yield from self._reaches(name, held, catalogue)
            if reached:
                found.append(Session(name, last_used, self, opened))
        return tuple(found)

    def _reaches(
        self, session_id: str, held: frozenset[str] | None, catalogue: Definitions
    ) -> Steps[tuple[bool, list[Any]]]:
        """`_reaches_session` by id, and the backend opened to decide it, if one was.

        Not opened where nothing narrows: on a remote backend that is a sandbox opened to
        say a session exists.
        """
        if held is None:
            return True, []
        files = yield from self._files_for(session_id, catalogue=catalogue)
        reached = yield from self._reaches_session(
            HarnessFiles(files), held, session_id=session_id, catalogue=catalogue
        )
        return reached, [files]

    def _files_in(self, session_id: str, opened: list[Any]) -> Steps[Any]:
        """The backend of a session still there, opened at most once into `opened`;
        `UnknownSessionError` once it is gone, since opening it again would make it."""
        yield from self._refuse_if_absent(session_id)
        if not opened:
            catalogue = yield on_host(self._versions.current)
            opened.append((yield from self._files_for(session_id, catalogue=catalogue)))
        return opened[0]

    def _pending_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._files_in(session_id, opened)
        mark = yield from read_pause_mark(HarnessFiles(files))
        return pending_from_mark(mark or {})

    def _artifact_in(self, session_id: str, name: str, opened: list[Any]) -> Steps[bytes]:
        files = yield from self._files_in(session_id, opened)
        return (yield from read_artifact(files, name, self.cfg.layout))

    def _result_in(self, session_id: str, number: str, opened: list[Any]) -> Steps[str]:
        files = yield from self._files_in(session_id, opened)
        return (yield from read_result(files, number))

    def _memory_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._files_in(session_id, opened)
        return (yield from memory_entries(files, self.cfg.layout))

    def _forget_in(self, session_id: str, entry_id: str, opened: list[Any]) -> Steps[None]:
        files = yield from self._files_in(session_id, opened)
        # Taken, not asked, as a sweep takes it: a turn claiming between the ask and the
        # write reads the entry at its start and may write it back.
        claimed = yield changing(
            self._sessions, "claim", self.cfg, session_id,
            stale_after=self.cfg.claim_stale_after,
        )
        if claimed is None:
            msg = (
                f"session {session_id} is {held_by_a_turn(self.cfg.claim_stale_after)}; "
                f"forget {entry_id} once it is free"
            )
            raise SessionBusyError(msg)
        try:
            yield from forget_entry(files, self.cfg.layout, entry_id)
        finally:
            yield changing(self._sessions, "release", self.cfg, session_id, claimed)

    def _size_in(self, session_id: str) -> Steps[int]:
        yield from self._refuse_if_absent(session_id)
        return (yield reading(self._sessions, "size", self.cfg, session_id))

"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.agents.reading import read
from kingfisher.domain.access import reaches
from kingfisher.domain.request import Request
from kingfisher.domain.session import (
    QuotaExceededError,
    Session,
    SessionInfo,
    UnknownSessionError,
    known,
    sessions_root,
)
from kingfisher.infrastructure.session_store import restore_into
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout, session_bytes
from kingfisher.infrastructure.workspace.snapshots import agent_snapshot, agent_started_with

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from kingfisher.config import Config
    from kingfisher.domain.access import Groups, Held
    from kingfisher.domain.ports import SessionRoot, SessionStore


class Sessions:
    """A session existing: naming one, making its directory, holding it, listing them."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    access: Groups | None
    dirs: Any
    workspace: Path
    sessions_store: SessionStore | None
    session_root: SessionRoot
    _claims: Path
    _shared: Any

    def _session_id_for(self, request: Request, root: Path) -> str:
        """Mint an id, or accept one that already names a session."""
        if request.session_id is None:
            return uuid4().hex
        if not self._exists(request.session_id, root):
            msg = f"no session {request.session_id!r}; omit session_id to start one"
            raise UnknownSessionError(msg)
        return request.session_id

    def _exists(self, session_id: str, root: Path) -> bool:
        """Whether this id names a session, by directory or by store."""
        if session_id in self.dirs.children(root):
            return True
        return self.sessions_store is not None and self.sessions_store.knows(session_id)

    def _refuse_if_over_budget(self, session: Session) -> None:
        """Stop a session that is already too large from growing further."""
        if self.cfg.session_max_bytes is None:
            return
        held = session_bytes(session.directory)
        if held > self.cfg.session_max_bytes:
            msg = (
                f"session {session.id} holds {held} bytes, over the "
                f"{self.cfg.session_max_bytes} allowed; delete it or raise the bound"
            )
            raise QuotaExceededError(msg)

    def sessions(self) -> tuple[SessionInfo, ...]:
        """Every session in this workspace, most recently used first.

        One `listing` call, measured at 0.22ms for fifty sessions, because it is the
        same call `reap` already makes.
        """
        return known(self.dirs.listing(sessions_root(self.workspace)))

    def session(self, session_id: str, *, groups: Held | None = None) -> SessionInfo | None:
        """One session, or `None` when this caller has no such session.

        Filtered from the same listing rather than stat-ing one path, so both answers
        come from one rule. At fifty sessions that is 0.22ms; it grows with the
        workspace, and a deployment large enough to mind wants an index rather than a
        cheaper stat.

        **A session whose pinned agent this caller cannot reach answers `None` too**,
        and the two states deliberately share one answer. One that said so would be a
        session *confirmed to exist*, so a leaked id would still be worth something
        -- and what an unreachable thing looks like here is, everywhere else, a thing
        that is not there. The reason is not lost; it is what that caller's audit
        line says.
        """
        found = next((s for s in self.sessions() if s.id == session_id), None)
        if found is None or self.access is None or not isinstance(groups, tuple):
            return found
        directory = sessions_root(self.workspace) / session_id
        kept = agent_started_with(directory)
        if kept is None:
            return found
        pinned = read(kept, agent_snapshot(directory))
        return found if reaches(pinned.groups, self.access.expand(groups)) else None

    def start_session(self, session_id: str | None = None) -> str:
        """Open a new session and return its id."""
        session_id = session_id or uuid4().hex
        session = Session.open(self.workspace, session_id, self.dirs)
        ensure_session_layout(session.directory)
        return session_id

    def open_session_for(self, request: Request) -> Session:
        """Name this request's session and make sure its directory exists."""
        root = sessions_root(self.workspace)
        session_id = self._session_id_for(request, root)
        # The session directory has to exist before the agent, because the
        # agent's backend is rooted at it. Creating it before the refusals does
        # not weaken the ordering rule: that rule is about not *destroying*
        # anything before the request is known to be valid, and an empty session
        # directory left by a rejected request is idempotent -- the retry reuses
        # it.
        return self._ready(Session.open(self.workspace, session_id, self.dirs))

    def _ready(self, session: Session) -> Session:
        """A session with its layout made and its files back, wherever it is."""
        ensure_session_layout(session.directory)
        if self.sessions_store is not None:
            restore_into(self.sessions_store, session.id, session.directory)
        return session

    @contextmanager
    def _held_session(self, request: Request) -> Iterator[Session]:
        """This turn's session, in a directory held for exactly as long."""
        session_id = self._session_id_for(request, sessions_root(self.workspace))
        with self.session_root.hold(session_id) as directory:
            yield self._ready(Session.at(session_id, directory, self.dirs))

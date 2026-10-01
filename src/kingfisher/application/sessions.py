"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.application.access import caller_holds
from kingfisher.domain.access import reaches
from kingfisher.domain.request import Request, Resume
from kingfisher.domain.session import (
    QuotaExceededError,
    Session,
    SessionInfo,
    UnknownSessionError,
    known,
    sessions_root,
)
from kingfisher.infrastructure.workspace import (
    agent_started_with,
)
from kingfisher.kinds.agents.reading import read

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

    from kingfisher.config import Config
    from kingfisher.domain.access import Held, SourceIds


class Sessions:
    """A session existing: naming one, making its directory, holding it, listing them."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    access: SourceIds | None
    workspace: Path
    #: Where the sessions are: which there are, how big, when each was used.
    _backends: Any
    #: The backend one session's files are reached through.
    _files_for: Callable[..., Any]
    #: What the instance keeps about one session, through that session's backend.
    _harness_at: Callable[..., Any]

    def _session_id_for(self, request: Request | Resume) -> str:
        """Mint an id, or accept one that already names a session."""
        if request.session_id is None:
            return uuid4().hex
        if not self._exists(request.session_id):
            raise self._unknown_session(request.session_id)
        return request.session_id

    def _unknown_session(self, session_id: str) -> UnknownSessionError:
        """The refusal for an id nobody issued, and for a session this caller may not
        touch. One wording for both, so that holding a real id teaches nothing.
        """
        return UnknownSessionError(f"no session {session_id!r}; omit session_id to start one")

    def _reaches_session(self, harness: Any, held: frozenset[str] | None) -> bool:
        """Whether a caller holding `held` may touch the session `harness` belongs to.

        The one rule reading a session and running a turn in it share: a caller who
        cannot reach the session's pinned agent cannot touch the session. `None` is a
        deployment with no vocabulary or an `UNSCOPED` call and reaches everything, and
        so does a session with nothing pinned yet, which has no agent to be out of reach
        of.
        """
        if held is None:
            return True
        kept = agent_started_with(harness)
        if kept is None:
            return True
        return reaches(read(kept).source_ids, held)

    def _exists(self, session_id: str) -> bool:
        """Whether this id names a session the deployment's backends hold."""
        return any(name == session_id for name, _ in self._backends.sessions(self.cfg))

    def _refuse_if_over_budget(self, session: Session) -> None:
        """Stop a session that is already too large from growing further."""
        if self.cfg.session_max_bytes is None:
            return
        held = self._backends.size(self.cfg, session.id)
        if held > self.cfg.session_max_bytes:
            msg = (
                f"session {session.id} holds {held} bytes, over the "
                f"{self.cfg.session_max_bytes} allowed; delete it or raise the bound"
            )
            raise QuotaExceededError(msg)

    def session_size(self, session_id: str) -> int:
        """How many bytes one session holds, as its backend counts them."""
        return self._backends.size(self.cfg, session_id)

    def sessions(self) -> tuple[SessionInfo, ...]:
        """Every session in this workspace, most recently used first.

        One `listing` call, measured at 0.22ms for fifty sessions, because it is the
        same call `reap` already makes.
        """
        return known(self._backends.sessions(self.cfg))

    def session(self, session_id: str, *, source_ids: Held | None = None) -> SessionInfo | None:
        """One session, or `None` when this caller has no such session.

        **A session whose pinned agent this caller cannot reach answers `None` too**,
        and the two states deliberately share one answer. One that said so would be a
        session *confirmed to exist*, so a leaked id would still be worth something
        -- and what an unreachable thing looks like here is, everywhere else, a thing
        that is not there. The reason is not lost; it is what that caller's audit
        line says.
        """
        reached = self._reached(session_id, source_ids)
        return None if reached is None else reached[0]

    def _reached(
        self, session_id: str, source_ids: Held | None
    ) -> tuple[SessionInfo, Any] | None:
        """`session`'s answer, and the backend that was opened to decide it.

        Handed back for a caller that goes on to read the session: building another
        asks the factory a second time, which for a remote backend is a second sandbox
        per query.

        Filtered from the same listing as `sessions()` rather than stat-ing one path,
        so both answers come from one rule. At fifty sessions that is 0.22ms; it grows
        with the workspace, and a deployment large enough to mind wants an index rather
        than a cheaper stat.
        """
        held = caller_holds(self.access, source_ids)
        found = next((s for s in self.sessions() if s.id == session_id), None)
        if found is None:
            return None
        directory = sessions_root(self.workspace) / session_id
        files = self._files_for(session_id, directory)
        harness = self._harness_at(session_id, directory, files)
        return (found, files) if self._reaches_session(harness, held) else None

    @contextmanager
    def _held_session(self, request: Request | Resume) -> Iterator[Session]:
        """This turn's session. Its backend makes it, the first time it is asked for it."""
        session_id = self._session_id_for(request)
        yield Session(id=session_id, directory=sessions_root(self.workspace) / session_id)

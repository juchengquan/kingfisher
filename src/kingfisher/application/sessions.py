"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

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
)
from kingfisher.infrastructure.session_store import HarnessFiles, agent_started_with
from kingfisher.infrastructure.steps import Steps, adrive, drive, reading
from kingfisher.kinds.agents.reading import read

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.config import Config
    from kingfisher.domain.access import Held, SourceIds


class Sessions:
    """A session existing: naming one, making its directory, holding it, listing them."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    access: SourceIds | None
    #: Where the sessions are: which there are, how big, when each was used.
    _backends: Any
    #: The backend one session's files are reached through, as a sequence.
    _files_for: Callable[..., Steps[Any]]

    def _session_for(self, request: Request | Resume) -> Steps[Session]:
        """This turn's session: a new id, or one that already names a session. Its
        backend makes it, the first time it is asked for it.
        """
        if request.session_id is None:
            session_id = uuid4().hex
        elif request.session_id in (yield from self._known_steps()):
            session_id = request.session_id
        else:
            raise self._unknown_session(request.session_id)
        return Session(id=session_id)

    def _unknown_session(self, session_id: str) -> UnknownSessionError:
        """The refusal for an id nobody issued, and for a session this caller may not
        touch. One wording for both, so that holding a real id teaches nothing.
        """
        return UnknownSessionError(f"no session {session_id!r}; omit session_id to start one")

    def _reaches_session(self, harness: Any, held: frozenset[str] | None) -> Steps[bool]:
        """Whether a caller holding `held` may touch the session `harness` belongs to.

        The one rule reading a session and running a turn in it share: a caller who
        cannot reach the session's pinned agent cannot touch the session. `None` is a
        deployment with no vocabulary or an `UNSCOPED` call and reaches everything, and
        so does a session with nothing pinned yet, which has no agent to be out of reach
        of.
        """
        if held is None:
            return True
        return self._reaches_pin((yield from agent_started_with(harness)), held)

    def _reaches_pin(self, kept: Any, held: frozenset[str] | None) -> bool:
        """`_reaches_session`, for a pin already read."""
        if held is None or kept is None:
            return True
        return reaches(read(kept).source_ids, held)

    def _known_steps(self) -> Steps[tuple[str, ...]]:
        """The id of every session the deployment's backends hold."""
        listing = yield reading(self._backends, "sessions", self.cfg)
        return tuple(name for name, _ in listing)

    def _refuse_if_over_budget(self, session: Session) -> Steps[None]:
        """Stop a session that is already too large from growing further."""
        if self.cfg.session_max_bytes is None:
            return
        held = yield reading(self._backends, "size", self.cfg, session.id)
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
        return drive(self._session_steps(session_id, source_ids))

    async def asession(
        self, session_id: str, *, source_ids: Held | None = None
    ) -> SessionInfo | None:
        """`session`, for a caller on an event loop."""
        return await adrive(self._session_steps(session_id, source_ids))

    def _session_steps(self, session_id: str, source_ids: Held | None) -> Steps[SessionInfo | None]:
        reached = yield from self._reached(session_id, source_ids, files_wanted=False)
        return None if reached is None else reached[0]

    def _reached(
        self, session_id: str, source_ids: Held | None, *, files_wanted: bool
    ) -> Steps[tuple[SessionInfo, Any] | None]:
        """`session`'s answer, and the backend opened to decide it or `None` where
        neither the check nor the caller needed one.

        Handed back for a caller that goes on to read the session: opening another
        asks the session backends a second time, which for a remote backend is a
        second sandbox per query. Not opened where nothing narrows the caller and the
        caller reads nothing, for the same reason: a remote backend would be a sandbox
        opened to say a session exists.

        Filtered from the same listing as `sessions()` rather than stat-ing one path,
        so both answers come from one rule. At fifty sessions that is 0.22ms; it grows
        with the workspace, and a deployment large enough to mind wants an index rather
        than a cheaper stat.
        """
        held = caller_holds(self.access, source_ids)
        listing = yield reading(self._backends, "sessions", self.cfg)
        found = next((s for s in known(listing) if s.id == session_id), None)
        if found is None:
            return None
        if held is None and not files_wanted:
            return found, None
        files = yield from self._files_for(session_id)
        reaches_it = yield from self._reaches_session(HarnessFiles(files, session_id), held)
        return (found, files) if reaches_it else None

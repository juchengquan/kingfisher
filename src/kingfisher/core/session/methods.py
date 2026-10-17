"""A session existing: naming one, making its directory, holding it, listing them."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.access.checks import caller_holds
from kingfisher.core.access.reach import reaches
from kingfisher.core.definitions.agents.reading import read
from kingfisher.core.request import Request, Resume
from kingfisher.core.session.files import read_artifact
from kingfisher.core.session.handle import Session
from kingfisher.core.session.model import (
    DEFAULT_SESSION,
    Conversation,
    QuotaExceededError,
    known,
    unknown_session,
)
from kingfisher.core.session.steps import Steps, adrive, drive, reading
from kingfisher.core.session.store import (
    HarnessFiles,
    agent_started_with,
    pending_from_mark,
    read_pause_mark,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.access.source_ids import Held, SourceIds
    from kingfisher.core.config import Config


class SessionMethods:
    """A session existing: naming one, making its directory, holding it, listing them."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    access: SourceIds | None
    #: Where the sessions are: which there are, how big, when each was used.
    _sessions: Any
    #: The backend one session's files are reached through, as a sequence.
    _files_for: Callable[..., Steps[Any]]

    def _session_for(self, request: Request | Resume) -> Steps[Conversation]:
        """This turn's session: the one it names, or `DEFAULT_SESSION` where it names
        none. Its backend makes it, the first time it is asked for it.

        Not checked against the listing: any id names a session, and a request naming
        one nobody has used starts it. Who may touch one that exists is its pinned
        agent's to decide, which the turn asks next. A resume is the exception: it
        answers a turn that stopped, so the session it names has to be there, and
        opening one to find nothing waiting would make it.
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
        self, session_id: str, *, source_ids: Held | None = None, create: bool = False
    ) -> Session | None:
        """One session, or `None` when this caller has no such session.

        **A session whose pinned agent this caller cannot reach answers `None` too**,
        and the two states deliberately share one answer. One that said so would be a
        session *confirmed to exist*, so a leaked id would still be worth something
        -- and what an unreachable thing looks like here is, everywhere else, a thing
        that is not there. The reason is not lost; it is what that caller's audit
        line says.

        `create` makes a session that is not there yet, as a turn naming it would, so a
        caller can place files before the first turn. One that is there and out of this
        caller's reach is still `None`: made or not, nothing is revealed.
        """
        return drive(self._session_steps(session_id, source_ids, create=create))

    async def asession(
        self, session_id: str, *, source_ids: Held | None = None, create: bool = False
    ) -> Session | None:
        """`session`, for a caller on an event loop."""
        return await adrive(self._session_steps(session_id, source_ids, create=create))

    def sessions(self, *, source_ids: Held | None = None) -> tuple[Session, ...]:
        """Every session this caller reaches, most recently used first; every session
        there is when no caller is named.

        One listing call, measured at 0.22ms for fifty sessions, and the same call `reap`
        makes. Naming a caller costs one more read per session, of the agent it is pinned
        to.
        """
        return drive(self._sessions_steps(source_ids))

    async def asessions(self, *, source_ids: Held | None = None) -> tuple[Session, ...]:
        """`sessions`, for a caller on an event loop."""
        return await adrive(self._sessions_steps(source_ids))

    def _session_steps(
        self, session_id: str, source_ids: Held | None, *, create: bool
    ) -> Steps[Session | None]:
        held = caller_holds(self.access, source_ids)
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
        # Naming nobody is the operator's view of every session, which is what the
        # command's listing is, and not a caller left out under a policy.
        held = None if source_ids is None else caller_holds(self.access, source_ids)
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

        Not opened where nothing narrows: a remote backend would be a sandbox opened to
        say a session exists.
        """
        if held is None:
            return True, []
        files = yield from self._files_for(session_id)
        reached = yield from self._reaches_session(HarnessFiles(files, session_id), held)
        return reached, [files]

    def _files_in(self, session_id: str, opened: list[Any]) -> Steps[Any]:
        """The backend of a session that is still there, opened at most once into
        `opened`; `UnknownSessionError` once the session is gone, rather than opening it
        again, which would make it."""
        yield from self._refuse_if_absent(session_id)
        if not opened:
            opened.append((yield from self._files_for(session_id)))
        return opened[0]

    def _pending_in(self, session_id: str, opened: list[Any]) -> Steps[tuple[Any, ...]]:
        files = yield from self._files_in(session_id, opened)
        mark = yield from read_pause_mark(HarnessFiles(files, session_id))
        return pending_from_mark(mark or {})

    def _artifact_in(self, session_id: str, name: str, opened: list[Any]) -> Steps[bytes]:
        files = yield from self._files_in(session_id, opened)
        return (yield from read_artifact(files, name, self.cfg.layout))

    def _size_in(self, session_id: str) -> Steps[int]:
        yield from self._refuse_if_absent(session_id)
        return (yield reading(self._sessions, "size", self.cfg, session_id))

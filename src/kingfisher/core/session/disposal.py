"""Getting rid of a session: one by name, or every one idle long enough.

A process that died mid-turn once left a session ten years idle and still there, which
is why a claim spares a session only while a turn could still be holding it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from kingfisher.core.session import retention
from kingfisher.core.session.retention import SweepResult
from kingfisher.core.session.steps import Steps, adrive, changing, drive

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.config import Config


class Disposal:
    """Getting rid of a session, and everything it left in other places."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self._sessions` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    _sessions: Any
    _known_steps: Callable[[], Steps[tuple[str, ...]]]

    def delete_session(self, session_id: str) -> str | None:
        """Dispose of one session and everything its backend holds. Returns a failure,
        or None.
        """
        return drive(self._delete_session_steps(session_id))

    async def adelete_session(self, session_id: str) -> str | None:
        """`delete_session`, for a caller on an event loop."""
        return await adrive(self._delete_session_steps(session_id))

    def _delete_session_steps(self, session_id: str) -> Steps[str | None]:
        if session_id not in (yield from self._known_steps()):
            return None
        return (yield from self._discard_steps(session_id))

    def reap(self, older_than_seconds: float | None = None, *, now: float) -> SweepResult:
        """Dispose of every session untouched for `older_than_seconds`.

        A claim only spares a session while somebody could still be holding it. This
        used to read claim names and spare every one, so a process that died mid-turn
        exempted its session from retention for good -- ten years idle and still
        there, measured.
        """
        age = self.cfg.session_ttl_s if older_than_seconds is None else older_than_seconds
        entries = self._sessions.sessions(self.cfg)
        # Twice on purpose: the first answers which sessions are old enough to ask the
        # claim question of, and the second is the plan a running turn has been taken
        # out of. `plan.busy` is the difference, which is what `reap` needs to say why
        # it removed nothing.
        idle = retention.expired(entries, age, now)
        plan = retention.expired(entries, age, now, busy=self._busy(idle.doomed, now=now))
        return retention.apply(plan, lambda session_id: drive(self._discard_steps(session_id)))

    def _discard_steps(self, session_id: str) -> Steps[str | None]:
        """The session, through its backend. A failure, or None."""
        failure = yield changing(self._sessions, "delete", self.cfg, session_id)
        return f"{session_id}: {failure}" if failure else None

    def _busy(self, candidates: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        """Which of these a turn is still running in, so a sweep spares them.

        Asked of the sessions, because the lock is theirs and only they know who holds
        one; and only of the ones old enough to go, because a deployment's own may answer
        it with a round trip each.
        """
        return tuple(
            session_id
            for session_id in candidates
            if self._sessions.held(
                self.cfg, session_id, stale_after=self.cfg.claim_stale_after, now=now
            )
        )

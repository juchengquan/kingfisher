"""Getting rid of a session: one by name, or every one no turn is running in.

A claim spares a session only while a turn could still be holding it: a process that
died mid-turn once left a session ten years idle and still there.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import retention
from .retention import Spared, SweepResult
from .steps import Steps, changing, drive

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.config import Config


class Disposal:
    """Getting rid of a session, and everything it left in other places."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _sessions: Any
    _known_steps: Callable[[], Steps[tuple[str, ...]]]

    def _delete_session_steps(self, session_id: str) -> Steps[str | None]:
        if session_id not in (yield from self._known_steps()):
            return None
        return (yield from self._discard_steps(session_id))

    def reap(self, *, now: float) -> SweepResult:
        """Dispose of every session a turn is not running in."""
        names = tuple(name for name, _ in self._sessions.sessions(self.cfg))
        plan = retention.unheld(names, busy=self._busy(names, now=now))
        return retention.apply(
            plan, lambda session_id: drive(self._swept_steps(session_id, now=now))
        )

    def _swept_steps(self, session_id: str, *, now: float) -> Steps[str | Spared | None]:
        """Delete an idle session under its claim, or spare it where a turn holds that."""
        # Taken, not asked: `held` answering no leaves room for a turn to claim before the
        # delete, which then removes the session under it.
        try:
            claimed = yield changing(
                self._sessions, "claim", self.cfg, session_id,
                stale_after=self.cfg.claim_stale_after, now=now,
            )
        except Exception:
            # Removed since the listing, by another sweep or a delete: what was asked.
            if session_id not in (yield from self._known_steps()):
                return None
            raise
        if claimed is None:
            return Spared.BUSY
        try:
            return (yield from self._discard_steps(session_id))
        finally:
            yield changing(self._sessions, "release", self.cfg, session_id, claimed)

    def _discard_steps(self, session_id: str) -> Steps[str | None]:
        """Delete the session through its backend; a failure, or None."""
        failure = yield changing(self._sessions, "delete", self.cfg, session_id)
        return f"{session_id}: {failure}" if failure else None

    def _busy(self, candidates: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        """Which of these a turn is still running in, so a sweep spares them.

        Asked of the sessions, which hold the lock.
        """
        return tuple(
            session_id
            for session_id in candidates
            if self._sessions.held(
                self.cfg, session_id, stale_after=self.cfg.claim_stale_after, now=now
            )
        )

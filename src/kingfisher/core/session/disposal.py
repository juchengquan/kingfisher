"""Getting rid of a session no turn is running in: one by name, or every one.

A claim spares a session only while a turn could still be holding it: a process that
died mid-turn once left a session ten years idle and still there.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from .model import SessionBusyError, held_by_a_turn
from .steps import Steps, changing, drive

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.config import Config


@dataclass(frozen=True)
class SweepResult:
    """What one reap removed, how many sessions it spared, and what it could not remove."""

    removed: tuple[str, ...]
    #: Sessions a running turn held, and so spared.
    kept: int
    #: Sessions that could not be fully removed, with why: surfaced, not swallowed.
    failures: tuple[str, ...] = ()


class Spared(Enum):
    """What `_swept_steps` answers for a session a turn holds."""

    BUSY = "busy"


class Disposal:
    """Getting rid of a session: one `Sessions.delete`, which removes all it holds."""

    #: Needed from the class this is mixed into; declared, or it is a contract nothing checks.
    cfg: Config
    _sessions: Any
    _known_steps: Callable[[], Steps[tuple[str, ...]]]

    def _delete_session_steps(self, session_id: str, *, force: bool = False) -> Steps[str | None]:
        """Delete a session under its claim, as a sweep does: deleted unclaimed, a running
        turn's files went from under it. `SessionBusyError` where a turn holds it, unless
        `force` takes any claim as a dead turn's."""
        if session_id not in (yield from self._known_steps()):
            return None
        swept = yield from self._swept_steps(
            session_id, now=None, stale_after=0.0 if force else None
        )
        if swept is Spared.BUSY:
            held = held_by_a_turn(self.cfg.claim_stale_after)
            msg = f"session {session_id} is {held}; delete it once it is free"
            raise SessionBusyError(msg)
        return swept

    def reap(self, *, now: float) -> SweepResult:
        """Dispose of every session a turn is not running in."""
        removed: list[str] = []
        failures: list[str] = []
        kept = 0
        for session_id, _ in self._sessions.sessions(self.cfg):
            failure = drive(self._swept_steps(session_id, now=now))
            if failure is Spared.BUSY:
                kept += 1
            elif failure:
                failures.append(failure)
            else:
                removed.append(session_id)
        return SweepResult(removed=tuple(removed), kept=kept, failures=tuple(failures))

    def _swept_steps(
        self, session_id: str, *, now: float | None, stale_after: float | None = None
    ) -> Steps[str | Spared | None]:
        """Delete an idle session under its claim, or spare it where a turn holds that."""
        # Taken, not asked: `held` answering no leaves room for a turn to claim before the
        # delete, which then removes the session under it.
        try:
            claimed = yield changing(
                self._sessions, "claim", self.cfg, session_id,
                stale_after=self.cfg.claim_stale_after if stale_after is None else stale_after,
                now=now,
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

"""Getting rid of a session, and everything it left in other places.

A session is two things in two places: what its backend holds, and a thread in a
checkpointer. Removing one means removing both, and missing one does not fail -- it
accumulates. One real workspace held 132 orphaned threads after every session had been
deleted; a process that died mid-turn left a session ten years idle and still there.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from kingfisher.domain import retention
from kingfisher.domain.retention import SweepResult
from kingfisher.infrastructure.steps import Steps, adrive, changing, drive
from kingfisher.layout import CLAIM

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.config import Config
    from kingfisher.infrastructure.harness.checkpointing import SharedThreads


class Disposal:
    """Getting rid of a session, and everything it left in other places."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self._backends` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    _shared: SharedThreads | None
    _backends: Any
    _files_for: Callable[..., Steps[Any]]
    _known_steps: Callable[[], Steps[tuple[str, ...]]]

    def delete_session(self, session_id: str) -> str | None:
        """Dispose of one session: its thread, and everything its backend holds.
        Returns a failure, or None.
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
        entries = self._backends.sessions(self.cfg)
        idle = retention.expired(entries, age, now)
        plan = retention.expired(entries, age, now, busy=self._busy(idle.doomed, now=now))
        result = retention.apply(plan, lambda session_id: drive(self._discard_steps(session_id)))
        return self._reconcile_threads(result)

    def _discard_steps(self, session_id: str) -> Steps[str | None]:
        """The thread, then the session. A failure, or None.

        The thread first: a session whose files went and whose thread stayed is the
        residue `_reconcile_threads` exists to find, where one whose thread went and
        whose files stayed is only a session that forgot its checkpoint.
        """
        if self._shared is not None:
            try:
                yield changing(self._shared, "forget", session_id)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                return f"{session_id}: thread not deleted ({type(exc).__name__})"
        failure = yield changing(self._backends, "delete", self.cfg, session_id)
        return f"{session_id}: {failure}" if failure else None

    def _busy(self, candidates: tuple[str, ...], *, now: float) -> tuple[str, ...]:
        """Which of these a turn is still running in, so a sweep spares them.

        Asked only of the sessions old enough to go, because the question is asked of
        each one's backend: a lock is the backend's, and only it knows who holds one.
        """
        return tuple(
            session_id
            for session_id in candidates
            if drive(self._files_for(session_id)).held(
                CLAIM, stale_after=self.cfg.claim_stale_after, now=now
            )
        )

    def _reconcile_threads(self, result: SweepResult) -> SweepResult:
        """Delete threads no session owns, and fold them into the result.

        `_discard_steps` takes the thread and the session together, so a swept session
        leaves neither behind. A session that goes any other way -- deleted by hand,
        or one that could not be removed until deleting learned to unlock `/data` --
        leaves its thread forever, because nothing else looks. One real workspace held
        132 such threads and 1,894 checkpoints after every session had been reaped.
        """
        shared = self._shared
        held = None if shared is None else shared.ids()
        if shared is None or held is None:
            return result

        dropped = []
        for thread in retention.orphaned(held, drive(self._known_steps())):
            with suppress(Exception):
                shared.forget(thread)
                dropped.append(thread)
        return replace(result, orphans=tuple(dropped))

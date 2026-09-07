"""Getting rid of a session, and everything it left in other places.

A session is four things in four places: a directory on disk, a thread in a database, a
claim marking a turn in progress, and a copy in whatever durable store a deployment
wired. Removing one means removing all four, and missing one does not fail -- it
accumulates. One real workspace held 132 orphaned threads after every session had been
deleted; a leftover claim made a reopened session refuse its first turn as busy; a
process that died mid-turn left a session ten years idle and still there.
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from kingfisher.domain import retention
from kingfisher.domain.retention import SweepResult
from kingfisher.domain.session import Session, sessions_root, still_held
from kingfisher.infrastructure.harness.checkpointing import thread_ids

if TYPE_CHECKING:
    from pathlib import Path

    from kingfisher.config import Config
    from kingfisher.domain.ports import SessionRoot, SessionStore


class Disposal:
    """Getting rid of a session, and everything it left in other places."""

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    dirs: Any
    workspace: Path
    sessions_store: SessionStore | None
    session_root: SessionRoot
    _claims: Path
    _shared: Any

    def delete_session(self, session_id: str) -> str | None:
        """Dispose of one session and its thread. Returns a failure, or None."""
        root = sessions_root(self.workspace)
        if session_id not in self.dirs.children(root):
            return None
        session = Session(id=session_id, directory=root / session_id)
        failure = session.discard(self.dirs, self._shared)
        session.release(self.dirs, self._claims)
        # And what the store kept, or a deleted session outlives its deletion
        # everywhere that matters. The directory going is the visible half; on a
        # host that may not hold data, the store is the only half that was ever
        # durable.
        self._forget(session_id)
        return failure

    def reap(self, older_than_seconds: float | None = None, *, now: float) -> SweepResult:
        """Dispose of every session untouched for `older_than_seconds`.

        A claim only spares a session while somebody could still be holding it. This
        used to read claim names and spare every one, so a process that died mid-turn
        exempted its session from retention for good -- ten years idle and still
        there, measured.
        """
        root = sessions_root(self.workspace)
        age = self.cfg.session_ttl_s if older_than_seconds is None else older_than_seconds
        plan = retention.expired(
            self.dirs.listing(root),
            age,
            now,
            busy=still_held(
                self.dirs.listing(self._claims),
                stale_after=self.cfg.claim_stale_after,
                now=now,
            ),
        )
        result = retention.apply(plan, root, self.dirs, self._shared)
        result = self._reconcile_threads(root, result)
        self._discard_dead_claims(root)
        # Named by the sweep rather than re-derived. `removed` is what actually
        # went, which is not the same as what the plan asked for -- a session
        # whose directory refused to delete is still there and its store copy
        # has to stay with it, or the next turn would find a directory with no
        # history behind it.
        for gone in result.removed:
            self._forget(gone)
        return result

    def _forget(self, session_id: str) -> None:
        """Drop this session from the store, if a deployment wired one."""
        if self.sessions_store is not None:
            self.sessions_store.forget(session_id)

    def _discard_dead_claims(self, root: Path) -> None:
        """Drop claims whose session no longer exists."""
        gone = retention.orphaned(self.dirs.children(self._claims), self.dirs.children(root))
        for name in gone:
            self.dirs.remove_tree(self._claims / name)

    def _reconcile_threads(self, root: Path, result: SweepResult) -> SweepResult:
        """Delete threads no session owns, and fold them into the result.

        `discard` takes the thread and the directory together, so a swept session
        leaves neither behind. A session directory that goes any other way -- deleted
        by hand, or one of the eight that could not be removed until `remove_tree`
        learned to unlock `/data` -- leaves its thread forever, because nothing else
        looks. One real workspace held 132 such threads and 1,894 checkpoints after
        every session had been reaped.
        """
        held = thread_ids(self._shared)
        if held is None:
            return result

        live = self.dirs.children(root)
        dropped = []
        for thread in retention.orphaned(held, live):
            with suppress(Exception):
                self._shared.delete_thread(thread)
                dropped.append(thread)
        return replace(result, orphans=tuple(dropped))

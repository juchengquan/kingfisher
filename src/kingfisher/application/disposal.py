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
from kingfisher.infrastructure.workspace import claim_path
from kingfisher.layout import CLAIM

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
    _shared: Any

    def delete_session(self, session_id: str, *, forget: bool = True) -> str | None:
        """Dispose of one session: its directory and thread where this workspace keeps
        them, and the store's copy wherever it is. Returns a failure, or None.

        `forget=False` evicts instead: this machine's copy goes and the store's stays,
        so the session can still be resumed -- without `/data`, which the store is
        never handed. The thread goes either way; the next turn is rebuilt from the
        transcript, which the store carries.
        """
        root = sessions_root(self.workspace)
        failure = None
        if session_id in self.dirs.children(root):
            session = Session(id=session_id, directory=root / session_id)
            failure = session.discard(self.dirs, self._shared)
        # The store whether or not a directory was here: under a root of the
        # deployment's own the store is the only place a session is kept, and
        # stopping at the missing directory left a deleted session resumable. Not
        # after a failure, for the reason `reap` gives -- a directory that stayed
        # needs the history behind it.
        if failure is None and forget:
            self._forget(session_id)
        return failure

    def reap(
        self,
        older_than_seconds: float | None = None,
        *,
        now: float,
        forget: bool = True,
    ) -> SweepResult:
        """Dispose of every session untouched for `older_than_seconds`.

        A claim only spares a session while somebody could still be holding it. This
        used to read claim names and spare every one, so a process that died mid-turn
        exempted its session from retention for good -- ten years idle and still
        there, measured.

        `forget=False` evicts, as it does for `delete_session`.
        """
        root = sessions_root(self.workspace)
        age = self.cfg.session_ttl_s if older_than_seconds is None else older_than_seconds
        plan = retention.expired(
            self.dirs.listing(root),
            age,
            now,
            busy=self._busy(root, now=now),
        )
        result = retention.apply(plan, root, self.dirs, self._shared)
        result = self._reconcile_threads(root, result)
        # Named by the sweep rather than re-derived. `removed` is what actually
        # went, which is not the same as what the plan asked for -- a session
        # whose directory refused to delete is still there and its store copy
        # has to stay with it, or the next turn would find a directory with no
        # history behind it.
        if forget:
            for gone in result.removed:
                self._forget(gone)
        return result

    def _forget(self, session_id: str) -> None:
        """Drop this session from the store, if a deployment wired one."""
        if self.sessions_store is not None:
            self.sessions_store.forget(session_id)

    def _busy(self, root: Path, *, now: float) -> tuple[str, ...]:
        """The sessions a turn is still running in, so a sweep spares them.

        One stat per session rather than one listing of a shared claims
        directory, which is what a claim living inside the session it guards
        costs. What it buys is that an orphaned claim cannot exist: it went with
        the session, so there is nothing left to sweep and nothing to sweep it.

        A claim only spares a session while somebody could still be holding it.
        This used to read claim names and spare every one, so a process that died
        mid-turn exempted its session from retention for good -- ten years idle
        and still there, measured.
        """
        held = tuple(
            (session_id, mtime)
            for session_id in self.dirs.children(root)
            for name, mtime in self.dirs.listing(claim_path(root / session_id).parent)
            if name == CLAIM
        )
        return still_held(held, stale_after=self.cfg.claim_stale_after, now=now)

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

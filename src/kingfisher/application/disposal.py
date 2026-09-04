"""Getting rid of a session, and everything it left in other places.

A session is four things in four places: a directory on disk, a thread in a
database, a claim marking a turn in progress, and a copy in whatever durable
store a deployment wired. Removing one means removing all four, and missing
one does not fail -- it accumulates. One real workspace held 132 orphaned
threads after every session had been deleted; a leftover claim made a
reopened session refuse its first turn as busy; a process that died mid-turn
left a session ten years idle and still there.

Two triggers and one job. `delete_session` is asked for, by name;
`reap` is the backstop for callers that never ask, by age. After that they
do the same work and share `_forget`, which is why they are one module
rather than a request-path half and a scheduled half.

**A mixin, not a collaborator, and the distinction is worth stating.** It shares
`self` with everything else on `Kingfisher` -- it can reach any attribute and
call any sibling method, and nothing stops it. What this buys is that a reader
looking for how a session is disposed of opens one file instead of scrolling past a turn; what it
does not buy is a boundary. Written as a separate object it would have needed
five constructor arguments and a delegating method for every public name, and
the public surface is the thing that must not move.

So the contract is written down instead. The attributes below are what this
half needs from the instance it is mixed into; declaring them is what lets `ty`
hold the two together rather than leaving it to whoever reads both.
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
    """Getting rid of a session, and everything it left in other places.

    See the module docstring for why this is a mixin and what it requires.
    """

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
        """Dispose of one session and its thread. Returns a failure, or None.

        Disposal is asked for rather than inferred. Retention used to run on
        the request path and keep the newest N sessions, which counts every
        caller's together -- so a busy caller evicted a quiet one's
        conversation, on a turn that had nothing to do with it.

        The claim goes with it. It used to be left behind, and because
        `start_session` takes a caller's id, re-opening a deleted one inherited
        the leftover and had its first turn refused as busy until the staleness
        window ran out.
        """
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

        The backstop under `delete_session`, for callers that never call it.
        Meant to be run by a janitor on its own schedule, not on a request:
        deleting somebody else's session is not part of serving a turn, and
        putting it there is what made retention a tenancy bug.

        `now` is passed in rather than read, so the decision stays testable
        and this stays a function of its arguments.

        A claim only spares a session while somebody could still be holding it.
        This used to read claim names and spare every one, so a process that
        died mid-turn exempted its session from retention for good -- ten years
        idle and still there, measured.
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
        """Drop this session from the store, if a deployment wired one.

        Deliberately not part of `Session.discard`: that removes a directory and
        a thread, which are things this process owns. A store is somebody
        else's, reached through a port, and a domain object should not know one
        exists.
        """
        if self.sessions_store is not None:
            self.sessions_store.forget(session_id)

    def _discard_dead_claims(self, root: Path) -> None:
        """Drop claims whose session no longer exists.

        `state/claims/` had nothing that emptied it. Bounded here rather than
        by age, because a claim is safe to remove exactly when there is nothing
        left to run a turn against -- taking over a *stale* claim on a session
        that still exists stays with `claim`, where only one `create_exclusive`
        can win the race.

        After the session sweep rather than before, so one pass clears a
        crashed holder completely: the session goes once its claim is too old
        to spare it, which is what makes the claim residue by the time this
        looks. Before it, the session would still exist and the claim would
        survive until the next run.
        """
        gone = retention.orphaned(self.dirs.children(self._claims), self.dirs.children(root))
        for name in gone:
            self.dirs.remove_tree(self._claims / name)

    def _reconcile_threads(self, root: Path, result: SweepResult) -> SweepResult:
        """Delete threads no session owns, and fold them into the result.

        `discard` takes the thread and the directory together, so a swept
        session leaves neither behind. A session directory that goes any other
        way -- deleted by hand, or one of the eight that could not be removed
        until `remove_tree` learned to unlock `/data` -- leaves its thread
        forever, because nothing else looks. One real workspace held 132 such
        threads and 1,894 checkpoints after every session had been reaped.

        After the sweep rather than before, so a session removed by this very
        call is already gone from the listing and its thread is already deleted;
        what is left is genuinely residue.
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

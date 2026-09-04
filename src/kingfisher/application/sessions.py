"""A session existing: naming one, making its directory, holding it, listing them.

Everything about a session that is not a turn running inside it and not its
disposal. Minting or accepting an id, laying the directory out, restoring
what a store kept, holding the tree for exactly the length of a turn, and
answering what exists.

The three on the turn's own path -- `open_session_for`, `_ready` and
`_held_session` -- are here rather than left behind, because they are about
a session existing rather than about a turn, and inheritance means the call
sites do not change to say so.

**A mixin, not a collaborator, and the distinction is worth stating.** It shares
`self` with everything else on `Kingfisher` -- it can reach any attribute and
call any sibling method, and nothing stops it. What this buys is that a reader
looking for what happens before a turn has somewhere to run opens one file
rather than scrolling past a turn; what it does not buy is a boundary.
Written as a separate object it would have needed five constructor arguments
and a delegating method for every public name, and the public surface is the
thing that must not move.

So the contract is written down instead. The attributes below are what this
half needs from the instance it is mixed into; declaring them is what lets `ty`
hold the two together rather than leaving it to whoever reads both.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.domain.access import reaches
from kingfisher.domain.request import Request
from kingfisher.domain.session import (
    QuotaExceededError,
    Session,
    SessionInfo,
    UnknownSessionError,
    known,
    sessions_root,
)
from kingfisher.infrastructure.catalogue.documents import read_agent
from kingfisher.infrastructure.session_store import restore_into
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout, session_bytes
from kingfisher.infrastructure.workspace.snapshots import agent_snapshot, agent_started_with

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from kingfisher.config import Config
    from kingfisher.domain.access import Groups, Held
    from kingfisher.domain.ports import SessionRoot, SessionStore


class Sessions:
    """A session existing: naming one, making its directory, holding it, listing them.

    See the module docstring for why this is a mixin and what it requires.
    """

    #: What this half needs from the instance it is mixed into. Declared rather
    #: than assumed: a mixin that read `self.dirs` without saying so would be a
    #: contract nothing checks, which is the shape this repository distrusts.
    cfg: Config
    access: Groups | None
    dirs: Any
    workspace: Path
    sessions_store: SessionStore | None
    session_root: SessionRoot
    _claims: Path
    _shared: Any

    def _session_id_for(self, request: Request, root: Path) -> str:
        """Mint an id, or accept one that already names a session.

        A supplied id may resume; it may not create. See `UnknownSessionError` --
        the id is what proves a session is the caller's, and it is proof only
        because it cannot be chosen.

        Full `uuid4().hex` rather than the twelve characters this used to take.
        Forty-eight bits is enough to avoid collisions, which is all it was for,
        and far too few for something that opens a conversation and its files.
        """
        if request.session_id is None:
            return uuid4().hex
        if not self._exists(request.session_id, root):
            msg = f"no session {request.session_id!r}; omit session_id to start one"
            raise UnknownSessionError(msg)
        return request.session_id

    def _exists(self, session_id: str, root: Path) -> bool:
        """Whether this id names a session, by directory or by store.

        A directory alone was the answer while the machine was allowed to keep
        one. Where it is not, a session that outlived its container has no
        directory and is not gone -- refusing it here would make the constraint
        and resumption mutually exclusive, which is the whole thing the store
        exists to prevent.

        The proof the check exists for is unchanged. It refuses an id the caller
        invented, and a caller can no more make a store hold an id than make a
        directory appear: both answer only for sessions kingfisher itself
        created.
        """
        if session_id in self.dirs.children(root):
            return True
        return self.sessions_store is not None and self.sessions_store.knows(session_id)

    def _refuse_if_over_budget(self, session: Session) -> None:
        """Stop a session that is already too large from growing further.

        Checked between turns and never during one. `execute` writes without
        any file tool seeing it, so there is nothing to intercept while a turn
        runs -- one already going can exceed the bound, and only a filesystem
        quota underneath could stop it. What this prevents is the next turn
        making it worse.
        """
        if self.cfg.session_max_bytes is None:
            return
        held = session_bytes(session.directory)
        if held > self.cfg.session_max_bytes:
            msg = (
                f"session {session.id} holds {held} bytes, over the "
                f"{self.cfg.session_max_bytes} allowed; delete it or raise the bound"
            )
            raise QuotaExceededError(msg)

    def sessions(self) -> tuple[SessionInfo, ...]:
        """Every session in this workspace, most recently used first.

        The read path a service needs and had no way to reach. Without it the
        only way to learn a session exists was to start a turn and catch
        `UnknownSessionError` -- which builds an agent, marks the session used,
        and takes its claim, so the cheap question had expensive answers.

        One `listing` call, measured at 0.22ms for fifty sessions, because it
        is the same call `reap` already makes.

        Ids and last-used only. Turn counts and disk are knowable and left out:
        nothing needs them yet, and disk is a walk per session that grows with
        what is in them rather than how many there are.
        """
        return known(self.dirs.listing(sessions_root(self.workspace)))

    def session(self, session_id: str, *, groups: Held | None = None) -> SessionInfo | None:
        """One session, or `None` when this caller has no such session.

        `None` rather than raising, because "is this still there" is an ordinary
        question with two ordinary answers. `UnknownSessionError` is for a
        request that named one and meant to use it.

        Filtered from the same listing rather than stat-ing one path, so both
        answers come from one rule. At fifty sessions that is 0.22ms; it grows
        with the workspace, and a deployment large enough to mind wants an
        index rather than a cheaper stat.

        **A session whose pinned agent this caller cannot reach answers `None`
        too**, and the two states deliberately share one answer. One that said
        so would be a session *confirmed to exist*, so a leaked id would still
        be worth something -- and what an unreachable thing looks like here is,
        everywhere else, a thing that is not there. The reason is not lost; it
        is what that caller's audit line says.

        The rule is here rather than in the service so both routes asking it get
        one answer without either growing a branch, and so it sits beside the
        per-turn check it mirrors.

        Read from the *pinned document* rather than the catalogue, which is what
        makes the two agree: a turn resolves the agent this session opened with,
        so this has to ask the same copy. Restricting an agent's `groups:` after
        a session pinned it does not reach back into that session, exactly as
        editing its prompt does not -- and for the same reason.

        A session with nothing pinned is visible. It has no agent to be out of
        reach of -- a one-shot turn creates its session before the turn pins
        anything -- and hiding it would make an id unusable in the window
        between the two.
        """
        found = next((s for s in self.sessions() if s.id == session_id), None)
        if found is None or self.access is None or not isinstance(groups, tuple):
            return found
        kept = agent_started_with(self.cfg.state_dir, session_id)
        if kept is None:
            return found
        pinned = read_agent(kept, agent_snapshot(self.cfg.state_dir, session_id))
        return found if reaches(pinned.groups, self.access.expand(groups)) else None

    def start_session(self, session_id: str | None = None) -> str:
        """Open a new session and return its id.

        The counterpart to `delete_session`, and the only way a session comes
        into existence with a name someone chose. That is the whole of T2: a
        *request* may not create, because its id may have come from whoever is
        calling the service; the service itself may, because it knows who is
        asking. Callers that just want a conversation omit `session_id` on the
        first request and read the id off the result.
        """
        session_id = session_id or uuid4().hex
        session = Session.open(self.workspace, session_id, self.dirs)
        ensure_session_layout(session.directory)
        return session_id

    def open_session_for(self, request: Request) -> Session:
        """Name this request's session and make sure its directory exists.

        Public, and `_graph_for` beside it is not, which is a distinction worth
        stating because it was nearly made the other way. `Kingfisher` inherits
        this from `Sessions` and `_admit` calls it on `self`, so it is reached
        in-tree and by name.

        The argument here used to be a different one: that nothing called it on
        a `Kingfisher`, that every use went through `for_groups`, and that a
        `Caller` handle delegating in was what kept it public. None of those
        three survives -- `for_groups` became `held_for`, the handle is gone,
        and the call is direct -- so what is left is the plain reason.

        Split out of `_admit` because the async path needs the directory before
        it can open anything: an aiosqlite connection belongs to the event loop,
        so it cannot be made inside the worker thread `_admit` runs on, and the
        per-session database lives inside this directory.

        Issuing the id is not idempotent -- an absent one mints a fresh uuid --
        so this runs once and the session is handed onward rather than derived
        twice.
        """
        root = sessions_root(self.workspace)
        session_id = self._session_id_for(request, root)
        # The session directory has to exist before the agent, because the
        # agent's backend is rooted at it. Creating it before the refusals does
        # not weaken the ordering rule: that rule is about not *destroying*
        # anything before the request is known to be valid, and an empty session
        # directory left by a rejected request is idempotent -- the retry reuses
        # it.
        return self._ready(Session.open(self.workspace, session_id, self.dirs))

    def _ready(self, session: Session) -> Session:
        """A session with its layout made and its files back, wherever it is.

        The two halves that have to happen inside a held tree, so they are one
        method rather than repeated beside each way of getting a directory.
        Restoring comes after the layout and before anything reads it: the
        agent's backend is rooted here, so a restore any later would arrive
        after the thing that reads it.
        """
        ensure_session_layout(session.directory)
        if self.sessions_store is not None:
            restore_into(self.sessions_store, session.id, session.directory)
        return session

    @contextmanager
    def _held_session(self, request: Request) -> Iterator[Session]:
        """This turn's session, in a directory held for exactly as long.

        The bracket is wider than the agent on both sides, and that is the whole
        reason it lives here rather than where the backend is built: restoring
        from the store writes into this directory before the turn, and keeping
        from it reads the directory afterwards. A provider that mounts something
        would otherwise be asked to mount it after the restore and unmount it
        before the save.
        """
        session_id = self._session_id_for(request, sessions_root(self.workspace))
        with self.session_root.hold(session_id) as directory:
            yield self._ready(Session.at(session_id, directory, self.dirs))

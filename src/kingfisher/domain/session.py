"""The Session aggregate: a conversation and the turns inside it.

Session is the root because that is where the hard invariants cluster — turn
ids unique within a conversation, a turn's inputs confined to its own
directory, and a discarded session taking its thread with it. Workspace is the
context those sessions live in, not a root of its own: an aggregate holding
every file in the project would be a concurrency bottleneck and the
large-aggregate anti-pattern in one.

Retention is deliberately *not* here. Deciding which sessions to drop is a
policy *across* sessions, so it lives in `domain.retention`, which names them
and asks each to discard itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from kingfisher.domain.ports import SessionDirs, ThreadStore


class UnknownSessionError(ValueError):
    """A request named a session that does not exist.

    Raised rather than creating one. A session id names a conversation and the
    files beside it, so it is a bearer credential: holding one is how a caller
    proves the session is theirs, and they hold one by having started it. If a
    supplied id could create a session, a service that forwarded an id from its
    own caller would let that caller choose -- or guess -- the name, and read
    somebody else's turn.
    """


class SessionBusyError(ValueError):
    """A turn is already running in this session.

    Two turns on one session share a conversation, and the checkpointer writes
    it whole: both read the same history, both append, and the last write wins.
    Measured, a turn simply vanished -- both callers got an answer and a run
    directory, and the conversation kept no record that one of them happened.

    Refused rather than queued. A queue hides a wait that is as long as whatever
    the other turn is doing, and a caller who did not know they were racing
    learns nothing from it. This is the same answer `allocate_turn` gives to a
    taken name, for the same reason.
    """

class QuotaExceededError(ValueError):
    """A session is already holding more than the deployment allows.

    Raised before a turn starts rather than during one. `execute` writes
    without any file tool seeing it, so there is nothing to intercept while a
    turn runs -- a turn already going can exceed the bound, and only a
    filesystem quota underneath could stop it. What this prevents is the *next*
    turn making it worse.
    """


@dataclass(frozen=True)
class Turn:
    """One request within a conversation."""

    session_id: str
    id: str
    directory: Path

    @property
    def virtual_dir(self) -> str:
        """The directory as the agent addresses it — machine-independent.

        No session segment. The session directory *is* the backend root, so
        the agent has no name for it and cannot address outside it; naming it
        here would also put the id into the prompt, changing the cached prefix
        on every session.
        """
        return f"/runs/{self.id}"

    @property
    def shell_dir(self) -> str:
        """The same directory as `execute` addresses it.

        The shell starts in the session root, which is what virtual `/` names,
        so this is `virtual_dir` without its leading slash. Trivial, and worth
        a name because the agent has to be *told*: measured over ten runs of one
        task, it passed the virtual path to the shell 4 times out of 10. Every
        one of those failed with `No such file or directory` and cost about
        three times the whole task -- +5.2 model calls, +19s, +56k input tokens
        -- to recover from. The 6 that started with this form never failed once.
        """
        return self.virtual_dir.lstrip("/")

    @property
    def input_dir(self) -> Path:
        """Files supplied with this request. Never `/data`: they arrive fresh
        each round and leave with the turn."""
        return self.directory / "input"

    @property
    def virtual_input_dir(self) -> str:
        return f"{self.virtual_dir}/input"


@dataclass(frozen=True)
class Session:
    """A conversation. Owns its turns and its own disposal."""

    id: str
    directory: Path

    @classmethod
    def open(cls, workspace: Path, session_id: str, dirs: SessionDirs) -> Session:
        """Open (creating if needed) one session's directory.

        Sessions live under `sessions/`, not `runs/`, because this directory is
        now the backend root: it holds the whole vocabulary the agent addresses
        — `data`, `derived`, `memory` and `runs` — rather than only that
        session's turns.
        """
        directory = Path(workspace) / "sessions" / session_id
        dirs.ensure(directory)
        return cls(id=session_id, directory=directory)

    @property
    def runs_dir(self) -> Path:
        """Where this session's turns live, one level inside its root."""
        return self.directory / "runs"

    def claim(
        self, dirs: SessionDirs, claims: Path, *, stale_after: float, now: float
    ) -> Path:
        """Take this session's turn slot, or refuse because someone holds it.

        Atomic for the same reason turn allocation is: `create_exclusive` fails
        on a name that exists, and that failure *is* the check. Two callers
        racing cannot both win it.

        Held in the store, not in the object. Any process may serve any request
        -- verified: a second instance continues a session the first one
        started -- so a lock in memory would guard one process against itself
        and nothing else.

        A holder that died leaves its claim behind, so a claim older than a turn
        could possibly be is taken over. `stale_after` is the turn timeout,
        which already bounds how long a turn may run: past it, whoever held this
        is gone or was going to be stopped anyway. The takeover is itself
        racy -- two callers can both find it stale -- and safe for the same
        reason as the first attempt, because only one `create_exclusive`
        succeeds.
        """
        path = claims / self.id
        if dirs.create_exclusive(path):
            return path

        held = dict(dirs.listing(claims))
        if now - held.get(self.id, now) < stale_after:
            msg = (
                f"session {self.id} already has a turn running; "
                f"wait for it to finish or start another session"
            )
            raise SessionBusyError(msg)

        dirs.remove_tree(path)
        if dirs.create_exclusive(path):
            return path
        msg = f"session {self.id} already has a turn running"
        raise SessionBusyError(msg)

    def release(self, dirs: SessionDirs, claims: Path) -> None:
        """Give the slot back. Safe to call when it was never taken."""
        dirs.remove_tree(claims / self.id)

    def allocate_turn(self, dirs: SessionDirs, turn_id: str | None = None) -> Turn:
        """Create the next turn's directory and return it.

        A caller-supplied id wins and is idempotent: the same id returns the
        same directory, so a retried request reuses its turn rather than
        forking a second one. A service should pass its own request id — only
        the caller knows where the request boundary is.

        Otherwise the next sequential id is allocated by `mkdir`, which fails
        if the name is taken. Scanning for the highest id and *then* creating
        it is the race this avoids.
        """
        runs = self.runs_dir
        dirs.ensure(runs)

        if turn_id:
            path = runs / turn_id
            dirs.ensure(path)
            return Turn(session_id=self.id, id=turn_id, directory=path)

        existing = dirs.children(runs)
        number = max(
            (int(n[1:]) for n in existing if n.startswith("t") and n[1:].isdigit()),
            default=0,
        )
        while True:
            number += 1
            candidate = runs / f"t{number:03d}"
            if dirs.create_exclusive(candidate):
                return Turn(session_id=self.id, id=candidate.name, directory=candidate)
            # Lost the race for this id; take the next one. The retry lives here
            # rather than in the adapter because it is the rule, not the
            # primitive -- the port only has to refuse a name it cannot claim.

    def discard(self, dirs: SessionDirs, threads: ThreadStore | None = None) -> str | None:
        """Delete this session's thread and directory. Returns a failure, or None.

        There is no transaction across a filesystem and sqlite, so the order is
        chosen to make the surviving failure benign:

          thread first, then directory
            a failure leaves a directory whose thread still exists — the
            session is intact and the next sweep retries it
          directory first, then thread
            a failure leaves a thread pointing at deleted files, which is
            exactly the state that makes an agent cite paths that are not there

        Nothing is half-deleted: if the thread will not go, the directory stays.
        """
        if threads is not None:
            try:
                threads.delete_thread(self.id)
            except Exception as exc:  # noqa: BLE001 -- reported, not swallowed
                return f"{self.id}: thread not deleted ({type(exc).__name__})"

        failure = dirs.remove_tree(self.directory)
        return f"{self.id}: {failure}" if failure else None

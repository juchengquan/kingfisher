"""What the core asks of the world, as interfaces a deployment may implement itself.

`Sessions`, with the `CommandRunner` a session's shell may hand its commands to;
`RunEvents`; and `Instructions`. Middleware is langchain's `AgentMiddleware`, used as it
is, and a wire format is a `WireFormat` in configuration rather than something
implemented. The conversation is not here at all: kingfisher keeps it in the session.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kingfisher.core.session.threads import off_loop, thread_pool

if TYPE_CHECKING:
    from kingfisher.core.config import Config
    from kingfisher.core.definitions.catalogue import Definitions


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced."""

    output: str
    exit_code: int
    #: Set when `output` is not all of it. A runner that cuts long output says so,
    #: because a caller cannot tell a truncated result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    `command` arrives already confined **when the runner is local**, which is the
    default. Applying the confinement stays on kingfisher's side so a runner cannot
    forget to -- but the confinement names paths on *this* host, so a runner shipping
    the command elsewhere must set `local` to False, and then receives the command as
    the model wrote it.

    What one has to keep, held by this repository's `tests/checks/runner.py`: it says
    whether it runs here; a command that works says so; a command that fails, or runs
    out of time, is a result and not an exception; and the result is shaped the way a
    caller reads it.
    """

    @property
    def local(self) -> bool:
        """Whether the command runs on this machine. `True` unless a runner says not.

        A read-only property rather than a plain attribute, and the difference is not
        cosmetic: a protocol declaring `local: bool` demands a *settable* one, so an
        adapter written as a frozen dataclass -- the obvious way to write one, and how
        `ReferenceRunner` in the suite is written -- does not satisfy this port. Nothing
        in kingfisher ever assigns to it, so demanding that was asking implementers for
        a guarantee no caller uses.
        """
        return True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception: `exit_code` 124, the shell's own,
        with output saying so. Raising would make every runner's failure the
        model's problem rather than a tool result it can read and retry.
        """
        ...



@runtime_checkable
class RunEvents(Protocol):
    """Where a record of what each turn did goes: its start and end, every model call,
    every tool call.

    Not session state. It is the one record written as the turn runs rather than at
    its end, and it is most wanted for the turn that crashed, so it goes wherever a
    deployment keeps its logs and outlives the session it describes. Every event
    carries `session_id` and `turn_id`, which is how one session's are found again.
    """

    def record(self, event: Mapping[str, object]) -> None:
        """Keep one event: a flat mapping whose `event` key names what happened.

        Called once per event, and not always from the thread running the turn:
        tool calls a model makes together run at once, on an executor's threads, and
        on the async path langchain hands each event to a worker thread. So the events
        of tool calls made together can interleave, though each call's start comes
        before its end, and a sink that keeps state between events wants a lock. An
        exception here is logged and the turn goes on, because a turn is not failed
        for want of a log line -- which also means a sink that fails is heard from
        only in the log it was failing to write.
        """
        ...


@runtime_checkable
class Sessions(Protocol):
    """How a deployment says where its sessions are, and what each one runs on.

    Asked per turn to `open` the session it is for, and asked the questions only
    something that sees every session can answer: which there are, how big, and
    when each was last used. Keyed by session id rather than by a directory,
    because a backend that runs elsewhere has no directory here to be handed.

    `open` is typed against its call and not its return, which is the asymmetry
    worth knowing. deepagents decides what a backend *is* with `isinstance` against its
    own abstract base class, so a protocol describing the return would describe the
    requirement wrongly -- an object satisfying it exactly would still be handed no
    shell. `refuse_unusable_backend` asks that question at the only time it can be
    answered, which is once there is an object to ask about.

    Each takes the `Config` because the default is one object serving every
    workspace, and the workspace is where it keeps them. A deployment's own may
    ignore it.

    What one has to keep, each held by a check in this repository's
    `tests/checks/sessions.py`:

    - two session ids never share storage;
    - what one turn's backend wrote, the next `open` of that session finds;
    - a session asked for is listed, and one deleted is gone;
    - a claim is exclusive and one session's: a second claim on a held session fails,
      and a claim on another does not;
    - a host path a backend names for a file is that session's file;
    - a session serves under `/skills/` the skills kingfisher read;
    - every async twin answers as its sync one does.

    What else kingfisher reads, it reads where an implementation declares it, by name:
    `where(cfg)` here, the folder these sessions are kept in on this host, for the
    origins line; `host_path`, `shell_prompt` and `unprotected` on a backend `open`
    returns; and `runs_here` on its shell.

    And what `open` returns, held by `tests/checks/backend.py`: deepagents recognises it
    as running commands and it routes every path kingfisher denies -- both refused at
    startup by `refuse_unusable_backend` -- and it reads back what it was given, keeps
    deepagents' offloads in the scratchpad, keeps the shell out of `/.harness` and
    `/inputs`, and refuses a host path.
    """

    def open(
        self,
        cfg: Config,
        session_id: str,
        # Positional-only, or this would be dictating parameter *names*: a protocol
        # matches those, so without the slash a deployment whose `open` reads
        # `(config, which)` fails to satisfy it for no reason anybody could act on.
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        """This session's backend, making the session if it is new."""
        ...

    def sessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        """`(session_id, last_used)` for every session there is."""
        ...

    def mark_used(self, cfg: Config, session_id: str) -> None:
        """Record that a turn is running in this session now.

        A port because retention depends on it. A turn writes *inside* a session, and
        on an ordinary filesystem that leaves the session's own timestamp alone --
        measured, a session was still 10,000s idle by that clock immediately after a
        turn completed in it.
        """
        ...

    def size(self, cfg: Config, session_id: str) -> int:
        """How many bytes this session holds."""
        ...

    def delete(self, cfg: Config, session_id: str) -> str | None:
        """Remove this session and everything in it. A reason on failure, or `None`."""
        ...

    def claim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """Take this session's turn lock, or answer False because a live turn holds it.

        Two turns in one session share a conversation and the last write wins, so this
        is what refuses the second. A claim older than `stale_after` seconds belongs to
        a turn that died, and is taken over; `now` is the clock that is judged by.
        """
        ...

    def release(self, cfg: Config, session_id: str, /) -> None:
        """Give the lock back. Safe where it was never taken."""
        ...

    def held(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """Whether a claim on this session is young enough that its turn may be running."""
        ...

    # The async twins. Written out here, so that a class subclassing this protocol has
    # them as `open` and `sessions` on kingfisher's pool, and overrides one only where
    # its own is a round trip it can await.

    async def aopen(
        self,
        cfg: Config,
        session_id: str,
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        """`open`, for the async path."""
        return await off_loop(thread_pool(), self.open, cfg, session_id, catalogue=catalogue)

    async def asessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        """`sessions`, for the async path."""
        return await off_loop(thread_pool(), self.sessions, cfg)

    async def adelete(self, cfg: Config, session_id: str) -> str | None:
        """`delete`, for the async path."""
        return await off_loop(thread_pool(), self.delete, cfg, session_id)

    async def amark_used(self, cfg: Config, session_id: str) -> None:
        """`mark_used`, for the async path."""
        return await off_loop(thread_pool(), self.mark_used, cfg, session_id)

    async def asize(self, cfg: Config, session_id: str) -> int:
        """`size`, for the async path."""
        return await off_loop(thread_pool(), self.size, cfg, session_id)

    async def aclaim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """`claim`, for the async path."""
        return await off_loop(
            thread_pool(), self.claim, cfg, session_id, stale_after=stale_after, now=now
        )

    async def arelease(self, cfg: Config, session_id: str, /) -> None:
        """`release`, for the async path."""
        return await off_loop(thread_pool(), self.release, cfg, session_id)


class Instructions(Protocol):
    """A deployment's standing text for every agent and subagent it runs.

    Asked once per agent built, so a change reaches the next turn without a restart,
    and read after kingfisher's own prompt and before the agent's: it is the most
    general of the additions, and the agent's own line the most specific. Empty for
    none. What kingfisher tells every agent is not here and not replaceable.
    """

    def text(self, cfg: Config) -> str:
        """The instructions, or an empty string for none."""
        ...

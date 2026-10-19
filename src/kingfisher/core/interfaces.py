"""What the core asks of the world, as interfaces a deployment may implement itself.

Middleware is langchain's `AgentMiddleware` as it is, a wire format is configuration,
and the conversation is kept in the session, so none of them is here.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from kingfisher.core.session.threads import off_loop, thread_pool

if TYPE_CHECKING:
    from kingfisher.core.definitions.catalogue import Definitions

    from .config import Config


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced."""

    output: str
    exit_code: int
    #: Set when `output` is not all of it: a caller cannot tell a cut result from a short one.
    truncated: bool = False


class CommandRunner(Protocol):
    """How a shell command is run, for a deployment that runs them elsewhere.

    `command` arrives already confined **when the runner is local**, the default:
    kingfisher confines it so a runner cannot forget to. The confinement names paths
    on *this* host, so a runner shipping commands elsewhere must set `local` to False,
    and then receives the command as the model wrote it.

    Held by `tests/checks/runner.py`: it says whether it runs here; a command that
    works says so; one that fails or runs out of time is a result, not an exception;
    and the result is shaped the way a caller reads it.
    """

    @property
    def local(self) -> bool:
        """Whether the command runs on this machine. `True` unless a runner says not.

        A property, not an attribute: a protocol declaring `local: bool` demands a
        settable one, which a frozen dataclass adapter is not, and nothing assigns it.
        """
        return True

    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception -- `exit_code` 124, the shell's own,
        with output saying so -- so the model gets a tool result it can read and retry.
        """
        ...



@runtime_checkable
class RunEvents(Protocol):
    """Where a record of each turn goes: its start and end, every model and tool call.

    Not session state: written as the turn runs and most wanted for a turn that
    crashed, so it goes where a deployment keeps its logs and outlives the session.
    Every event carries `session_id` and `turn_id`.
    """

    def record(self, event: Mapping[str, object]) -> None:
        """Keep one event: a flat mapping whose `event` key names what happened.

        Not always called from the turn's thread: tool calls made together run at once
        on an executor, and the async path hands each event to a worker thread. Their
        events can interleave, though each call's start comes before its end, so a sink
        keeping state between events wants a lock. An exception here is logged and the
        turn goes on, so a failing sink is heard from only in the log.
        """
        ...


@runtime_checkable
class Sessions(Protocol):
    """How a deployment says where its sessions are, and what each one runs on.

    Asked per turn to `open` its session, and asked what only something seeing every
    session can answer: which there are, how big, when each was last used. Keyed by
    session id, because a backend that runs elsewhere has no directory here.

    `open` is typed by its call, not its return: deepagents decides what a backend is
    with `isinstance` against its own base class, so an object matching a protocol
    would still get no shell. `refuse_unusable_backend` asks once there is an object.

    Each takes the `Config` because the default serves every workspace and keeps its
    sessions there; a deployment's own may ignore it.

    Held by `tests/checks/sessions.py`:

    - two session ids never share storage;
    - what one turn's backend wrote, the next `open` of that session finds;
    - a session asked for is listed, and one deleted is gone;
    - a claim is exclusive and one session's: a second claim on a held session fails,
      and a claim on another does not;
    - a host path a backend names for a file is that session's file;
    - a session serves under `/skills/` the skills kingfisher read;
    - every async twin answers as its sync one does.

    Anything else is read only where an implementation declares it, by name: `where(cfg)`
    here, the folder these sessions are kept in on this host, for the origins line;
    `host_path`, `shell_prompt` and `unprotected` on a backend `open` returns; and
    `runs_here` on its shell.

    What `open` returns, held by `tests/checks/backend.py`: deepagents recognises it
    as running commands and it routes every path kingfisher denies -- both refused at
    startup by `refuse_unusable_backend` -- and it reads back what it was given, keeps
    deepagents' offloads in the scratchpad, keeps the shell out of `/.harness` and
    `/inputs`, and refuses a host path.
    """

    def open(
        self,
        cfg: Config,
        session_id: str,
        # Positional-only: a protocol matches parameter names, so without the slash
        # an `open` reading `(config, which)` would not satisfy it.
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

        Part of the interface because retention depends on it: a turn writes *inside* a
        session, which on an ordinary filesystem leaves the session's own timestamp alone.
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
    ) -> str | None:
        """Take this session's turn lock and answer a token naming this taker, or `None`
        because a live turn holds it.

        Two turns in one session share a conversation and the last write wins, so this
        refuses the second. A claim older than `stale_after` seconds belongs to a dead
        turn and is taken over -- by one caller only, however many judged it stale at
        once; `now` is the clock that judges it.
        """
        ...

    def release(self, cfg: Config, session_id: str, token: str, /) -> None:
        """Give back the lock `token` took. Safe where it was never taken, and a no-op
        where a later turn holds it: a turn that outlived its claim must not end the
        claim of the turn that took it over."""
        ...

    def held(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> bool:
        """Whether a claim on this session is young enough that its turn may be running."""
        ...

    # Written out so a subclass inherits each twin as its sync one on kingfisher's pool,
    # and overrides only one it can await.

    async def aopen(
        self,
        cfg: Config,
        session_id: str,
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        return await off_loop(thread_pool(), self.open, cfg, session_id, catalogue=catalogue)

    async def asessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        return await off_loop(thread_pool(), self.sessions, cfg)

    async def adelete(self, cfg: Config, session_id: str) -> str | None:
        return await off_loop(thread_pool(), self.delete, cfg, session_id)

    async def amark_used(self, cfg: Config, session_id: str) -> None:
        return await off_loop(thread_pool(), self.mark_used, cfg, session_id)

    async def asize(self, cfg: Config, session_id: str) -> int:
        return await off_loop(thread_pool(), self.size, cfg, session_id)

    async def aclaim(
        self, cfg: Config, session_id: str, /, *, stale_after: float, now: float | None = None
    ) -> str | None:
        return await off_loop(
            thread_pool(), self.claim, cfg, session_id, stale_after=stale_after, now=now
        )

    async def arelease(self, cfg: Config, session_id: str, token: str, /) -> None:
        return await off_loop(thread_pool(), self.release, cfg, session_id, token)


class Instructions(Protocol):
    """A deployment's standing text for every agent and subagent it runs.

    Asked once per agent built, so a change reaches the next turn without a restart.
    Read after kingfisher's own prompt and before the agent's, the most general first.
    What kingfisher tells every agent is not replaceable.
    """

    def text(self, cfg: Config) -> str:
        """The instructions, or an empty string for none."""
        ...

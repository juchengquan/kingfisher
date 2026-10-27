"""What the core asks of the world, as interfaces a deployment may implement itself.

Middleware is langchain's `AgentMiddleware` as it is, a wire format is configuration,
and the conversation is kept in the session, so none of them is here.
"""

from __future__ import annotations

import base64
import inspect
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, get_args

from kingfisher.core.session.threads import off_loop, thread_pool

if TYPE_CHECKING:
    from kingfisher.core.agent.tool_context import Caller
    from kingfisher.core.definitions.catalogue import Definitions
    from kingfisher.core.session.unused import UnusedFiles

    from .config import Config
    from .result import MemoryChanges, MemoryEntry


@dataclass(frozen=True)
class CommandResult:
    """What running one command produced."""

    output: str
    exit_code: int
    #: Set when `output` is not all of it: a caller cannot tell a cut result from a short one.
    truncated: bool = False


#: What one scratch run may carry in, and again what it may bring back. A backend hands
#: files over whole, into kingfisher's own process, so the bound is set before the run.
MAX_SCRATCH_BYTES = 32 * 1024 * 1024

#: How many files one scratch run may name to bring back.
MAX_SCRATCH_OUTPUTS = 64


@dataclass(frozen=True)
class ScratchRun:
    """What a command run over files in a throwaway folder produced.

    Refused is `error`, never an exception, as a backend reports everything else: then
    nothing ran.
    """

    output: str = ""
    exit_code: int = 1
    #: Set when `output` is not all of it.
    truncated: bool = False
    #: The outputs asked for that came back, by name.
    files: Mapping[str, bytes] = field(default_factory=dict)
    #: The outputs asked for that did not, by name, each with why.
    missing: Mapping[str, str] = field(default_factory=dict)
    error: str | None = None


class CommandRunner(ABC):
    """How a shell command is run, for a deployment that runs them elsewhere.

    `command` arrives already confined **when the runner is local**, the default:
    kingfisher confines it so a runner cannot forget to. The confinement names paths
    on *this* host, so a runner shipping commands elsewhere must set `local` to False,
    and then receives the command as the model wrote it.

    Held by `tests/checks/runner.py`: it says whether it runs here; a command that
    works says so; one that fails or runs out of time is a result, not an exception;
    the result is shaped the way a caller reads it; and nothing a command starts
    outlives it.
    """

    #: Whether the command runs on this machine. `True` unless a runner says not.
    local: bool = True

    @abstractmethod
    def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
        """Run `command`, giving up after `timeout` seconds if one is given.

        A timeout is a result, not an exception -- `exit_code` 124, the shell's own,
        with output saying so -- so the model gets a tool result it can read and retry.
        """
        ...



class RunEvents(ABC):
    """Where a record of each turn goes: its start and end, every model and tool call.

    Not session state: written as the turn runs and most wanted for a turn that
    crashed, so it goes where a deployment keeps its logs and outlives the session.
    Every event carries `session_id` and `turn_id`.
    """

    @abstractmethod
    def record(self, event: Mapping[str, object]) -> None:
        """Keep one event: a flat mapping whose `event` key names what happened.

        Not always called from the turn's thread: tool calls made together run at once
        on an executor, and the async path hands each event to a worker thread. Their
        events can interleave, though each call's start comes before its end, so a sink
        keeping state between events wants a lock. An exception here is logged and the
        turn goes on, so a failing sink is heard from only in the log.
        """
        ...


class Sessions(ABC):
    """How a deployment says where its sessions are, and what each one runs on.

    Asked per turn to `open` its session, and asked what only something seeing every
    session can answer: which there are, how big, when each was last used. Keyed by
    session id, because a backend that runs elsewhere has no directory here.

    `open` is typed by its call, not its return: deepagents decides what a backend is
    with `isinstance` against its own base class, so an object with a backend's
    methods would still get no shell. `refuse_unusable_backend` asks once there is an object.

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
    deepagents' own files in the scratchpad, keeps the shell out of `/.harness` and
    `/inputs`, and refuses a host path.
    """

    @abstractmethod
    def open(
        self,
        cfg: Config,
        session_id: str,
        # Positional-only, so an override may name them as it likes: `(config, which)`.
        /,
        *,
        catalogue: Definitions | None = None,
    ) -> Any:
        """This session's backend, making the session if it is new."""
        ...

    @abstractmethod
    def sessions(self, cfg: Config) -> tuple[tuple[str, float], ...]:
        """`(session_id, last_used)` for every session there is.

        One left out is not merely unlisted: its files are refused, a sweep never reaps it,
        and a turn in it takes it for a session it made, deleted if refused after its claim
        unless an earlier turn pinned it.
        """
        ...

    @abstractmethod
    def mark_used(self, cfg: Config, session_id: str) -> None:
        """Record that a turn is running in this session now.

        Part of the interface because retention depends on it: a turn writes *inside* a
        session, which on an ordinary filesystem leaves the session's own timestamp alone.
        """
        ...

    @abstractmethod
    def size(self, cfg: Config, session_id: str) -> int:
        """How many bytes this session holds."""
        ...

    @abstractmethod
    def delete(self, cfg: Config, session_id: str) -> str | None:
        """Remove this session and everything in it. A reason on failure, or `None`."""
        ...

    @abstractmethod
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

    @abstractmethod
    def release(self, cfg: Config, session_id: str, token: str, /) -> None:
        """Give back the lock `token` took. Safe where it was never taken, and a no-op
        where a later turn holds it: a turn that outlived its claim must not end the
        claim of the turn that took it over."""
        ...

    @abstractmethod
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


class Instructions(ABC):
    """A deployment's standing text for every agent and subagent it runs, but a compiled
    subagent: deepagents runs that graph with the prompt its `build` gave it.

    Asked once per agent built, so a change reaches the next turn without a restart.
    Read after kingfisher's own prompt and before the agent's, the most general first.
    What kingfisher tells every agent is not replaceable.
    """

    @abstractmethod
    def text(self, cfg: Config) -> str:
        """The instructions, or an empty string for none."""
        ...


class TurnInstructions(ABC):
    """A deployment's text for one turn -- today's date, who the user is -- for every agent
    and subagent the turn runs but a compiled subagent, as `Instructions` reach.

    Asked once per turn. Sent after the part of the system prompt that is cached, so text
    that differs between sessions and turns costs none of them that cache, and stored
    nowhere: the next turn asks again.
    """

    @abstractmethod
    def text(self, cfg: Config, session_id: str, caller: Caller) -> str:
        """What this turn's graphs are told, or an empty string for nothing."""
        ...


class MemoryStore(ABC):
    """Where a user's memory is kept, for a deployment whose memory follows the user from
    one session to the next rather than ending with each.

    With one wired, the session's memory file is a working copy: a turn's start writes the
    user's entries into it before the agent reads it, and its end hands over what the turn
    added, changed and removed. Asked only where memory is on for the turn. Plain methods:
    on the async path each is called on kingfisher's thread pool, off the event loop. An
    exception from any is reported as `memory_store_failed` and the turn goes on.
    """

    @abstractmethod
    def key(self, caller: Caller) -> str | None:
        """Whose memory this turn's is, or `None` for no one's, which keeps the session's.

        kingfisher reads no part of it, and hands it back to `load` and `apply` as given.
        """
        ...

    @abstractmethod
    def load(self, key: str) -> tuple[MemoryEntry, ...]:
        """This user's entries, in order, each with the id kingfisher gave it."""
        ...

    @abstractmethod
    def apply(self, key: str, changes: MemoryChanges) -> tuple[MemoryEntry, ...]:
        """`changes` applied by id to this user's entries as they are now, and the result.

        Another session of the same user may have changed them since this one loaded, so
        an entry is merged by its id, never the list replaced. An id given afresh is `m`
        and a number, as kingfisher numbers one, or the next turn numbers it again.
        """
        ...


class UnusedResults(ABC):
    """What becomes of the saved tool results nothing points to any more.

    Asked at the end of every turn that leaves one, once the ledger and the conversation
    are written and while the session is still claimed. Every file handed over has
    already been judged safe to drop: no row names it, and its name is one kingfisher's
    recorder gives, so nothing the agent wrote beside them is among them.
    """

    @abstractmethod
    def settle(self, found: UnusedFiles) -> None:
        """Do what this deployment does with each file in `found`.

        A file not removed is handed over again by the next turn. An exception here is
        logged and the turn finishes as it would have.
        """
        ...


@dataclass(frozen=True)
class ReturnedPdf:
    """One PDF in a tool's result, as kingfisher judged it by its size.

    `verdict` is `"passes"`, to be handed over as it is; `"too_large"`, more than a turn
    holds on its own; or `"turn_full"`, small enough alone but not beside the PDFs the
    turn already holds.
    """

    verdict: Literal["passes", "too_large", "turn_full"]
    #: Its length in bytes, 0 for one the provider fetches by reference.
    size: int
    #: The PDF bytes the turn already holds, beside which it was judged.
    held: int
    #: The PDF bytes one turn of this graph may hold, which its model's summariser sets.
    allowance: int
    #: The tool whose result carries it, and the call that result answers.
    tool: str
    call_id: str
    #: The file `read_file` read it from; `None` from any other tool.
    path: str | None = None
    _base64: str | None = field(default=None, repr=False, compare=False)

    def read(self) -> bytes | None:
        """The PDF itself, or `None` for one the provider fetches by reference."""
        return None if self._base64 is None else base64.b64decode(self._base64)


class PdfAttachments(ABC):
    """What the model is handed in place of a PDF a tool returns, for a deployment that
    judges one by more than its size: its pages, say, or what it holds.

    Asked of every PDF a tool's result carries, on every graph kingfisher builds, once
    kingfisher has judged it -- whatever that verdict was. The calls of one graph are
    asked one at a time, since each PDF is judged beside what the one before was
    replaced with. A plain method: on the async path it is called on kingfisher's thread
    pool, off the event loop.
    """

    @abstractmethod
    def replacement(self, pdf: ReturnedPdf) -> str | Sequence[Any] | None:
        """What the model is handed instead of `pdf`, or `None` for kingfisher's verdict.

        Text or content blocks, as a tool may return. They are judged as any result is, so
        a PDF handed back in its place is still refused past what the turn holds. Anything
        handed over counts as the call succeeding; nothing -- `""`, no blocks, or blank
        text alone -- is logged and read as `None`, so to withhold a PDF, say so in text.
        An exception here is logged, and the verdict stands.
        """
        ...


class SubagentMiddlewares(ABC):
    """Which of the agent's middleware a portable subagent runs, since it may name none.

    Asked once for each portable subagent built, a helper too, with the main agent's names
    whatever summoned it: the agent's line is the ceiling everything beneath it is clamped
    by. Each name answered is built with the settings the agent's file wrote for it. A
    subagent the deployment wrote, or one that arrives compiled, is never asked about.
    """

    @abstractmethod
    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Sequence[str]:
        """The middleware `subagent` runs, from `middlewares`, the agent's own, in order.

        A name outside them is refused when the agent is built, not dropped: it would be
        middleware this request was never granted.
        """
        ...


class AgentsMiddlewares(SubagentMiddlewares):
    """`SubagentMiddlewares` answering the agent's own: what a subagent written to travel
    cannot name, it runs as the agent it works for does."""

    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Sequence[str]:  # noqa: ARG002 -- the agent's, whoever asks
        return middlewares


class ScratchRunLimit(ABC):
    """Which graphs' offered tools a deployment refuses `ToolContext.run`, among those
    kingfisher would hand it to.

    Asked once per graph built with offered tools: the agent, each subagent a definition
    declares, and `general-purpose`. Its answer only takes away. A graph refused the shell,
    or holding it behind a person, is refused a run whatever it says, and a tool called
    from `eval`'s code is never handed one. An exception here is logged and refuses that
    graph's tools their runs, naming it in each refusal; the other graphs are untouched.
    So does any answer but `True` or `False`: `allows` is called, never awaited, so an
    `async def` answers a coroutine, which is not a grant.
    """

    @abstractmethod
    def allows(self, name: str, kind: Literal["agent", "subagent", "general-purpose"]) -> bool:
        """Whether the tools of graph `name` may run commands, where kingfisher would let them.

        `name` is the agent's, empty for a request naming none; the subagent's; or
        `general-purpose` for deepagents' own.
        """
        ...


class NoScratchRunLimit(ScratchRunLimit):
    """Every graph kingfisher would hand a run is handed one."""

    def allows(
        self, name: str, kind: Literal["agent", "subagent", "general-purpose"]  # noqa: ARG002
    ) -> bool:
        return True


def every_interface() -> tuple[type, ...]:
    """The interfaces defined here, in the order they are written."""
    # By base rather than `inspect.isabstract`: that takes the imported `collections.abc`
    # names too, and would drop an interface whose every method came to have a default,
    # while the defaults kept here subclass an interface rather than `ABC`.
    return tuple(
        one
        for one in globals().values()
        if isinstance(one, type) and one.__module__ == __name__ and ABC in one.__bases__
    )


def refuse_unless_subclass(
    what: str, given: object, interface: type, *, error: type[Exception] = TypeError
) -> None:
    """Raise `error` unless `given`, wired as `what`, subclasses `interface`.

    Python refuses a subclass missing a method when it is made, and checks nothing of
    anything else.
    """
    if isinstance(given, interface):
        return
    msg = (
        f"{what} takes a {interface.__name__}, and {type(given).__name__} does not "
        f"subclass it -- subclass {interface.__name__}, which names every method it asks for"
    )
    raise error(msg)


def hooks_of(function: Callable[..., object]) -> dict[str, type]:
    """The parameters of `function` that are hooks, and the interface each takes: those
    left out by default and typed as one of the interfaces here.

    Read off the annotation, so a hook added to a signature is checked with nothing else
    to extend.
    """
    interfaces = {one.__name__: one for one in every_interface()}
    found: dict[str, type] = {}
    for name, parameter in inspect.signature(function).parameters.items():
        if parameter.default is not None:
            continue
        written = parameter.annotation
        # A string under `from __future__ import annotations`, as `service.py` writes it.
        names = (
            written.split(" | ")
            if isinstance(written, str)
            else [getattr(one, "__name__", "") for one in get_args(written) or (written,)]
        )
        if taken := [interfaces[one] for one in names if one in interfaces]:
            found[name] = taken[0]
    return found

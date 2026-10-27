"""What the core asks of the world, as interfaces a deployment may implement itself.

Middleware is langchain's `AgentMiddleware` as it is, a wire format is configuration,
and the conversation is kept in the session, so none of them is here.
"""

from __future__ import annotations

import base64
import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, Protocol, get_args, runtime_checkable

from kingfisher.core.session.threads import off_loop, thread_pool

if TYPE_CHECKING:
    from kingfisher.core.agent.tool_context import Caller
    from kingfisher.core.definitions.catalogue import Definitions
    from kingfisher.core.session.unused import UnusedFiles

    from .config import Config


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


class CommandRunner(Protocol):
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
    deepagents' own files in the scratchpad, keeps the shell out of `/.harness` and
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
    """A deployment's standing text for every agent and subagent it runs, but a compiled
    subagent: deepagents runs that graph with the prompt its `build` gave it.

    Asked once per agent built, so a change reaches the next turn without a restart.
    Read after kingfisher's own prompt and before the agent's, the most general first.
    What kingfisher tells every agent is not replaceable.
    """

    def text(self, cfg: Config) -> str:
        """The instructions, or an empty string for none."""
        ...


class TurnInstructions(Protocol):
    """A deployment's text for one turn -- today's date, who the user is -- for every agent
    and subagent the turn runs but a compiled subagent, as `Instructions` reach.

    Asked once per turn. Sent after the part of the system prompt that is cached, so text
    that differs between sessions and turns costs none of them that cache, and stored
    nowhere: the next turn asks again.
    """

    def text(self, cfg: Config, session_id: str, caller: Caller) -> str:
        """What this turn's graphs are told, or an empty string for nothing."""
        ...


class UnusedResults(Protocol):
    """What becomes of the saved tool results nothing points to any more.

    Asked at the end of every turn that leaves one, once the ledger and the conversation
    are written and while the session is still claimed. Every file handed over has
    already been judged safe to drop: no row names it, and its name is one kingfisher's
    recorder gives, so nothing the agent wrote beside them is among them.
    """

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


class PdfAttachments(Protocol):
    """What the model is handed in place of a PDF a tool returns, for a deployment that
    judges one by more than its size: its pages, say, or what it holds.

    Asked of every PDF a tool's result carries, on every graph kingfisher builds, once
    kingfisher has judged it -- whatever that verdict was. The calls of one graph are
    asked one at a time, since each PDF is judged beside what the one before was
    replaced with. A plain method: on the async path it is called on kingfisher's thread
    pool, off the event loop.
    """

    def replacement(self, pdf: ReturnedPdf) -> str | Sequence[Any] | None:
        """What the model is handed instead of `pdf`, or `None` for kingfisher's verdict.

        Text or content blocks, as a tool may return. They are judged as any result is, so
        a PDF handed back in its place is still refused past what the turn holds. Anything
        handed over counts as the call succeeding; nothing -- `""`, no blocks, or blank
        text alone -- is logged and read as `None`, so to withhold a PDF, say so in text.
        An exception here is logged, and the verdict stands.
        """
        ...


class SubagentMiddlewares(Protocol):
    """Which of the agent's middleware a portable subagent runs, since it may name none.

    Asked once for each portable subagent built, a helper too, with the main agent's names
    whatever summoned it: the agent's line is the ceiling everything beneath it is clamped
    by. Each name answered is built with the settings the agent's file wrote for it. A
    subagent the deployment wrote, or one that arrives compiled, is never asked about.
    """

    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Sequence[str]:
        """The middleware `subagent` runs, from `middlewares`, the agent's own, in order.

        A name outside them is refused when the agent is built, not dropped: it would be
        middleware this request was never granted.
        """
        ...


class AgentsMiddlewares:
    """`SubagentMiddlewares` answering the agent's own: what a subagent written to travel
    cannot name, it runs as the agent it works for does."""

    def names(self, subagent: str, agent: str, middlewares: tuple[str, ...]) -> Sequence[str]:  # noqa: ARG002 -- the agent's, whoever asks
        return middlewares


class ScratchRunLimit(Protocol):
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

    def allows(self, name: str, kind: Literal["agent", "subagent", "general-purpose"]) -> bool:
        """Whether the tools of graph `name` may run commands, where kingfisher would let them.

        `name` is the agent's, empty for a request naming none; the subagent's; or
        `general-purpose` for deepagents' own.
        """
        ...


class NoScratchRunLimit:
    """Every graph kingfisher would hand a run is handed one."""

    def allows(
        self, name: str, kind: Literal["agent", "subagent", "general-purpose"]  # noqa: ARG002
    ) -> bool:
        return True


def hooks_of(function: Callable[..., object]) -> dict[str, type]:
    """The parameters of `function` that are hooks, and the protocol each takes: those
    left out by default and typed as one of the protocols here.

    Read off the annotation, so a hook added to a signature is checked with nothing else
    to extend.
    """
    protocols = {
        name: one
        for name, one in globals().items()
        if isinstance(one, type) and Protocol in one.__mro__[1:]
    }
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
        if taken := [protocols[one] for one in names if one in protocols]:
            found[name] = taken[0]
    return found


def refuse_incomplete(keyword: str, hook: object, protocol: type) -> None:
    """Refuse `hook`, wired as `keyword=`, where it lacks a method `protocol` lists.

    A class of the deployment's own is taken whatever it subclasses, so long as it has
    every method.
    """
    # Read off the protocol, so a method added to one later is checked with no other edit.
    missing = sorted(
        name
        for name, value in vars(protocol).items()
        if callable(value)
        and not name.startswith("_")
        and not callable(getattr(hook, name, None))
    )
    if not missing:
        return
    named = ", ".join(missing)
    msg = (
        f"{keyword}= takes a {protocol.__name__}, and {type(hook).__name__} has no "
        f"{named} -- define {'it' if len(missing) == 1 else 'them'} as "
        f"{protocol.__name__} does"
    )
    raise TypeError(msg)

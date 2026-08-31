"""The application service: wired once, then asked to run things.

`stream()` used to build its own world on every call -- checkpointer, session
directories, workspace layout, permissions -- and take a keyword argument for
each thing a test might want to substitute. That list grows with every port,
and it made construction a per-request event for a program whose next shape is
a server that constructs once and serves many.

So the wiring lives here and the orchestration reads as a sequence:

    kingfisher = Kingfisher(from_env())
    for event in kingfisher.stream(request):
        ...

Module-level `run()` and `stream()` remain, over a default instance, so
`run("profile /data/x.csv")` still works and nothing calling it had to change.

What is *not* hoisted: the agent. It reads the workspace's skills and subagent
definitions at construction, so a cached one would serve a stale view of a
directory the user can edit between turns, and uploads write definitions into
it per request.

Measured, so the trade is a fact rather than a guess: 9.2ms median and 10.0ms
p95 for an unrestricted agent, of which 7.2ms is `create_deep_agent` compiling
the graph -- everything kingfisher does around it is sub-millisecond. Against a
turn of 1.5-1.9s that is 0.6%.

What it scales with, per item added at construction:

  subagent      +4.3ms   each compiles its own graph
  custom tool   +0.47ms  linear to at least 50
  middleware    +0.06ms
  skill          0.0ms   sixteen measure the same as none
  deny rule      0.0ms   a hundred measure the same as none

Skills and permissions are free because they reach the agent as prompt text and
as a rules list, not as anything compiled. Tools are an order of magnitude
cheaper than subagents and an order dearer than middleware, so "adding things
dynamically is cheap" is true or false depending entirely on which.

The costs are additive: 10 tools, 5 middleware, 20 deny rules and 2 subagents
predicts 20.8ms and measures 21.6ms, about 1% of a turn.

Construction is CPU-bound Python, so it does not parallelise: ~100 builds per
second per process, and worker threads make it slightly worse (0.85x) rather
than better. At 1.5s a turn that ceiling is around 150 concurrent turns, or
about 34 if every one activates eight subagents. Below that it is noise; above
it, a cache keyed on session *and* capabilities *and* a fingerprint of the
definitions would be the thing to reach for -- the fingerprint because uploads
change what a session offers between turns, which is the staleness this avoids
by not caching at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import AsyncExitStack, contextmanager, suppress
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic, time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.application import config as config_module
from kingfisher.config import Config
from kingfisher.domain import retention
from kingfisher.domain.agent import AgentSpec
from kingfisher.domain.capabilities import (
    UNRESTRICTED,
    Capabilities,
    CapabilityError,
    withheld,
)
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.domain.retention import SweepResult
from kingfisher.domain.session import (
    QuotaExceededError,
    Session,
    SessionInfo,
    UnknownSessionError,
    known,
    sessions_root,
    still_held,
)
from kingfisher.domain.transcript import Message
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.infrastructure.catalogue.documents import read_agent
from kingfisher.infrastructure.files import fetch_refs
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.agent import (
    MiddlewareFactory,
    available_skills,
    build_agent,
    defined_subagents,
    indistinct_delegates,
    registered_tools,
    release_interpreter,
    workspace_tool_names,
)
from kingfisher.infrastructure.harness.checkpointing import (
    async_session_checkpointer,
    build_session_checkpointer,
    release_checkpointer,
    thread_ids,
)
from kingfisher.infrastructure.harness.runlog import JsonlRunLogger, log_path
from kingfisher.infrastructure.seeding import SEED_HINT
from kingfisher.infrastructure.session_store import (
    TRANSCRIPT,
    LocalSessionStore,
    keep_from,
    read_transcript,
    restore_into,
    write_transcript,
)
from kingfisher.infrastructure.uploads import provision
from kingfisher.infrastructure.workspace_fs import (
    LocalSessionDirs,
    LocalSessionRoot,
    agent_snapshot,
    agent_started_with,
    check_placeable,
    collect_artifacts,
    ensure_layout,
    ensure_session_layout,
    place_data,
    place_inputs,
    protect_data,
    remember_agent,
    session_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.domain.ports import (
        CommandRunner,
        DefinitionStore,
        FileStore,
        SessionDirs,
        SessionRoot,
        SessionStore,
        ThreadStore,
    )


#: "Nothing was supplied", distinct from `None`, which is a deliberate choice to
#: run without a checkpointer at all.
_UNSET: Any = object()


@dataclass(frozen=True)
class _Admitted:
    """A request that has passed everything able to reject it.

    The boundary is the whole point of the type. `_admit` may raise -- an
    unknown session, a quota, a file that is not there, middleware this request
    may not use -- and it runs before any turn directory exists. `_open_turn`
    creates one and never refuses.

    That ordering was a claim in a docstring, and it was false: `--data` naming
    a missing file left nothing behind, while `--input` naming one left `t001`,
    because the inputs were copied after the turn was allocated. Making the
    halves separate functions with one type between them is what turns the
    claim into something a reader can check.
    """

    request: Request
    session: Any
    graph: Any
    #: Paths `protect_data` could not harden. Reported to the caller rather
    #: than raised, so they cross the boundary instead of stopping at it.
    unprotected: tuple[str, ...]
    placement: Any
    #: Content resolved from `input_refs`, held until the turn exists.
    #:
    #: Fetched during admission because a ref that will not resolve must refuse
    #: the request, and written in `_open_turn` because a turn's `input/` is not
    #: there yet. The bytes wait in between rather than the refusal moving.
    fetched_inputs: Any = None
    #: The saver this service opened for the turn, or None when it opened
    #: nothing -- an injected instance is the deployment's to close.
    release: Any = None
    #: `(what, names)` for each thing this workspace offers that the request did
    #: not grant -- tools, skills, subagents. Crosses rather than stopping: a
    #: withheld name is a fact about the run, not a refusal.
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = ()
    #: `(name, why)` for each delegate that asked to run elsewhere and did not.
    #: A fact about the run, like `withheld` -- nothing is wrong enough to stop
    #: for, and nothing else would ever say it.
    indistinct: tuple[tuple[str, str], ...] = ()
    #: Tool names more than one file defines, which the agent holding the
    #: grant therefore cannot hold. Reported rather than dropped in silence.
    delegate_only: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Prepared:
    """Everything a turn needs before the model is reached.

    Extracted so the orchestration sequence exists once. `stream` and `astream`
    differ only in how they iterate the graph -- a dozen lines each -- and the
    hundred lines of ordering that matter are not written twice to drift apart.
    """

    graph: Any
    message: str
    session: Any
    turn: Any
    logger: Any
    config: dict[str, Any]
    events: tuple[RunEvent, ...]
    deadline: float
    timeout_s: float
    #: Closed when the turn ends. See `_checkpointer_for`.
    release: Any = None
    #: What was said in this session before now. The graph's saver holds one
    #: turn and nothing after it, so this is where a conversation comes from.
    history: tuple[Message, ...] = ()


def _withheld_by_kind(
    allowed: Capabilities,
    cfg: Config,
    session_dir: Path,
    graph: Any,
    catalogue: Definitions,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What this request left out, per kind, skipping the kinds it left nothing.

    **`middleware` is deliberately not among them**, and the reason is about
    this report rather than about that axis. What goes here is what a caller
    could have asked for differently -- a tool, a skill, a delegate they may
    grant next time. A caller cannot register a middleware: the names come from
    whatever constructed `Kingfisher`, so telling them one was withheld names
    something they have no way to act on.

    It is the one axis where a shortfall can pass unremarked --
    `approved_middleware` raises for a name a request withheld, but a
    definition that wrote `middleware: ["*"]` resolves quietly smaller, and
    quietly to nothing. That is the trade this absence accepts, written here
    because this is where the next reader will come looking for it.

    Three axes, one rule. Each differs only in where "what the workspace offers"
    comes from, and none of the three is knowable without asking the thing that
    assembled the agent -- which is why a grant goes stale in the first place.

    The catalogue is passed rather than re-derived, so what a caller is told it
    did not grant is measured against the same directories the agent was built
    from.

    Each "what the workspace offers" is a thunk, not a value, and that is a cost
    rather than a style: three of the four walk a directory, and an axis left at
    its default is skipped a line later without ever needing the answer. Written
    eagerly, the subagent walk happened on every turn of every run -- which
    stayed invisible only while that axis defaulted to none and this function
    was the sole reader.
    """
    default = Capabilities()
    workspace = tuple(workspace_tool_names(cfg, catalogue=catalogue))
    offered = (
        # Built-ins and workspace tools are granted apart, so they are reported
        # apart: "3 tool(s) not granted" meant nothing when it could have been
        # either kind.
        # `or ()` for the unreadable case, which cannot happen to a graph
        # `build_agent` made -- and if it ever does, a run report listing no
        # built-ins is a better outcome than a turn that will not start.
        # `test_a_real_build_is_readable` is what notices instead.
        ("builtin tool", "builtin_tools", lambda: tuple(
            n for n in registered_tools(graph) or () if n not in set(workspace)
        )),
        ("tool", "tools", lambda: workspace),
        ("skill", "skills", lambda: available_skills(cfg, session_dir, catalogue=catalogue)),
        (
            "subagent",
            "subagents",
            lambda: tuple(defined_subagents(cfg, session_dir, catalogue=catalogue)),
        ),
    )
    found = []
    for what, field, names_of in offered:
        granted = getattr(allowed, field)
        # Silent when the request left an axis alone. `subagents` defaults to
        # none, so reporting every axis at its default would put a line about
        # undeclared delegates on every run -- which is the noise this event
        # exists to avoid being.
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=names_of()):
            found.append((what, left_out))
    return tuple(found)


def _delegate_only(allowed: Capabilities, cfg: Config, *, catalogue: Any) -> tuple[str, ...]:
    """Names this run was granted that only a delegate can actually ask for.

    Computed from the catalogue rather than threaded out of `build_agent`,
    because the graph has already dropped them by the time it exists -- which is
    exactly why it has to be said from somewhere that still knows.
    """
    from kingfisher.domain.tool import Offering  # noqa: PLC0415
    from kingfisher.infrastructure.catalogue import Definitions  # noqa: PLC0415

    found = (catalogue or Definitions.from_config(cfg)).tools.found
    return Offering.of(found).ambiguous(allowed.tools, found)


def opening_events(  # noqa: PLR0913, PLR0917 -- one parameter per warning
    # kind, and folding them into a bag would only move the list somewhere
    # a reader has to go and find it.
    turn_dir: str,
    unprotected: tuple[str, ...],
    placement: Any,
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = (),
    indistinct: tuple[tuple[str, str], ...] = (),
    delegate_only: tuple[str, ...] = (),
) -> tuple[RunEvent, ...]:
    """What the caller is told before the model is reached.

    A function because it is one: nothing here touches the service, and every
    input is already decided by the time it runs. `_prepare` was 123 lines and
    this was the part of it that could be checked on its own.
    """
    events: list[RunEvent] = []
    if unprotected:
        events.append(RunEvent(kind="protect_failed", text="; ".join(unprotected)))
    # A grant is a whitelist, so it means less than the workspace holds and says
    # so nowhere. Told here rather than discovered when the model reaches for
    # one and is refused halfway through a turn. One line per kind, because a
    # single line naming three kinds is the one nobody finishes reading.
    for what, names in withheld:
        events.append(
            RunEvent(
                kind="withheld",
                text=f"{len(names)} {what}(s) not granted: {', '.join(names)}",
            )
        )
    # Granted, and still not in the agent's own hands. Two files may each define
    # a `fetch`, and an agent dispatches by name -- so the pair goes to whichever
    # delegate names one, and the agent holding the grant gets neither.
    #
    # Said out loud because the alternative is the failure this codebase refuses
    # everywhere: quietly holding less than was asked for. It is deliberately
    # *not* folded into `withheld`, which means "you did not ask for this" --
    # here the caller did ask, and the answer is "name which one, in a delegate".
    if delegate_only:
        events.append(
            RunEvent(
                kind="delegate_only",
                text=(
                    f"{len(delegate_only)} tool name(s) more than one file defines, "
                    f"so this agent holds none of them -- a subagent that names one "
                    f"gets it: {', '.join(delegate_only)}"
                ),
            )
        )
    # A delegate that meant to run elsewhere and did not. Said here because
    # nothing later will: it builds, it answers, and an answer from the model
    # it was supposed to be checking looks exactly like a good one.
    for name, why in indistinct:
        events.append(RunEvent(kind="indistinct", text=f"{name} {why}", agent=name))
    if placement.placed:
        # Replacement is the one dangerous case -- durable data, silently
        # overwritten -- so it is named rather than assumed.
        replaced = f" ({len(placement.replaced)} replaced)" if placement.replaced else ""
        events.append(
            RunEvent(kind="data_placed", text=f"{', '.join(placement.placed)}{replaced}")
        )
    events.append(RunEvent(kind="run_start", text=turn_dir))
    return tuple(events)


def turn_message(task: str, turn: Any, placed: tuple[str, ...], has_inputs: bool) -> str:
    """The task, plus this turn's facts and nothing more.

    What the task should *produce* is the task's business: asking for a written
    report is one kind of request among many, and a general agent should not
    carry one convention's filenames in its plumbing. They lived in the system
    prompt once, which made every greeting deliberate over two files nobody
    wanted.

    These facts reach the model here rather than in the system prompt because
    they are run-scoped: the prompt is the cached prefix, and putting a turn
    directory in it would move that prefix on every session.
    """
    # Named because `/data` changed under a session the agent may already have
    # looked at.
    arrived = f" New files in /data: {', '.join(placed)}." if placed else ""
    supplied = (
        f" Files supplied with this request are in {turn.virtual_input_dir}."
        if has_inputs
        else ""
    )
    # Both names for the one directory. `system.md` states the rule -- drop the
    # leading slash for the shell -- and stating it there was not enough: over
    # ten runs of one task the agent passed the virtual path to `execute` 4
    # times, each failing and costing roughly three times the whole task to
    # recover. The 6 that used the shell form first never failed. This line is
    # already per-turn, so unlike the system prompt it costs no cache to say.
    return (
        f"{task}\n\n"
        f"Your run directory for this task is {turn.virtual_dir} "
        f"(from the shell, {turn.shell_dir}).{supplied}{arrived}"
    )


def _consume(
    namespace: Any,
    mode: str,
    chunk: Any,
    answer: str,
    delegates: runtime.Delegates,
) -> tuple[str, tuple[RunEvent, ...]]:
    """One stream chunk into (answer so far, events to emit).

    Both loops are offered every chunk and each mode ignores the ones that are
    not its own. Written once so the sync and async loops cannot come to
    disagree about which mode carries the answer -- or, now, about which agent
    a chunk came from, which is a second thing they could have drifted on.
    """
    if (text := runtime.answer_in(namespace, mode, chunk)) is not None:
        answer = text
    return answer, tuple(runtime.events_in(namespace, mode, chunk, delegates))


def _overrun(prepared: _Prepared) -> RunEvent | None:
    """The cut-short event once a turn is out of time, else nothing.

    Checked between chunks, which is the only place there is to stop. What the
    turn produced is already on disk and in the manifest, so ending here keeps
    the work and loses only the steps that had not happened yet.
    """
    if monotonic() <= prepared.deadline:
        return None
    return RunEvent(kind="cut_short", text=f"turn stopped after {prepared.timeout_s}s")


def _out_of_steps(cfg: Config) -> RunEvent:
    """The same event for the other bound on a turn.

    Two bounds, and until this they behaved nothing alike. `turn_timeout_s` is
    checked between chunks and ends the turn as a `RunResult` with `cut_short`
    set; `recursion_limit` is enforced inside langgraph's own loop and came out
    as a `GraphRecursionError` through every caller -- the driver printed a
    stack trace, and the HTTP surface had no mapping for it at all.

    Observed on a run that had already written its report and validated the
    markup: the file was on disk, and what the caller got was a traceback with
    no path in it. Nothing about running out of steps is less ordinary than
    running out of seconds, so it is reported the same way and the work
    survives the same way.

    Names the setting because the two bounds are raised in different places and
    "turn stopped" alone sends the reader to the wrong one.
    """
    return RunEvent(
        kind="cut_short",
        text=(
            f"turn stopped after {cfg.recursion_limit} steps "
            f"(raise KINGFISHER_RECURSION_LIMIT)"
        ),
    )


class Kingfisher:
    """A configured kingfisher. Construct once; call `run` or `stream` per request.

    Construction is where the deployment-scoped work happens -- creating the
    layout, dropping write bits on `/data`, opening the thread store. Doing it
    here rather than per request also means a broken workspace or an
    unreachable state directory fails at startup, not on the first turn.

    Every collaborator is injectable, and by protocol rather than by patching:
    a test hands in its own `SessionDirs` to watch turn allocation, or its own
    graph to drive a scripted conversation.

    `catalogue` follows that shape too, and takes either form. A deployment
    pointing at three directories passes the mapping and names no classes; one
    holding its definitions somewhere kingfisher did not choose passes a
    `Definitions` of its own repositories. Both settle to the same object here, so
    nothing downstream knows which arrived -- and swapping a single kind is
    `replace(catalogue, subagents=...)`, since it is frozen.
    """

    def __init__(  # noqa: PLR0913 -- the composition root; each argument is one
        # collaborator a deployment or a test substitutes, and folding them into
        # a parameter object would hide exactly what is substitutable.
        self,
        cfg: Config | None = None,
        *,
        dirs: SessionDirs | None = None,
        # A store, or a factory given a session directory, or nothing for the
        # default -- see `_checkpointer_for`. The union is the contract, so it
        # is written here rather than left for a reader to infer from a branch.
        threads: ThreadStore | Callable[[Path], Any] | None = None,
        definitions: DefinitionStore | None = None,
        files: FileStore | None = None,
        # Where a session's files go when the machine may not keep them. `None`
        # means the session directory is the only copy, which is what every
        # deployment has had until now and stays correct wherever the host is
        # allowed to hold data.
        sessions: SessionStore | None = None,
        session_root: SessionRoot | None = None,
        runner: Callable[[Path], CommandRunner] | None = None,
        catalogue: Definitions | Mapping[str, Path] | None = None,
        grants: Capabilities | None = None,
        middleware: Mapping[str, MiddlewareFactory] | None = None,
        graph: Any | None = None,
    ) -> None:
        self.cfg = cfg or config_module.from_env()
        config_module.enforce_local_only_tracing()

        # Only what sessions share. Each session's own layout is made per
        # request, because its path is not known until the request names it.
        self.workspace: Path = ensure_layout(self.cfg.workspace)

        # Where the reviewed definitions are read from, settled once. Omitted,
        # it is the three directories `cfg` names, which is what it has always
        # been; supplied, a deployment has staged them somewhere itself and this
        # is the only place that has to know. Resolved here rather than per
        # request so a deployment that fetches them pays once, and so a
        # catalogue that cannot be read fails at startup rather than serving an
        # agent that has quietly been told about nothing.
        # Read now rather than on the first turn: a definition that will not
        # parse is a wiring mistake, and this is the last moment it is cheap
        # to say so. `--list` deliberately does not do this -- see `warm`.
        self.catalogue: Definitions = resolve_definitions(self.cfg, catalogue).warm()

        # Injected, or derived from configuration, or nothing -- the same
        # order `catalogue` follows and for the same reason: derive from `cfg`,
        # never invent. A deployment keeping sessions somewhere that is not a
        # directory passes the object; one keeping them in a directory names it
        # and this builds the adapter.
        self.sessions_store: SessionStore | None = sessions or (
            LocalSessionStore(self.cfg.session_store) if self.cfg.session_store else None
        )
        self.dirs: Any = dirs if dirs is not None else LocalSessionDirs()
        # Where a session's files are for the length of a turn. The default
        # keeps them under the workspace and leaves them there, which is what
        # this did before there was a port for it; a deployment whose tree
        # exists only while a turn runs supplies its own and gets the release
        # for free, because the turn is what closes it.
        #
        # This governs the *turn*, and only the turn. `sessions()`, `reap` and
        # `session_bytes` still read `sessions_root(workspace)`, so a provider
        # that puts its sessions elsewhere gets an inventory that reports nothing
        # and a janitor with nothing to sweep. That is survivable for a tree
        # whose whole point is not to outlive the turn -- there is nothing to
        # inventory -- and wrong for one that does. Whichever it is, the store
        # is what a caller should be asking, and that is not what those three
        # ask today.
        self.session_root: SessionRoot = session_root or LocalSessionRoot(self.workspace)
        # A callable, and only a callable. A runner is built for one turn --
        # kingfisher's own Landlock fence is, because its policy is generated
        # from the session -- and a shared instance could not know which session
        # it was running for, would be one fence for every tenant where the
        # runner *is* the isolation, and would be called from several threads at
        # once because turns overlap. A deployment with one to share writes
        # `lambda session_dir: shared`: a line at the call site rather than a
        # second shape here forever. `threads` takes both and needed a second
        # attribute to remember which it was given.
        if runner is not None and not callable(runner):
            msg = (
                "runner is built per turn, so it takes a callable: pass "
                "`lambda session_dir: your_runner` if you have one to share"
            )
            raise TypeError(msg)
        self._runner = runner
        # Host-side, beside the run logs, because the session directory is the
        # agent's own root -- a claim kept there would be something `execute`
        # could delete. `state_dir` is the one place the agent never addresses.
        self._claims: Path = self.cfg.state_dir / "claims"
        self.dirs.ensure(self._claims)
        # Three shapes, and the difference is who owns the connection. An
        # instance is a shared store the deployment made and manages; a callable
        # is a factory this service calls per session and closes after the turn;
        # `None` means the default, which is a database inside each session.
        #
        # `_shared` is the instance case only. `Session.discard` and `reap` use
        # it to forget a thread, and both correctly do nothing when it is absent:
        # a per-session database is deleted by removing the directory it sits in,
        # which is the whole reason orphaned threads stop being possible.
        self.threads: Any = threads
        self._shared: Any = threads if (threads is not None and not callable(threads)) else None
        # No default. A deployment that never serves uploaded definitions has
        # nothing to wire, and a request that supplies ids without one is a
        # configuration error worth saying out loud rather than a silent no-op.
        self.definitions: Any = definitions
        # Beside `definitions` and for the same reason: a caller with no host
        # paths names files by id, and only something the deployment wired can
        # turn a name into content.
        self.files: Any = files
        # What this deployment permits, before any request asks for anything.
        # Unrestricted by default, so a single-caller deployment is unaffected;
        # a service in front of many callers sets it, and `intersect` can only
        # subtract, so no request can widen past it.
        self.grants: Capabilities = grants if grants is not None else UNRESTRICTED
        # What a definition may name in its `middleware:` field. Empty by
        # default, so any such line fails loudly until a deployment wires one --
        # kingfisher cannot define these, only a deployment knows what its
        # middleware is. Registering is not the same as permitting: `grants`
        # still clamps which registered names a request may reach.
        self.middleware: Mapping[str, MiddlewareFactory] = middleware or {}
        self._graph = graph

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

    def session(self, session_id: str) -> SessionInfo | None:
        """One session, or `None` when there is no such session.

        `None` rather than raising, because "is this still there" is an ordinary
        question with two ordinary answers. `UnknownSessionError` is for a
        request that named one and meant to use it.

        Filtered from the same listing rather than stat-ing one path, so both
        answers come from one rule. At fifty sessions that is 0.22ms; it grows
        with the workspace, and a deployment large enough to mind wants an
        index rather than a cheaper stat.
        """
        return next((s for s in self.sessions() if s.id == session_id), None)

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

    def graph_for(
        self,
        request: Request,
        session_dir: Path,
        capabilities: Capabilities | None = None,
        checkpointer: Any = _UNSET,
    ) -> Any:
        """The graph that serves one request, rooted at its session.

        Built per request because capabilities narrow it, because it reads
        workspace content that can change between turns, and now because its
        backend is anchored to the session -- two sessions cannot share a
        graph without sharing a filesystem root. An injected graph is returned
        as-is -- and refused if the request narrows anything, since those
        restrictions were never applied to it.
        """
        if self._graph is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built graph"
                raise ValueError(msg)
            return self._graph

        return build_agent(
            self.cfg,
            agent=self._agent_for(request, session_dir.name),
            # Called here rather than passed down. This is where a turn first
            # has a session directory, and `build_agent` is where one is already
            # known -- so the harness keeps taking a runner, and only the
            # service, which does not know the session until now, takes a way to
            # make one.
            runner=self._runner(session_dir) if self._runner is not None else None,
            capabilities=capabilities if capabilities is not None else request.capabilities,
            session_dir=session_dir,
            run_on=request.run_on,
            middleware_registry=self.middleware,
            checkpointer=self.threads if checkpointer is _UNSET else checkpointer,
            catalogue=self.catalogue,
        )

    def remember_agent(self, session_id: str, name: str | None) -> None:
        """Have this session keep the agent it opened with.

        Nothing to keep for a session that named none, which is the migration
        path, and nothing to keep when the repository cannot hand over the
        document it parsed -- a deployment serving definitions from elsewhere
        keeps the behaviour it had, which is to read the catalogue each turn.
        Both are silent because both are ordinary.
        """
        if name is None:
            return
        documents = getattr(self.catalogue.agents, "documents", {})
        if (text := documents.get(name)) is not None:
            remember_agent(self.cfg.state_dir, session_id, text)

    def _agent_for(self, request: Request, session_id: str) -> AgentSpec | None:
        """The agent this turn runs, which is the one its session opened with.

        A session is fixed to an agent for its whole life. Swapping mid-session
        would change the system prompt under a history that already happened, so
        the conversation would no longer match the instructions that produced it.

        A later turn may name the same agent again -- a stateless caller sends
        the same payload every time and should not have to track what it opened
        with. Naming a *different* one is refused rather than ignored: honouring
        it is wrong, and ignoring it silently answers a question the caller
        thought they had asked.
        """
        kept = agent_started_with(self.cfg.state_dir, session_id)
        if kept is None:
            spec = self.agent_named(request.agent)
            self.remember_agent(session_id, request.agent)
            return spec

        started = read_agent(kept, agent_snapshot(self.cfg.state_dir, session_id))
        if request.agent is not None and request.agent != started.name:
            msg = (
                f"this session is running {started.name!r}; it was fixed when the "
                f"session opened and cannot be changed to {request.agent!r} "
                f"mid-conversation -- start a session to run a different agent"
            )
            raise CapabilityError(msg)
        return started

    def agent_named(self, name: str | None) -> AgentSpec | None:
        """The agent this request asked for, out of the catalogue.

        Naming one is required, and `None` is refused rather than defaulted.
        There is no honest default: the agent decides where every prompt in the
        session goes and what it costs, and a default would put the most
        consequential choice a caller makes somewhere the call site never
        mentions. It also leaves one path through `build_agent` rather than two.

        A name, never a definition: an agent decides which endpoint receives the
        session's prompts and whose credentials pay, so a caller picks from what
        the deployment reviewed and supplies nothing.

        The return stays optional because `build_agent` still takes an optional
        spec -- a test building a bare graph passes none, and that is a different
        question from what a *request* may leave out.
        """
        offered = self.catalogue.agents.specs
        listing = ", ".join(sorted(offered)) if offered else "none"
        if name is None:
            msg = (
                f"this request names no agent; this workspace offers {listing}"
                + ("" if offered else f" -- try {SEED_HINT}")
            )
            raise CapabilityError(msg)
        spec = offered.get(name)
        if spec is None:
            msg = (
                f"no agent named {name!r}; this workspace offers {listing}"
                + ("" if offered else f" -- try {SEED_HINT}")
            )
            raise CapabilityError(msg)
        return spec

    def _prepare(
        self,
        request: str | Request,
        session: Session | None = None,
        checkpointer: Any = _UNSET,
    ) -> _Prepared:
        """Do everything up to the model call, and return what the loop needs.

        Blocking, and deliberately so: filesystem work plus building
        the agent, measured at 15-46ms end to end -- of which 9.2ms is the
        agent. `astream` runs it on a worker thread rather than pretending
        otherwise.

        Two halves, and the seam is the rule: everything able to reject the
        request runs first, and only then is a turn directory created. That was
        a sentence in this docstring for a long time and was not true --
        `--input` named a missing file, was refused, and left `t001` behind.
        Written as two functions it is checkable, and `_Admitted` is the only
        way across.
        """
        return self._open_turn(self._admit(request, session, checkpointer))

    def _checkpointer_for(self, session_dir: Path) -> tuple[Any, Any]:
        """The saver this turn runs on, and how to release it when the turn ends.

        Only what this service opened is closed. An injected instance belongs to
        the deployment that made it and outlives every turn; a factory's result
        and the per-session default are ours, and a process serving many sessions
        would otherwise hold a file descriptor for each one it had ever touched.

        `None` for both when the deployment turned conversation off: a graph
        takes `checkpointer=None` and runs, and each turn simply starts cold.
        The flag wins over an injected store, because a deployment that said it
        wants no conversation means it whatever it wired earlier.
        """
        if not self.cfg.conversation_enabled:
            return None, None
        if self.threads is None:
            saver = build_session_checkpointer(session_dir)
            return saver, saver
        if callable(self.threads):
            saver = self.threads(session_dir)
            return saver, saver
        return self.threads, None

    async def _async_checkpointer_for(self, stack: AsyncExitStack, session_dir: Path) -> Any:
        """The saver an async turn runs on, entered into the turn's exit stack.

        Separate from `_checkpointer_for` because an aiosqlite connection
        belongs to the event loop that made it: it cannot be opened inside the
        worker thread `_prepare` runs on, which is why `astream` resolves the
        session first and hands the saver down.

        This is what carries the per-session shape to the deployments that most
        want it. `astream` refuses a sync saver outright -- `SqliteSaver`
        raises `NotImplementedError` on `aget_tuple` -- so an async deployment
        has always injected its own, and injecting an *instance* means one
        database shared by every session, which is the contention this avoids.
        A factory returning an async context manager gets one per session.

        `None` when conversation is off, for the same reason as the sync twin.
        """
        if not self.cfg.conversation_enabled:
            return None
        if self.threads is None:
            return await stack.enter_async_context(async_session_checkpointer(session_dir))
        if callable(self.threads):
            made = self.threads(session_dir)
            if hasattr(made, "__aenter__"):
                return await stack.enter_async_context(made)
            return made
        return self.threads

    def open_session_for(self, request: Request) -> Session:
        """Name this request's session and make sure its directory exists.

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

    def _admit(
        self,
        request: str | Request,
        session: Session | None = None,
        checkpointer: Any = _UNSET,
    ) -> _Admitted:
        """Everything that can refuse, before anything a refusal would strand.

        Nothing is destroyed here either, and nothing turn-shaped is created.
        The session directory is, which the rule tolerates: an empty one left
        by a rejected request is idempotent, and the retry reuses it.
        """
        request = Request.coerce(request)
        cfg, dirs = self.cfg, self.dirs
        session = session if session is not None else self.open_session_for(request)
        # A turn writes inside the session, never to the session itself, so the
        # timestamp `retention.expired` reads would still say "idle" for a
        # conversation in daily use. Recorded here, at the top of a turn, rather
        # than at the end: a turn that fails still happened.
        dirs.mark_used(session.directory)
        # Before the other refusals rather than after: those read the session,
        # and a turn arriving halfway through would be reading it as it moved.
        session.claim(dirs, self._claims, stale_after=cfg.claim_stale_after, now=time())
        try:
            return self._admitted(request, session, cfg, checkpointer)
        except BaseException:
            session.release(dirs, self._claims)
            raise

    def _admitted(
        self, request: Request, session: Session, cfg: Config, checkpointer: Any = _UNSET
    ) -> _Admitted:
        """The rest of admission, once the session is claimed.

        Split so the claim has exactly one release path for a refusal. Every
        check below can raise, and each one leaving the slot held would wedge
        the session until the claim aged out.
        """
        # Kernel-level guard; the deny rule covers only the file tools. Paths
        # it could not harden are reported below rather than raised: they used
        # to abort the run, and since this runs before anything else, one file
        # owned by another user made a session unusable for good.
        unprotected = protect_data(session.directory)

        # Before the data is placed, not after: placing it grows the session,
        # so checking afterwards would let a request that is already over
        # budget add to it and only then be refused.
        self._refuse_if_over_budget(session)

        # Both halves of "a request naming something that is not there must
        # fail before it leaves anything behind": the paths are checked by
        # `place_data`, the ids by the store, and neither has written yet.
        fetched = fetch_refs(request, self.files)

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        placement = place_data(request.data, session.directory, contents=fetched.data)

        # Before the agent, which discovers definitions by reading the
        # directories this writes.
        brought = provision(
            request, self.definitions, session.directory, cfg, catalogue=self.catalogue
        )

        # What this deployment permits, narrowed by what the request asked for.
        # Definitions the request brought itself are added back: their content
        # came from the caller, so a grant list -- written before their names
        # existed -- has no opinion about them.
        allowed = self.grants.intersect(request.capabilities).including(
            skills=brought.skills, subagents=brought.subagents
        )
        # Resolved here rather than in `__init__`, because the default is a
        # database inside this session and there is no session until now. The
        # async path opens its own on the event loop and hands it down, which is
        # what `checkpointer` carries.
        release: Any = None
        if checkpointer is _UNSET:
            checkpointer, release = self._checkpointer_for(session.directory)
        graph = self.graph_for(
            request, session.directory, capabilities=allowed, checkpointer=checkpointer
        )

        # The last thing that can refuse, and the reason this half exists. The
        # files themselves cannot be copied until a turn directory holds them,
        # but refusing them must not wait that long.
        check_placeable(request.inputs)

        return _Admitted(
            request=request,
            session=session,
            graph=graph,
            unprotected=unprotected,
            placement=placement,
            fetched_inputs=fetched.inputs,
            release=release,
            # Tools come off the assembled graph rather than a list kept
            # somewhere: the surface includes whatever the workspace defined, so
            # the only honest answer to "what was offered" is what was wired.
            # Skills and subagents are not on the graph, so they are asked of
            # the same functions `build_agent` asked -- 0.04ms and 1.4ms against
            # an admit already measured at 15-46ms.
            withheld=_withheld_by_kind(allowed, cfg, session.directory, graph, self.catalogue),
            delegate_only=_delegate_only(allowed, cfg, catalogue=self.catalogue),
            indistinct=indistinct_delegates(
                cfg,
                allowed,
                session.directory,
                catalogue=self.catalogue,
                run_on=request.run_on,
            ),
        )

    def _open_turn(self, admitted: _Admitted) -> _Prepared:
        """Create the turn and compose what the loop needs.

        Past the point of no refusal. Anything here that raised would leave a
        turn directory behind, which is what `_admit` exists to prevent -- so
        this half only ever creates, copies and composes.
        """
        cfg, dirs = self.cfg, self.dirs
        request, session = admitted.request, admitted.session
        session_id = session.id

        # The aggregate owns turn allocation: atomic, and a caller-supplied id wins.
        turn = session.allocate_turn(dirs, request.turn_id)

        place_inputs(request.inputs, turn.input_dir, contents=admitted.fetched_inputs)

        logger = JsonlRunLogger(
            log_path(cfg.state_dir, session_id),
            model=cfg.models.default,
            endpoint=cfg.models.resolve()[0].endpoint,
            session_id=session_id,
        )
        logger.run_start(request.task, turn.virtual_dir)

        return _Prepared(
            graph=admitted.graph,
            release=admitted.release,
            history=read_transcript(session.directory),
            message=turn_message(
                request.task,
                turn,
                admitted.placement.placed,
                # Fetched inputs are inputs. The agent is told the directory
                # exists on the same terms either way -- where a file came from
                # is the deployment's business, not the agent's.
                has_inputs=bool(request.inputs or admitted.fetched_inputs),
            ),
            session=session,
            turn=turn,
            logger=logger,
            config={
                "configurable": {"thread_id": session_id},
                "callbacks": [logger],
                "recursion_limit": cfg.recursion_limit,
            },
            events=opening_events(
                turn.virtual_dir,
                admitted.unprotected,
                admitted.placement,
                admitted.withheld,
                admitted.indistinct,
                admitted.delegate_only,
            ),
            deadline=monotonic() + cfg.turn_timeout_s,
            timeout_s=cfg.turn_timeout_s,
        )

    def _keep(self, prepared: _Prepared) -> tuple[str, ...]:
        """Persist what this turn produced, and name it.

        In the turn's `finally` rather than beside the terminal event, and that
        is the whole point of it being a separate method. `stream` is a
        generator whose last act is `yield self._finished(...)`, so a caller
        that stops reading early never advances the body that far -- the turn's
        files were never written to the store at all, and a session that moved
        to another machine came back without them. Nothing said so, because from
        the caller's side it had the answer it wanted.

        Moving the save a few lines earlier would not have helped: a generator
        only runs when someone pulls, so "after the graph loop, before the final
        yield" is the same `next()` call. Ending the turn is the only place that
        runs whether the caller listened or not.

        At the end of the turn rather than after each tool call. The narrower
        window is better and costs a directory walk per call, which is
        unmeasured -- and what has to be proven first is that a session survives
        the machine, for which a turn-end save is enough. Measure, then narrow.
        """
        self._record(prepared)
        kept = collect_artifacts(prepared.session.directory)
        if self.sessions_store is not None:
            # The transcript is named separately rather than collected. It sits
            # at the session root, and `collect_artifacts` walks `/derived` and
            # `/memory` -- so a first draft wrote it and never kept it, and a
            # session that outlived its machine came back with its files and no
            # conversation.
            #
            # And it stays out of `kept`, which is what the caller is handed:
            # `artifacts` is what a turn *produced*, and a transcript is
            # plumbing for the same reason `.home` is.
            keep_from(
                self.sessions_store,
                prepared.session.id,
                prepared.session.directory,
                (*kept, TRANSCRIPT),
            )
        return kept

    def _finished(
        self, prepared: _Prepared, answer: str, kept: tuple[str, ...], *, cut_short: bool
    ) -> RunEvent:
        """The terminal event, built the same way whichever loop produced it.

        Takes what `_keep` saved rather than saving anything itself, so that a
        caller who never reads this event has still had their work kept.
        """
        return RunEvent(
            kind="finished",
            text=answer,
            result=RunResult(
                session_id=prepared.session.id,
                turn_id=prepared.turn.id,
                answer=answer,
                virtual_dir=prepared.turn.virtual_dir,
                run_dir=prepared.turn.directory,
                log_path=log_path(self.cfg.state_dir, prepared.session.id),
                # Collected after the graph has finished, so it reflects what
                # the turn actually left behind -- including what the shell
                # wrote, which no file tool would have reported.
                artifacts=kept,
                cut_short=cut_short,
            ),
        )

    def _record(self, prepared: _Prepared) -> None:
        """Write what was said this turn, as records this package owns.

        Read back out of the graph rather than accumulated from the stream: the
        stream carries chunks and tool events shaped for a reader, and the state
        is the one place holding the conversation as messages. `get_state` works
        because the turn's saver is still alive here -- it holds this turn and
        nothing after it.

        A turn that produced no state leaves the transcript alone rather than
        truncating it. A refused turn, or one that died before the first
        superstep, has nothing to add and must not take the previous
        conversation with it.

        Nothing is suppressed. A first draft wrapped this in `suppress`, which
        hid the fact that it was writing nothing at all -- and a conversation
        lost without an error is precisely the failure this design exists to
        prevent. A graph with no `get_state` is the one case that is not an
        error: a deployment with conversation turned off has no state to read,
        and neither does a caller who injected something simpler than a graph.
        """
        if not self.cfg.conversation_enabled:
            return
        read = getattr(prepared.graph, "get_state", None)
        if read is None:
            return
        try:
            snapshot = read(prepared.config)
        except ValueError:
            # `No checkpointer set` -- an injected graph that keeps no state
            # between supersteps. Structural, like the missing method above, and
            # not a conversation that failed to be read. Caught by name rather
            # than by suppressing everything, so a graph that genuinely cannot
            # answer still says so.
            return
        if snapshot is None:
            # What the paragraph above describes, now that persistence runs at
            # the end of *every* turn rather than only a completed one: a graph
            # that died before its first superstep has no state to hand back.
            # Reading `.values` off it raised, which turned "nothing to add"
            # into a second failure on top of the first.
            return
        messages = snapshot.values.get("messages")
        if messages:
            write_transcript(prepared.session.directory, runtime.as_transcript(messages))

    def stream(self, request: str | Request) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        The terminal event has `kind == "finished"` and carries the `RunResult`.
        """
        # Coerced here rather than only in `_prepare`, because holding the
        # session now happens first and a bare task string has no session id to
        # read.
        request = Request.coerce(request)
        with self._held_session(request) as session:
            yield from self._stream_turn(request, session)

    def _stream_turn(self, request: Request, session: Session) -> Iterator[RunEvent]:
        """One turn, with its directory already held.

        Split from `stream` for the reason `_astream_turn` is split from
        `astream`: so what holds the session wraps the whole turn without
        indenting the loop that matters.
        """
        prepared = self._prepare(request, session)
        answer = ""
        ok = False
        cut_short = False
        kept: tuple[str, ...] = ()
        delegates = runtime.Delegates()
        try:
            # Inside the `try`, not before it. A caller that stops reading
            # during these -- `run_start` is the first -- used to leave the turn
            # with no end at all: the claim stayed taken, the checkpointer
            # stayed open, and nothing was persisted.
            yield from prepared.events
            for namespace, mode, chunk in prepared.graph.stream(
                runtime.user_payload(prepared.message, prepared.history),
                config=prepared.config,
                stream_mode=runtime.STREAM_MODES,
                subgraphs=True,
            ):
                answer, events = _consume(namespace, mode, chunk, answer, delegates)
                yield from events
                if (stop := _overrun(prepared)) is not None:
                    cut_short = True
                    yield stop
                    break
            answer = normalize_answer(answer)
            ok = True
        except runtime.OutOfSteps:
            # The other bound, reported like the first. `ok` stays true: the
            # turn ended in a way the caller was told about, which is what that
            # flag records -- not that every step it wanted happened.
            answer = normalize_answer(answer)
            cut_short = True
            ok = True
            yield _out_of_steps(self.cfg)
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # Before the slot goes back, and inside its own `finally` so that a
            # store which is unreachable does not also leak the claim. Ending
            # the turn is the only moment that happens whether the caller read
            # the last event or walked away after the answer.
            try:
                kept = self._keep(prepared)
            finally:
                # The slot goes back however the turn ended -- answered, refused
                # mid-stream, or cut short by its deadline.
                prepared.session.release(self.dirs, self._claims)
            # And so does the connection, when this service opened one. A
            # per-session database is a file descriptor per session, so a
            # process serving many would otherwise hold every one it touched.
            release_checkpointer(prepared.release)
            # And the QuickJS runtime, which is the one of the three that hangs
            # the process rather than leaking a handle. See `release_interpreter`.
            release_interpreter(self.cfg, prepared.graph)

        yield self._finished(prepared, answer, kept, cut_short=cut_short)

    async def astream(self, request: str | Request) -> AsyncIterator[RunEvent]:
        """`stream`, on an event loop.

        The same turn and the same ordering -- `_prepare` is shared, so there
        is one copy of the sequence that matters. What this buys is not a
        faster turn: a turn is the model's time, and measurement puts our own
        code at 15-46ms of 1.5-1.9s. It is concurrency. Four turns measured against
        the live gateway cost 0.4-1.2 turns of wall clock instead of four.

        `_prepare` is filesystem work, so it runs on a worker thread
        rather than blocking every other turn sharing this loop.

        Needs a checkpointer with async methods: `SqliteSaver` raises on
        `aget_tuple` rather than merely blocking the loop. Nothing injected now
        means one per session, opened here because an aiosqlite connection
        belongs to the loop that made it and cannot be built inside the worker
        thread `_prepare` runs on. That is why the session is opened first and
        handed down: naming a session is not idempotent, so it happens once.
        """
        request = Request.coerce(request)
        async with AsyncExitStack() as stack:
            # On the worker thread and into the stack that already wraps this
            # turn, so the root is released the same way the saver is -- and so
            # that holding it, which for a mount is real work, does not block
            # every other turn sharing this loop.
            holding = self._held_session(Request.coerce(request))
            session = await asyncio.to_thread(holding.__enter__)
            # Pushed rather than entered through the stack, for two reasons.
            # `enter_context` loses the session's type through `to_thread`, and
            # `push` leaves the turn's exception reaching a provider's
            # `__exit__` -- a callback would swallow which way the turn ended.
            # After entering, so a hold that failed is not then released.
            stack.push(holding)
            saver = await self._async_checkpointer_for(stack, session.directory)
            async for event in self._astream_turn(request, session, saver):
                yield event

    async def _astream_turn(
        self, request: Request, session: Session, saver: Any
    ) -> AsyncIterator[RunEvent]:
        """One async turn, with its session and saver already resolved.

        Split from `astream` only so the exit stack holding the saver wraps the
        whole turn without indenting the loop that matters.
        """
        prepared = await asyncio.to_thread(self._prepare, request, session, saver)
        answer = ""
        ok = False
        cut_short = False
        kept: tuple[str, ...] = ()
        delegates = runtime.Delegates()
        try:
            # Inside the `try`, not before it. A caller that stops reading
            # during these -- `run_start` is the first -- used to leave the turn
            # with no end at all: the claim stayed taken, the checkpointer
            # stayed open, and nothing was persisted.
            for event in prepared.events:
                yield event
            async for namespace, mode, chunk in prepared.graph.astream(
                runtime.user_payload(prepared.message, prepared.history),
                config=prepared.config,
                stream_mode=runtime.STREAM_MODES,
                subgraphs=True,
            ):
                answer, events = _consume(namespace, mode, chunk, answer, delegates)
                for event in events:
                    yield event
                if (stop := _overrun(prepared)) is not None:
                    cut_short = True
                    yield stop
                    break
            answer = normalize_answer(answer)
            ok = True
        except runtime.OutOfSteps:
            # See the same branch in `stream`. Written twice rather than shared,
            # like the loop above it: the two differ only in `async for`, and
            # factoring three lines out of a generator costs more than it saves.
            answer = normalize_answer(answer)
            cut_short = True
            ok = True
            yield _out_of_steps(self.cfg)
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # As in `stream`, and on a worker thread for the same reason
            # `_prepare` is: a directory walk and a store write would otherwise
            # block every other turn sharing this loop.
            try:
                kept = await asyncio.to_thread(self._keep, prepared)
            finally:
                # The slot goes back however the turn ended -- answered, refused
                # mid-stream, or cut short by its deadline.
                prepared.session.release(self.dirs, self._claims)
            # And so does the connection, when this service opened one. A
            # per-session database is a file descriptor per session, so a
            # process serving many would otherwise hold every one it touched.
            release_checkpointer(prepared.release)
            # And the QuickJS runtime, which is the one of the three that hangs
            # the process rather than leaking a handle. See `release_interpreter`.
            release_interpreter(self.cfg, prepared.graph)

        yield self._finished(prepared, answer, kept, cut_short=cut_short)

    async def arun(self, request: str | Request) -> RunResult:
        """Run one task to completion on an event loop. A drain of `astream`."""
        result: RunResult | None = None
        async for event in self.astream(request):
            if event.kind == "finished":
                result = event.result

        if result is None:  # pragma: no cover -- astream always ends with `finished`
            msg = "astream() ended without a finished event"
            raise RuntimeError(msg)
        return result

    def run(self, request: str | Request) -> RunResult:
        """Run one task to completion. A drain of `stream`."""
        result: RunResult | None = None
        for event in self.stream(request):
            if event.kind == "finished":
                result = event.result

        if result is None:  # pragma: no cover -- stream always ends with `finished`
            msg = "stream() ended without a finished event"
            raise RuntimeError(msg)
        return result

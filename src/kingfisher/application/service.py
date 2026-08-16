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
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic, time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.application import config as config_module
from kingfisher.config import Config
from kingfisher.domain import retention
from kingfisher.domain.capabilities import UNRESTRICTED, Capabilities, withheld
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
)
from kingfisher.infrastructure import runtime
from kingfisher.infrastructure.agent import (
    available_skills,
    build_agent,
    defined_subagents,
    registered_tools,
    workspace_tool_names,
)
from kingfisher.infrastructure.checkpointing import build_checkpointer, thread_ids
from kingfisher.infrastructure.runlog import JsonlRunLogger, log_path
from kingfisher.infrastructure.uploads import provision
from kingfisher.infrastructure.workspace_fs import (
    LocalSessionDirs,
    check_placeable,
    collect_artifacts,
    ensure_layout,
    ensure_session_layout,
    place_data,
    place_inputs,
    protect_data,
    resolve_catalogue,
    session_bytes,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from kingfisher.domain.ports import DefinitionStore, SessionDirs, ThreadStore


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
    #: `(what, names)` for each thing this workspace offers that the request did
    #: not grant -- tools, skills, subagents. Crosses rather than stopping: a
    #: withheld name is a fact about the run, not a refusal.
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = ()


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


def _withheld_by_kind(
    allowed: Capabilities,
    cfg: Config,
    session_dir: Path,
    graph: Any,
    catalogue: Mapping[str, Path],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """What this request left out, per kind, skipping the kinds it left nothing.

    Three axes, one rule. Each differs only in where "what the workspace offers"
    comes from, and none of the three is knowable without asking the thing that
    assembled the agent -- which is why a grant goes stale in the first place.

    The catalogue is passed rather than re-derived, so what a caller is told it
    did not grant is measured against the same directories the agent was built
    from.
    """
    default = Capabilities()
    workspace = tuple(workspace_tool_names(cfg, catalogue=catalogue))
    offered = (
        # Built-ins and workspace tools are granted apart, so they are reported
        # apart: "3 tool(s) not granted" meant nothing when it could have been
        # either kind.
        ("builtin tool", "builtin_tools", tuple(
            n for n in registered_tools(graph) if n not in set(workspace)
        )),
        ("tool", "tools", workspace),
        ("skill", "skills", available_skills(cfg, session_dir, catalogue=catalogue)),
        ("subagent", "subagents", tuple(defined_subagents(cfg, session_dir, catalogue=catalogue))),
    )
    found = []
    for what, field, names in offered:
        granted = getattr(allowed, field)
        # Silent when the request left an axis alone. `subagents` defaults to
        # none, so reporting every axis at its default would put a line about
        # undeclared delegates on every run -- which is the noise this event
        # exists to avoid being.
        if granted == getattr(default, field):
            continue
        if left_out := withheld(granted, offered=names):
            found.append((what, left_out))
    return tuple(found)


def opening_events(
    turn_dir: str,
    unprotected: tuple[str, ...],
    placement: Any,
    withheld: tuple[tuple[str, tuple[str, ...]], ...] = (),
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


class Kingfisher:
    """A configured kingfisher. Construct once; call `run` or `stream` per request.

    Construction is where the deployment-scoped work happens -- creating the
    layout, dropping write bits on `/data`, opening the thread store. Doing it
    here rather than per request also means a broken workspace or an
    unreachable state directory fails at startup, not on the first turn.

    Every collaborator is injectable, and by protocol rather than by patching:
    a test hands in its own `SessionDirs` to watch turn allocation, or its own
    agent to drive a scripted conversation.

    `catalogue_roots` is the exception to that shape, and deliberately: it is
    three paths rather than an object, because there is nothing for an object to
    do yet. Whatever fetches a catalogue stages it and hands over the
    directories; kingfisher reads them and never copies. An interface with a
    `roots()` on it can be accepted alongside the mapping on the day a source
    needs behaviour -- lazy fetching, version pinning, refresh without a restart
    -- and until one does, inventing it would mean guessing at what it wants.
    """

    def __init__(  # noqa: PLR0913 -- the composition root; each argument is one
        # collaborator a deployment or a test substitutes, and folding them into
        # a parameter object would hide exactly what is substitutable.
        self,
        cfg: Config | None = None,
        *,
        dirs: SessionDirs | None = None,
        threads: ThreadStore | None = None,
        definitions: DefinitionStore | None = None,
        catalogue_roots: Mapping[str, Path] | None = None,
        grants: Capabilities | None = None,
        middleware: Mapping[str, Callable[[], Any]] | None = None,
        agent: Any | None = None,
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
        self.catalogue: Mapping[str, Path] = resolve_catalogue(self.cfg, catalogue_roots)

        self.dirs: Any = dirs if dirs is not None else LocalSessionDirs()
        # Host-side, beside the run logs, because the session directory is the
        # agent's own root -- a claim kept there would be something `execute`
        # could delete. `state_dir` is the one place the agent never addresses.
        self._claims: Path = self.cfg.state_dir / "claims"
        self.dirs.ensure(self._claims)
        self.threads: Any = threads if threads is not None else build_checkpointer(self.cfg)
        # No default. A deployment that never serves uploaded definitions has
        # nothing to wire, and a request that supplies ids without one is a
        # configuration error worth saying out loud rather than a silent no-op.
        self.definitions: Any = definitions
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
        self.middleware: Mapping[str, Any] = middleware or {}
        self._agent = agent

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
        if request.session_id not in self.dirs.children(root):
            msg = f"no session {request.session_id!r}; omit session_id to start one"
            raise UnknownSessionError(msg)
        return request.session_id

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
        """
        root = sessions_root(self.workspace)
        if session_id not in self.dirs.children(root):
            return None
        return Session(id=session_id, directory=root / session_id).discard(
            self.dirs, self.threads
        )

    def reap(self, older_than_seconds: float | None = None, *, now: float) -> SweepResult:
        """Dispose of every session untouched for `older_than_seconds`.

        The backstop under `delete_session`, for callers that never call it.
        Meant to be run by a janitor on its own schedule, not on a request:
        deleting somebody else's session is not part of serving a turn, and
        putting it there is what made retention a tenancy bug.

        `now` is passed in rather than read, so the decision stays testable
        and this stays a function of its arguments.
        """
        root = sessions_root(self.workspace)
        age = self.cfg.session_ttl_s if older_than_seconds is None else older_than_seconds
        plan = retention.expired(
            self.dirs.listing(root),
            age,
            now,
            busy=self.dirs.children(self._claims),
        )
        result = retention.apply(plan, root, self.dirs, self.threads)
        return self._reconcile_threads(root, result)

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
        held = thread_ids(self.threads)
        if held is None:
            return result

        live = self.dirs.children(root)
        dropped = []
        for thread in retention.orphaned(held, live):
            with suppress(Exception):
                self.threads.delete_thread(thread)
                dropped.append(thread)
        return replace(result, orphans=tuple(dropped))

    def agent_for(
        self, request: Request, session_dir: Path, capabilities: Capabilities | None = None
    ) -> Any:
        """The graph that serves one request, rooted at its session.

        Built per request because capabilities narrow it, because it reads
        workspace content that can change between turns, and now because its
        backend is anchored to the session -- two sessions cannot share a
        graph without sharing a filesystem root. An injected agent is returned
        as-is -- and refused if the request narrows anything, since those
        restrictions were never applied to it.
        """
        if self._agent is not None:
            if not request.capabilities.is_unrestricted:
                msg = "cannot honour request.capabilities against a pre-built agent"
                raise ValueError(msg)
            return self._agent

        return build_agent(
            self.cfg,
            capabilities=capabilities if capabilities is not None else request.capabilities,
            session_dir=session_dir,
            middleware_registry=self.middleware,
            checkpointer=self.threads,
            catalogue=self.catalogue,
        )

    def _prepare(self, request: str | Request) -> _Prepared:
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
        return self._open_turn(self._admit(request))

    def _admit(self, request: str | Request) -> _Admitted:
        """Everything that can refuse, before anything a refusal would strand.

        Nothing is destroyed here either, and nothing turn-shaped is created.
        The session directory is, which the rule tolerates: an empty one left
        by a rejected request is idempotent, and the retry reuses it.
        """
        request = Request.coerce(request)
        cfg, dirs = self.cfg, self.dirs
        workspace = self.workspace
        root = sessions_root(workspace)
        session_id = self._session_id_for(request, root)

        # The session directory has to exist before the agent, because the
        # agent's backend is rooted at it. Creating it first does not weaken
        # the ordering rule below: that rule is about not *destroying*
        # anything before the request is known to be valid, and an empty
        # session directory left by a rejected request is idempotent -- the
        # retry reuses it.
        session = Session.open(workspace, session_id, dirs)
        ensure_session_layout(session.directory)
        # A turn writes inside the session, never to the session itself, so the
        # timestamp `retention.expired` reads would still say "idle" for a
        # conversation in daily use. Recorded here, at the top of a turn, rather
        # than at the end: a turn that fails still happened.
        dirs.mark_used(session.directory)
        # Before the other refusals rather than after: those read the session,
        # and a turn arriving halfway through would be reading it as it moved.
        session.claim(dirs, self._claims, stale_after=cfg.turn_timeout_s, now=time())
        try:
            return self._admitted(request, session, cfg)
        except BaseException:
            session.release(dirs, self._claims)
            raise

    def _admitted(
        self, request: Request, session: Session, cfg: Config
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

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        placement = place_data(request.data, session.directory)

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
        graph = self.agent_for(request, session.directory, capabilities=allowed)

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
            # Tools come off the assembled graph rather than a list kept
            # somewhere: the surface includes whatever the workspace defined, so
            # the only honest answer to "what was offered" is what was wired.
            # Skills and subagents are not on the graph, so they are asked of
            # the same functions `build_agent` asked -- 0.04ms and 1.4ms against
            # an admit already measured at 15-46ms.
            withheld=_withheld_by_kind(allowed, cfg, session.directory, graph, self.catalogue),
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

        place_inputs(request.inputs, turn.input_dir)

        logger = JsonlRunLogger(
            log_path(cfg.state_dir, session_id),
            model=cfg.model,
            api_style=cfg.api_style,
            session_id=session_id,
        )
        logger.run_start(request.task, turn.virtual_dir)

        return _Prepared(
            graph=admitted.graph,
            message=turn_message(
                request.task, turn, admitted.placement.placed, has_inputs=bool(request.inputs)
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
            ),
            deadline=monotonic() + cfg.turn_timeout_s,
            timeout_s=cfg.turn_timeout_s,
        )

    def _finished(self, prepared: _Prepared, answer: str, *, cut_short: bool) -> RunEvent:
        """The terminal event, built the same way whichever loop produced it."""
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
                artifacts=collect_artifacts(prepared.session.directory),
                cut_short=cut_short,
            ),
        )

    def stream(self, request: str | Request) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        The terminal event has `kind == "finished"` and carries the `RunResult`.
        """
        prepared = self._prepare(request)
        yield from prepared.events

        answer = ""
        ok = False
        cut_short = False
        delegates = runtime.Delegates()
        try:
            for namespace, mode, chunk in prepared.graph.stream(
                runtime.user_payload(prepared.message),
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
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # The slot goes back however the turn ended -- answered, refused
            # mid-stream, or cut short by its deadline.
            prepared.session.release(self.dirs, self._claims)

        yield self._finished(prepared, answer, cut_short=cut_short)

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
        `aget_tuple`, so build one with `async_checkpointer(cfg)`.
        """
        prepared = await asyncio.to_thread(self._prepare, request)
        for event in prepared.events:
            yield event

        answer = ""
        ok = False
        cut_short = False
        delegates = runtime.Delegates()
        try:
            async for namespace, mode, chunk in prepared.graph.astream(
                runtime.user_payload(prepared.message),
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
        finally:
            prepared.logger.run_end(ok=ok, answer_chars=len(answer))
            # The slot goes back however the turn ended -- answered, refused
            # mid-stream, or cut short by its deadline.
            prepared.session.release(self.dirs, self._claims)

        yield self._finished(prepared, answer, cut_short=cut_short)

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

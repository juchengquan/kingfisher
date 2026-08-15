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
directory the user can edit between turns. Building it costs ~30ms against a
model call of two to three seconds, which is not a trade worth taking.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from kingfisher.adapters import runtime
from kingfisher.adapters.agent import build_agent
from kingfisher.adapters.checkpointing import build_checkpointer
from kingfisher.adapters.runlog import JsonlRunLogger, log_path
from kingfisher.adapters.uploads import provision
from kingfisher.adapters.workspace_fs import (
    LocalSessionDirs,
    collect_artifacts,
    ensure_layout,
    ensure_session_layout,
    place_data,
    protect_data,
)
from kingfisher.app import config as config_module
from kingfisher.config import Config
from kingfisher.domain import retention
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.domain.retention import SweepResult
from kingfisher.domain.session import Session, UnknownSessionError

if TYPE_CHECKING:
    from kingfisher.domain.ports import DefinitionStore, SessionDirs, ThreadStore


class Kingfisher:
    """A configured kingfisher. Construct once; call `run` or `stream` per request.

    Construction is where the deployment-scoped work happens -- creating the
    layout, dropping write bits on `/data`, opening the thread store. Doing it
    here rather than per request also means a broken workspace or an
    unreachable state directory fails at startup, not on the first turn.

    Every collaborator is injectable, and by protocol rather than by patching:
    a test hands in its own `SessionDirs` to watch turn allocation, or its own
    agent to drive a scripted conversation.
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
        grants: Capabilities | None = None,
        agent: Any | None = None,
    ) -> None:
        self.cfg = cfg or config_module.from_env()
        config_module.enforce_local_only_tracing()

        # Only what sessions share. Each session's own layout is made per
        # request, because its path is not known until the request names it.
        self.workspace: Path = ensure_layout(self.cfg.workspace)

        self.dirs: Any = dirs if dirs is not None else LocalSessionDirs()
        self.threads: Any = threads if threads is not None else build_checkpointer(self.cfg)
        # No default. A deployment that never serves uploaded definitions has
        # nothing to wire, and a request that supplies ids without one is a
        # configuration error worth saying out loud rather than a silent no-op.
        self.definitions: Any = definitions
        # What this deployment permits, before any request asks for anything.
        # Unrestricted by default, so a single-caller deployment is unaffected;
        # a service in front of many callers sets it, and `intersect` can only
        # subtract, so no request can widen past it.
        self.grants: Capabilities = grants or Capabilities()
        self._agent = agent

    def _session_id_for(self, request: Request, sessions_root: Path) -> str:
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
        if request.session_id not in self.dirs.children(sessions_root):
            msg = f"no session {request.session_id!r}; omit session_id to start one"
            raise UnknownSessionError(msg)
        return request.session_id

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
        sessions_root = self.workspace / "sessions"
        if session_id not in self.dirs.children(sessions_root):
            return None
        return Session(id=session_id, directory=sessions_root / session_id).discard(
            self.dirs, self.threads
        )

    def reap(self, older_than_seconds: float, now: float) -> SweepResult:
        """Dispose of every session untouched for `older_than_seconds`.

        The backstop under `delete_session`, for callers that never call it.
        Meant to be run by a janitor on its own schedule, not on a request:
        deleting somebody else's session is not part of serving a turn, and
        putting it there is what made retention a tenancy bug.

        `now` is passed in rather than read, so the decision stays testable
        and this stays a function of its arguments.
        """
        sessions_root = self.workspace / "sessions"
        plan = retention.expired(self.dirs.listing(sessions_root), older_than_seconds, now)
        return retention.apply(plan, sessions_root, self.dirs, self.threads)

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
            checkpointer=self.threads,
        )

    def stream(self, request: str | Request) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        The terminal event has `kind == "finished"` and carries the `RunResult`.

        Nothing is destroyed here. Retention used to run on this path, keeping
        the newest N sessions -- which counts every caller's sessions together,
        so one busy caller evicted another's conversation. Disposal is now
        asked for: `delete_session` and `reap`.

        Ordering still matters for the rest. Anything that can reject the
        request happens before the turn directory exists, so a typo does not
        leave one behind.
        """
        request = Request.coerce(request)
        cfg, dirs = self.cfg, self.dirs
        workspace = self.workspace
        sessions_root = workspace / "sessions"
        session_id = self._session_id_for(request, sessions_root)

        # The session directory has to exist before the agent, because the
        # agent's backend is rooted at it. Creating it first does not weaken
        # the ordering rule below: that rule is about not *destroying*
        # anything before the request is known to be valid, and an empty
        # session directory left by a rejected request is idempotent -- the
        # retry reuses it.
        session = Session.open(workspace, session_id, dirs)
        ensure_session_layout(session.directory)
        # Kernel-level guard; the deny rule covers only the file tools. Paths
        # it could not harden are reported below rather than raised: they used
        # to abort the run, and since this runs before anything else, one file
        # owned by another user made a session unusable for good.
        unprotected = protect_data(session.directory)

        # Before the turn exists, and before anything is destroyed: a request
        # naming a file that is not there must fail without having placed the
        # ones that were. `place_data` re-hardens `/data` on its way out.
        placement = place_data(request.data, session.directory)

        # Before the agent, which discovers definitions by reading the
        # directories this writes.
        brought = provision(request, self.definitions, session.directory, cfg)

        # What this deployment permits, narrowed by what the request asked for.
        # Definitions the request brought itself are added back: their content
        # came from the caller, so a grant list -- written before their names
        # existed -- has no opinion about them.
        allowed = self.grants.intersect(request.capabilities).including(
            skills=brought.skills, subagents=brought.subagents
        )
        graph = self.agent_for(request, session.directory, capabilities=allowed)


        # The aggregate owns turn allocation: atomic, and a caller-supplied id wins.
        turn = session.allocate_turn(dirs, request.turn_id)

        if request.inputs:
            turn.input_dir.mkdir(exist_ok=True)
            for source in request.inputs:
                shutil.copy(source, turn.input_dir / Path(source).name)

        logger = JsonlRunLogger(
            log_path(cfg.state_dir, session_id),
            model=cfg.model,
            api_style=cfg.api_style,
            session_id=session_id,
        )
        logger.run_start(request.task, turn.virtual_dir)

        if unprotected:
            yield RunEvent(kind="protect_failed", text="; ".join(unprotected))
        if placement.placed:
            # Replacement is the one dangerous case -- durable data, silently
            # overwritten -- so it is named rather than assumed.
            replaced = f" ({len(placement.replaced)} replaced)" if placement.replaced else ""
            yield RunEvent(kind="data_placed", text=f"{', '.join(placement.placed)}{replaced}")
        yield RunEvent(kind="run_start", text=turn.virtual_dir)

        # The turn directory is run-scoped, so it reaches the model here rather
        # than in the system prompt — putting it there would change the cached
        # prefix on every session.
        supplied = (
            f" Files supplied with this request are in {turn.virtual_input_dir}."
            if request.inputs
            else ""
        )
        # Named because `/data` changed under a session the agent may already
        # have looked at. In the turn message, not the prompt, for the same
        # reason the run directory is: the prompt's cached prefix must not move.
        arrived = (
            f" New files in /data: {', '.join(placement.placed)}." if placement.placed else ""
        )
        # This turn's facts, and nothing more. What the task should produce is the
        # task's business: asking for a written report is one kind of request among
        # many, and a general agent should not carry one convention's filenames in
        # its plumbing. They lived in the system prompt once, which made every
        # greeting deliberate over two files nobody wanted.
        message = (
            f"{request.task}\n\n"
            f"Your run directory for this task is {turn.virtual_dir}.{supplied}{arrived}"
        )

        answer = ""
        ok = False
        try:
            for mode, chunk in graph.stream(
                runtime.user_payload(message),
                config={
                    "configurable": {"thread_id": session_id},
                    "callbacks": [logger],
                    "recursion_limit": cfg.recursion_limit,
                },
                stream_mode=runtime.STREAM_MODES,
            ):
                # Both are offered every chunk and each ignores the modes that
                # are not its own. Which mode carries what is the adapter's
                # knowledge, not this module's.
                if (text := runtime.answer_in(mode, chunk)) is not None:
                    answer = text
                yield from runtime.events_in(mode, chunk)

            answer = normalize_answer(answer)
            ok = True
        finally:
            logger.run_end(ok=ok, answer_chars=len(answer))

        yield RunEvent(
            kind="finished",
            text=answer,
            result=RunResult(
                session_id=session_id,
                turn_id=turn.id,
                answer=answer,
                run_dir=turn.directory,
                log_path=log_path(cfg.state_dir, session_id),
                # Collected after the graph has finished, so it reflects what
                # the turn actually left behind -- including what the shell
                # wrote, which no file tool would have reported.
                artifacts=collect_artifacts(session.directory),
            ),
        )

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

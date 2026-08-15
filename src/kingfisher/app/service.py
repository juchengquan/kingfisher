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
from kingfisher.adapters.workspace_fs import (
    LocalSessionDirs,
    ensure_layout,
    ensure_session_layout,
    protect_data,
)
from kingfisher.adapters.workspace_git import pre_run_commit
from kingfisher.app import config as config_module
from kingfisher.config import Config
from kingfisher.domain import retention
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.domain.session import Session

if TYPE_CHECKING:
    from kingfisher.domain.ports import SessionDirs, ThreadStore


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

    def __init__(
        self,
        cfg: Config | None = None,
        *,
        dirs: SessionDirs | None = None,
        threads: ThreadStore | None = None,
        agent: Any | None = None,
    ) -> None:
        self.cfg = cfg or config_module.from_env()
        config_module.enforce_local_only_tracing()

        # Only what sessions share. Each session's own layout is made per
        # request, because its path is not known until the request names it.
        self.workspace: Path = ensure_layout(self.cfg.workspace)

        self.dirs: Any = dirs if dirs is not None else LocalSessionDirs()
        self.threads: Any = threads if threads is not None else build_checkpointer(self.cfg)
        self._agent = agent

    def agent_for(self, request: Request, session_dir: Path) -> Any:
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
            capabilities=request.capabilities,
            session_dir=session_dir,
            checkpointer=self.threads,
        )

    def stream(self, request: str | Request) -> Iterator[RunEvent]:
        """Run one task, yielding progress as it happens.

        The terminal event has `kind == "finished"` and carries the `RunResult`.

        Ordering matters. Anything that can reject the request is done first,
        while nothing has been written or removed yet: a request naming a
        capability the workspace does not offer used to raise only after the
        sweep had deleted old sessions, which made a typo destructive.

        Then commit, then sweep, then create this turn's directory. The sweep
        runs after the commit, so the restore point covers the state it is
        about to change, and skips this session by name so a run can never
        delete itself.
        """
        request = Request.coerce(request)
        cfg, dirs, checkpointer = self.cfg, self.dirs, self.threads
        workspace = self.workspace
        session_id = request.session_id or uuid4().hex[:12]

        # The session directory has to exist before the agent, because the
        # agent's backend is rooted at it. Creating it first does not weaken
        # the ordering rule below: that rule is about not *destroying*
        # anything before the request is known to be valid, and an empty
        # session directory left by a rejected request is idempotent -- the
        # retry reuses it.
        session = Session.open(workspace, session_id, dirs)
        ensure_session_layout(session.directory)
        # Kernel-level guard; the deny rule covers only the file tools.
        protect_data(session.directory)

        # Built before anything is removed. Construction is side-effect free
        # but validation is not free of *consequence*: a request naming a
        # capability the workspace lacks used to raise only after the sweep had
        # deleted old sessions. A usage error must not be destructive.
        graph = self.agent_for(request, session.directory)

        commit = pre_run_commit(workspace, f"kingfisher: pre-run {session_id}")
        # Decide, then act. `retention.plan` is pure -- it names victims from
        # `(name, mtime)` pairs and touches nothing -- and `retention.apply` walks
        # the list, leaving the per-session ordering to `Session.discard`.
        #
        # This session is excluded by name rather than by running the sweep
        # before its directory exists, which is how it used to be kept safe.
        # It cannot run first any more -- the agent needs the session rooted
        # before it can be built, and the agent is what validates the request.
        # Naming the exemption is the better guarantee anyway: it does not
        # depend on `keep_runs` being positive, or on this session's mtime
        # happening to be the newest.
        sessions = workspace / "sessions"
        others = tuple(e for e in dirs.listing(sessions) if e[0] != session_id)
        sweep_plan = retention.plan(others, cfg.keep_runs)
        swept = retention.apply(sweep_plan, sessions, dirs, checkpointer)

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
        logger.swept(swept.removed, swept.kept)
        logger.run_start(request.task, turn.virtual_dir)

        if swept.removed:
            yield RunEvent(kind="swept", text=", ".join(swept.removed))
        if swept.failures:
            yield RunEvent(kind="sweep_failed", text="; ".join(swept.failures))
        yield RunEvent(kind="run_start", text=turn.virtual_dir)

        # The turn directory is run-scoped, so it reaches the model here rather
        # than in the system prompt — putting it there would change the cached
        # prefix on every session.
        supplied = (
            f" Files supplied with this request are in {turn.virtual_input_dir}."
            if request.inputs
            else ""
        )
        # This turn's facts, and nothing more. What the task should produce is the
        # task's business: asking for a written report is one kind of request among
        # many, and a general agent should not carry one convention's filenames in
        # its plumbing. They lived in the system prompt once, which made every
        # greeting deliberate over two files nobody wanted.
        message = (
            f"{request.task}\n\n"
            f"Your run directory for this task is {turn.virtual_dir}.{supplied}"
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
                if mode == "values":
                    # Full state each step; the last one carries the final answer.
                    if (text := runtime.final_text(chunk)) is not None:
                        answer = text
                    continue
                yield from runtime.events_in(chunk)

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
                swept=swept.removed,
                commit=commit,
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

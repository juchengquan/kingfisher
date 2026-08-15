"""Running a task — orchestration, and everything with a side effect.

`stream()` is the primitive and `run()` drains it, so the orchestration
sequence (commit, sweep, turn directory, logging, normalisation) exists exactly
once. Two entrypoints with two copies of that sequence would drift, and the
drift would be silent.

Ordering matters. Anything that can reject the request is done first, while
nothing has been written or removed yet: a request naming a capability the
workspace does not offer used to raise only after the sweep had deleted old
sessions, which made a typo destructive.

Then commit, then sweep, then create this turn's directory. The sweep runs
before the new directory exists so it is never a candidate for its own
deletion, and after the commit so the restore point covers the state the sweep
is about to change.

This module speaks only kingfisher's vocabulary. Every LangChain and LangGraph
shape lives behind `adapters.runtime`.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

from kingfisher.adapters import runtime
from kingfisher.adapters.agent import build_agent
from kingfisher.adapters.checkpointing import build_checkpointer
from kingfisher.adapters.runlog import JsonlRunLogger, log_path
from kingfisher.app import config as config_module
from kingfisher.domain.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.domain.session import Session
from kingfisher.domain.workspace import (
    ensure_layout,
    pre_run_commit,
    protect_data,
    sweep,
)

__all__ = [
    "Request",
    "RunEvent",
    "RunResult",
    "new_session_id",
    "normalize_answer",
    "run",
    "stream",
]


def new_session_id() -> str:
    return uuid4().hex[:12]


def stream(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens.

    The terminal event has `kind == "finished"` and carries the `RunResult`.

        for event in stream("profile /data/orders.csv"):
            print(event)

        for event in stream(Request(task, session_id=sid, turn_id=req.id)):
            print(event)
    """
    request = Request.coerce(request)

    cfg = cfg or config_module.from_env()
    config_module.enforce_local_only_tracing()

    workspace = ensure_layout(cfg.workspace)
    protect_data(workspace)  # kernel-level guard; the deny rule covers only file tools
    checkpointer = checkpointer if checkpointer is not None else build_checkpointer(cfg)
    session_id = request.session_id or new_session_id()

    if agent is not None and not request.capabilities.is_unrestricted:
        # An injected agent was built elsewhere, so this request's restrictions
        # were never applied to it. Refusing beats running with more access
        # than the caller asked for and saying nothing.
        msg = "cannot honour request.capabilities against a pre-built agent"
        raise ValueError(msg)

    # Assembled before anything is written or removed. Construction is
    # side-effect free but validation is not free of *consequence*: a request
    # naming a capability the workspace lacks used to raise only after the
    # sweep had deleted old sessions and a turn directory existed. A usage
    # error must not be destructive.
    graph = (
        agent
        if agent is not None
        else build_agent(
            cfg, capabilities=request.capabilities, checkpointer=checkpointer
        )
    )

    commit = pre_run_commit(workspace, f"kingfisher: pre-run {session_id}")
    swept = sweep(workspace, cfg.keep_runs, checkpointer)

    # The aggregate owns turn allocation: atomic, and a caller-supplied id wins.
    turn = Session.open(workspace, session_id).allocate_turn(request.turn_id)

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


def run(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed.

    A drain of `stream()` — there is no second orchestration path.
    """
    result: RunResult | None = None
    for event in stream(request, cfg=cfg, agent=agent, checkpointer=checkpointer):
        if event.kind == "finished":
            result = event.result

    if result is None:  # pragma: no cover -- stream always terminates with `finished`
        msg = "stream() ended without a finished event"
        raise RuntimeError(msg)
    return result

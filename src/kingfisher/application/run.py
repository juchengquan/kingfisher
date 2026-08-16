"""Module-level conveniences over a default `Kingfisher`.

    run("profile /data/orders.csv")

The orchestration itself lives on `Kingfisher`, with the wiring it needs. This
is the one-liner surface, unchanged from before the service object existed, so
nothing calling it had to learn a new shape. It builds an instance per call --
fine for a script, wasteful for a server, which is the case `Kingfisher` is for.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import uuid4

from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer

__all__ = [
    "Kingfisher",
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


def _service(cfg: Config | None, agent: Any, checkpointer: Any, dirs: Any) -> Kingfisher:
    return Kingfisher(cfg, agent=agent, threads=checkpointer, dirs=dirs)


def stream(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
    dirs: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens.

        for event in stream("profile /data/orders.csv"):
            print(event)

        for event in stream(Request(task, session_id=sid, turn_id=req.id)):
            print(event)
    """
    return _service(cfg, agent, checkpointer, dirs).stream(request)


def run(
    request: str | Request,
    *,
    cfg: Config | None = None,
    agent: Any | None = None,
    checkpointer: Any | None = None,
    dirs: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed."""
    return _service(cfg, agent, checkpointer, dirs).run(request)

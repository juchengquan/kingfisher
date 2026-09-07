"""Module-level conveniences over a default `Kingfisher`.

    run("profile /data/orders.csv")
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer

__all__ = [
    "Kingfisher",
    "Request",
    "RunEvent",
    "RunResult",
    "normalize_answer",
    "run",
    "stream",
]


def _service(cfg: Config | None, graph: Any, checkpointer: Any, dirs: Any) -> Kingfisher:
    return Kingfisher(cfg, graph=graph, threads=checkpointer, dirs=dirs)


def stream(
    request: str | Request,
    *,
    cfg: Config | None = None,
    graph: Any | None = None,
    checkpointer: Any | None = None,
    dirs: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens."""
    return _service(cfg, graph, checkpointer, dirs).stream(request)


def run(
    request: str | Request,
    *,
    cfg: Config | None = None,
    graph: Any | None = None,
    checkpointer: Any | None = None,
    dirs: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed."""
    return _service(cfg, graph, checkpointer, dirs).run(request)

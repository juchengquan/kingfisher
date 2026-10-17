"""Module-level conveniences: one `Kingfisher`, built and driven in a single call.

Sessions are required, as on `Kingfisher`: Python that runs kingfisher says what it runs on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.core.config import Config
from kingfisher.core.interfaces import Sessions
from kingfisher.core.request import Request
from kingfisher.core.result import RunEvent, RunResult, normalize_answer
from kingfisher.core.service import Kingfisher

__all__ = [
    "Kingfisher",
    "Request",
    "RunEvent",
    "RunResult",
    "normalize_answer",
    "run",
    "stream",
]


def _service(
    cfg: Config | None,
    *,
    graph: Any,
    sessions: Sessions,
    run_events: Any,
) -> Kingfisher:
    return Kingfisher(
        cfg,
        graph=graph,
        sessions=sessions,
        run_events=run_events,
    )


def stream(
    request: str | Request,
    *,
    sessions: Sessions,
    cfg: Config | None = None,
    graph: Any | None = None,
    run_events: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens."""
    return _service(
        cfg,
        graph=graph,
        sessions=sessions,
        run_events=run_events,
    ).stream(request)


def run(
    request: str | Request,
    *,
    sessions: Sessions,
    cfg: Config | None = None,
    graph: Any | None = None,
    run_events: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed."""
    return _service(
        cfg,
        graph=graph,
        sessions=sessions,
        run_events=run_events,
    ).run(request)


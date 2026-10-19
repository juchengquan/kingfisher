"""Module-level conveniences: one `Kingfisher`, built and driven in a single call.

Sessions are required, as on `Kingfisher`: Python that runs kingfisher says what it runs on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from .config import Config
from .interfaces import Sessions
from .request import Request
from .result import RunEvent, RunResult
from .service import Kingfisher

__all__ = ["run", "stream"]


def stream(
    request: str | Request,
    *,
    sessions: Sessions,
    cfg: Config | None = None,
    graph: Any | None = None,
    run_events: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens."""
    return Kingfisher(
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
    return Kingfisher(
        cfg,
        graph=graph,
        sessions=sessions,
        run_events=run_events,
    ).run(request)


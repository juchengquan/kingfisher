"""Module-level conveniences: one `Kingfisher`, built and driven in a single call.

They take the sessions `Kingfisher` takes, and as it does, refuse to run
without them: Python that runs kingfisher says what it runs on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.core.agent.backend import Sessions
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


def _service(
    cfg: Config | None,
    *,
    graph: Any,
    sessions: Sessions,
    checkpointer: Any,
    run_events: Any,
) -> Kingfisher:
    return Kingfisher(
        cfg,
        graph=graph,
        sessions=sessions,
        threads=checkpointer,
        run_events=run_events,
    )


def stream(  # noqa: PLR0913 -- one parameter per collaborator `Kingfisher`
    # takes, named again here so that changing one does not cost a caller the
    # whole constructor
    request: str | Request,
    *,
    sessions: Sessions,
    cfg: Config | None = None,
    graph: Any | None = None,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens.
    """
    return _service(
        cfg,
        graph=graph,
        sessions=sessions,
        checkpointer=checkpointer,
        run_events=run_events,
    ).stream(request)


def run(  # noqa: PLR0913 -- one parameter per collaborator `Kingfisher`
    # takes, named again here so that changing one does not cost a caller the
    # whole constructor
    request: str | Request,
    *,
    sessions: Sessions,
    cfg: Config | None = None,
    graph: Any | None = None,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed.
    """
    return _service(
        cfg,
        graph=graph,
        sessions=sessions,
        checkpointer=checkpointer,
        run_events=run_events,
    ).run(request)


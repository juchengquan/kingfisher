"""Module-level conveniences: one `Kingfisher`, built and driven in a single call.

They take the sessions `Kingfisher` takes, and as it does, refuse to run
without them: Python that runs kingfisher says what it runs on.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.application.plugins import Plugins
from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.infrastructure.harness.backend import LocalSessions, Sessions
from kingfisher.infrastructure.wiring import store_named

__all__ = [
    "Kingfisher",
    "Request",
    "RunEvent",
    "RunResult",
    "configured_plugins",
    "configured_sessions",
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


def configured_sessions(cfg: Config) -> Sessions:
    """The sessions `KINGFISHER_SESSIONS` names, or `LocalSessions`.

    For the command line, which builds its own `Kingfisher` and has nowhere else to be
    told. Without it, `kingfisher sessions` and `reap` would list and delete a local
    `sessions/` while a deployment's own keep them somewhere else.
    """
    if cfg.sessions_factory is None:
        return LocalSessions()
    return store_named(cfg.sessions_factory, setting="KINGFISHER_SESSIONS", port=Sessions)


def configured_plugins(cfg: Config) -> Plugins:
    """The plugins `KINGFISHER_PLUGINS` names, or none, for kingfisher's own of each.

    For the command line, for the reason `configured_sessions` is: a deployment whose
    turns record their events somewhere, or keep their conversations in a database,
    would otherwise have every command run without either.
    """
    if cfg.plugins_factory is None:
        return Plugins()
    return store_named(cfg.plugins_factory, setting="KINGFISHER_PLUGINS", port=Plugins)

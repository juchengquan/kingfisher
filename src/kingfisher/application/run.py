"""Module-level conveniences over a default `Kingfisher`.

Default session backends where `Kingfisher` refuses to pick any, and the
difference is what each is for: `Kingfisher` is the composition root, where a
deployment says what its agents run on, and these two are the one-liner that
spares a caller from saying it -- unless the caller hands over a graph, which
names its session backends here as it would to `Kingfisher`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.infrastructure.harness.backend import SessionBackends, default_backends
from kingfisher.infrastructure.wiring import store_named

__all__ = [
    "Kingfisher",
    "Request",
    "RunEvent",
    "RunResult",
    "configured_backends",
    "normalize_answer",
    "run",
    "stream",
]


def _service(
    cfg: Config | None,
    *,
    graph: Any,
    backends: SessionBackends | None,
    checkpointer: Any,
    run_events: Any,
) -> Kingfisher:
    if backends is None and graph is not None:
        msg = (
            "a pre-built graph needs session backends named beside it: they are how "
            "kingfisher reaches the sessions its backend keeps, and only the graph's "
            "builder knows where that is. Pass backends=default_backends if it was "
            "built on kingfisher's own"
        )
        raise ValueError(msg)
    return Kingfisher(
        cfg,
        graph=graph,
        backends=default_backends if backends is None else backends,
        threads=checkpointer,
        run_events=run_events,
    )


def stream(  # noqa: PLR0913 -- one parameter per collaborator `Kingfisher`
    # takes, named again here so that changing one does not cost a caller the
    # whole constructor
    request: str | Request,
    *,
    cfg: Config | None = None,
    graph: Any | None = None,
    backends: SessionBackends | None = None,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens.

    `backends` left out is `default_backends`, except beside `graph`, which has to
    name them.
    """
    return _service(
        cfg,
        graph=graph,
        backends=backends,
        checkpointer=checkpointer,
        run_events=run_events,
    ).stream(request)


def run(  # noqa: PLR0913 -- one parameter per collaborator `Kingfisher`
    # takes, named again here so that changing one does not cost a caller the
    # whole constructor
    request: str | Request,
    *,
    cfg: Config | None = None,
    graph: Any | None = None,
    backends: SessionBackends | None = None,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed.

    `backends` left out is `default_backends`, except beside `graph`, which has to
    name them.
    """
    return _service(
        cfg,
        graph=graph,
        backends=backends,
        checkpointer=checkpointer,
        run_events=run_events,
    ).run(request)


def configured_backends(cfg: Config) -> SessionBackends:
    """The session backends `KINGFISHER_SESSION_BACKENDS_FACTORY` names, or the default.

    For the command line, which builds its own `Kingfisher` and has nowhere else to be
    told. Without it, `kingfisher sessions` and `reap` would list and delete a local
    `sessions/` while a deployment's own keep them somewhere else.
    """
    if cfg.session_backends_factory is None:
        return default_backends
    return store_named(
        cfg.session_backends_factory,
        setting="KINGFISHER_SESSION_BACKENDS_FACTORY",
        port=SessionBackends,
    )

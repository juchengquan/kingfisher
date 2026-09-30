"""Module-level conveniences over a default `Kingfisher`.

A default backend where `Kingfisher` refuses one, and the difference is what each
is for: `Kingfisher` is the composition root, where a deployment says what its
agents run on, and these two are the one-liner that spares a caller from saying it.
The name is in the signature rather than hidden in the body, so a reader can see
what they are getting and hand over something else without abandoning the helper.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from kingfisher.application.service import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.domain.result import RunEvent, RunResult, normalize_answer
from kingfisher.infrastructure.harness.backend import SessionBackends, default_backend
from kingfisher.infrastructure.wiring import store_named

__all__ = [
    "Kingfisher",
    "Request",
    "RunEvent",
    "RunResult",
    "configured_backend",
    "normalize_answer",
    "run",
    "stream",
]


def _service(
    cfg: Config | None,
    *,
    graph: Any,
    backend: Any,
    checkpointer: Any,
    run_events: Any,
) -> Kingfisher:
    return Kingfisher(
        cfg,
        graph=graph,
        # Not beside a graph. A pre-built one already carries its filesystem and
        # `Kingfisher` refuses both, so passing the default here unconditionally
        # would make every caller that supplies a graph commit the one mistake
        # this helper exists to spare them.
        backend=None if graph is not None else backend,
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
    backend: Any = default_backend,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> Iterator[RunEvent]:
    """Run one task, yielding progress as it happens."""
    return _service(
        cfg,
        graph=graph,
        backend=backend,
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
    backend: Any = default_backend,
    checkpointer: Any | None = None,
    run_events: Any | None = None,
) -> RunResult:
    """Run one task to completion and return where its outputs landed."""
    return _service(
        cfg,
        graph=graph,
        backend=backend,
        checkpointer=checkpointer,
        run_events=run_events,
    ).run(request)


def configured_backend(cfg: Config) -> SessionBackends:
    """The backends `KINGFISHER_BACKEND_FACTORY` names, or `default_backend`.

    For the command line, which builds its own `Kingfisher` and has nowhere else to be
    told. Without it, `kingfisher sessions` and `reap` would list and delete a local
    `sessions/` while a deployment's own backend keeps them somewhere else.
    """
    if cfg.backend_factory is None:
        return default_backend
    return store_named(
        cfg.backend_factory, setting="KINGFISHER_BACKEND_FACTORY", port=SessionBackends
    )

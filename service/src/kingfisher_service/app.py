"""The ASGI application: HTTP mapped onto the methods `Kingfisher` already has.

Transport, and nothing else. This server does not know who is calling and has
no place to put the answer: authentication, mapping callers to sessions, and
per-caller quotas belong to whatever sits in front of it. That line was drawn
before this file existed -- kingfisher has no tenant concept in `Request` or
`Config`, because a tenant field would make it decide who may see what -- and a
server that authenticated would be the thing that line put outside.

This module assembles; the routes live beside it in `sessions.py` and
`turns.py`, and the dependency they share is in `dependencies.py` so that the
assembling only goes one way.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request, status

from kingfisher import Config, Kingfisher, LocalFileStore, async_checkpointer, config_from_env
from kingfisher_service import access, errors, sessions
from kingfisher_service.config import ServiceConfig
from kingfisher_service.identity import GroupsFrom
from kingfisher_service.turns import turn_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _refuse_mismatch(kf: Kingfisher, groups_from: GroupsFrom | None) -> None:
    """Refuse a deployment whose policy and identity do not agree.

    Both directions, and each is worth refusing for its own reason.

    A vocabulary with no source cannot serve one request: the library refuses a
    call that does not say who is calling, so every route answers 500 and the
    deployment is up while serving nothing. One message at startup, where
    somebody is watching, beats that on every axis.

    A source with no vocabulary is the more dangerous one. Nothing fails --
    groups are resolved and then narrow nothing at all -- so a deployment
    somebody wired identity into, and believes is locked down, quietly is not.

    A `RuntimeError` rather than `AccessError`: this is the process being
    assembled wrongly, not a request being refused, and the two should not reach
    the same handler.
    """
    if (kf.access is not None) == (groups_from is not None):
        return
    if groups_from is None:
        msg = (
            "this deployment has an access policy, so every request must say who "
            "is calling: pass groups_from= to create_app -- "
            "`from_header(\'X-Kf-Groups\')` if a gateway states them, or your own "
            "callable. Without one, no route can serve a request at all"
        )
    else:
        msg = (
            "groups_from= was given but this deployment has no access policy, so "
            "the groups it resolves would narrow nothing. Write groups.yaml, or "
            "set KINGFISHER_GROUPS_FILE -- a server wired for identity that "
            "controls nothing is the one that looks locked down and is not"
        )
    raise RuntimeError(msg)


def create_app(
    kingfisher: Kingfisher | None = None,
    config: ServiceConfig | None = None,
    groups_from: GroupsFrom | None = None,
) -> FastAPI:
    """Build the app, optionally around an instance somebody else made.

    Taking one is what makes this testable without inventing anything: tests
    build `Kingfisher(graph=StubAgent(...), threads=StubCheckpointer())` and
    hand it over, which is the substitution point every existing test already
    uses. An app that constructed its own at import time would push its tests
    toward patching `create_deep_agent` instead -- which this repo forbids,
    because three live bugs got through while construction was stubbed out.

    Given nothing, it builds one in the lifespan from the environment, holding
    the async saver open for the process. That saver is not optional: `astream`
    needs async methods and `SqliteSaver` raises on `aget_tuple`, so a sync one
    does not merely block the loop, it refuses.
    """
    settings = config or ServiceConfig.from_env()
    if kingfisher is not None:
        _refuse_mismatch(kingfisher, groups_from)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.kingfisher is not None:
            yield
            return
        cfg: Config = config_from_env()
        files = LocalFileStore(settings.file_store_dir) if settings.file_store_dir else None
        async with async_checkpointer(cfg) as threads:
            built = Kingfisher(cfg, threads=threads, files=files)
            # The same check, at the only other moment it can be made. Given an
            # instance it runs at construction; building one from the
            # environment, there is nothing to check until here -- and here is
            # still before the first request, which is the whole of what
            # "refuses to start" has to mean.
            _refuse_mismatch(built, groups_from)
            app.state.kingfisher = built
            try:
                yield
            finally:
                app.state.kingfisher = None

    app = FastAPI(
        title="kingfisher",
        version="0.1.0",
        summary="Sessions and turns for a general-purpose agent.",
        lifespan=lifespan,
    )
    app.state.kingfisher = kingfisher
    app.state.settings = settings
    app.state.groups_from = groups_from

    @app.middleware("http")
    async def refuse_oversize_bodies(request: Request, call_next):  # noqa: ANN001, ANN202
        """Reject on the header rather than after reading the body.

        `task` is unbounded text, and the point of a limit is not tidiness -- it
        is that measuring a body by reading it is the cost being avoided. A
        chunked request without `Content-Length` is not caught here and is left
        to whatever terminates the connection.
        """
        declared = request.headers.get("content-length")
        if declared is not None and int(declared) > settings.max_body_bytes:
            return errors.problem(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "body_too_large",
                f"the request body is larger than {settings.max_body_bytes} bytes",
                limit=settings.max_body_bytes,
            )
        return await call_next(request)

    access.install(app)
    errors.install(app)
    app.include_router(sessions.router)
    app.include_router(turn_router(settings))
    return app

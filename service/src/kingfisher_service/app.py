"""The ASGI application: HTTP mapped onto the methods `Kingfisher` already has."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, Request, status

from kingfisher import (
    Config,
    Kingfisher,
    LocalFileStore,
    config_from_env,
    file_store_named,
)
from kingfisher_service import access, errors, sessions
from kingfisher_service.config import PREFIX, ServiceConfig
from kingfisher_service.identity import GroupsFrom
from kingfisher_service.turns import turn_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _refuse_mismatch(kf: Kingfisher, groups_from: GroupsFrom | None) -> None:
    """Refuse a deployment whose policy and identity do not agree.

    A vocabulary with no source cannot serve one request: the library refuses a call
    that does not say who is calling, so every route answers 500 and the deployment
    is up while serving nothing. One message at startup, where somebody is watching,
    beats that on every axis.
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


def _file_store(settings: ServiceConfig) -> Any:
    """Where `input_refs` and `data_refs` are fetched from, or nowhere."""
    if settings.file_store_factory is not None:
        return file_store_named(
            settings.file_store_factory, setting=f"{PREFIX}FILE_STORE_FACTORY"
        )
    if settings.file_store_dir is not None:
        return LocalFileStore(settings.file_store_dir)
    return None


def create_app(
    kingfisher: Kingfisher | None = None,
    config: ServiceConfig | None = None,
    groups_from: GroupsFrom | None = None,
) -> FastAPI:
    """Build the app, optionally around an instance somebody else made."""
    settings = config or ServiceConfig.from_env()
    if kingfisher is not None:
        _refuse_mismatch(kingfisher, groups_from)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.kingfisher is not None:
            yield
            return
        cfg: Config = config_from_env()
        built = Kingfisher(cfg, files=_file_store(settings))
        # The same check, at the only other moment it can be made. Given an
        # instance it runs at construction; building one from the environment,
        # there is nothing to check until here -- and here is still before the
        # first request, which is the whole of what "refuses to start" has to
        # mean.
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
        """Reject on the header rather than after reading the body."""
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

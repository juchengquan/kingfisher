"""The ASGI application: HTTP mapped onto the methods `Kingfisher` already has.

Transport, and nothing else. This server does not know who is calling and has
no place to put the answer: authentication, mapping callers to sessions, and
per-caller quotas belong to whatever sits in front of it. That line was drawn
before this file existed -- kingfisher has no tenant concept in `Request` or
`Config`, because a tenant field would make it decide who may see what -- and
a server that authenticated would be the thing that line put outside.

What that leaves is a small surface over five methods. The session half is
here; turns follow.

Sessions live in the path rather than the body. A service in front has to
authorise "may this caller touch session X", and a gateway that must parse a
JSON body to make an access decision is a gateway that gets rewritten.

There is no collection endpoint. A session id is a bearer credential -- holding
one is how a caller proves the session is theirs -- so listing them hands out
every credential on the box. Whatever knows whose sessions are whose calls
`Kingfisher.sessions()` in-process, which is who that method was added for.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status

from kingfisher import Config, Kingfisher, async_checkpointer, from_env
from kingfisher.server.config import ServerConfig
from kingfisher.server.payloads import session_payload

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def kingfisher_of(request: Request) -> Kingfisher:
    """The instance this app serves. One per process, per T4.

    The cost measured there is the process, not the instance -- resolving
    deepagents is 1310ms and 115MB, a further instance is 1.1ms and 0.16MB --
    so process count follows concurrency rather than tenancy. With identity
    outside, there is nothing here to key a registry on anyway.
    """
    return request.app.state.kingfisher


def create_app(
    kingfisher: Kingfisher | None = None,
    config: ServerConfig | None = None,
) -> FastAPI:
    """Build the app, optionally around an instance somebody else made.

    Taking one is what makes this testable without inventing anything: tests
    build `Kingfisher(agent=StubAgent(...), threads=StubCheckpointer())` and
    hand it over, which is the substitution point every existing test already
    uses. An app that constructed its own at import time would push its tests
    toward patching `create_deep_agent` instead -- which this repo forbids,
    because three live bugs got through while construction was stubbed out.

    Given nothing, it builds one in the lifespan from the environment, holding
    the async saver open for the process. That saver is not optional: `astream`
    needs async methods and `SqliteSaver` raises on `aget_tuple`, so a sync one
    does not merely block the loop, it refuses.
    """
    settings = config or ServerConfig.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.kingfisher is not None:
            yield
            return
        cfg: Config = from_env()
        async with async_checkpointer(cfg) as threads:
            app.state.kingfisher = Kingfisher(cfg, threads=threads)
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

    @app.middleware("http")
    async def refuse_oversize_bodies(request: Request, call_next):  # noqa: ANN001, ANN202
        """Reject on the header rather than after reading the body.

        `task` is unbounded text, and the point of a limit is not to be tidy
        about it -- it is that measuring a body by reading it is the cost being
        avoided. A chunked request without `Content-Length` is not caught here
        and is left to whatever terminates the connection.
        """
        declared = request.headers.get("content-length")
        if declared is not None and int(declared) > settings.max_body_bytes:
            return Response(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                content=f'{{"error":"body_too_large","limit":{settings.max_body_bytes}}}',
                media_type="application/json",
            )
        return await call_next(request)

    # Sync handlers on purpose. Each does filesystem work -- `session` is a
    # directory listing, measured at 0.24ms for fifty sessions and 22ms for
    # five thousand -- and fastapi runs a `def` endpoint on a worker thread
    # while an `async def` one would hold the loop for exactly that long. It is
    # the same reason `astream` puts `_prepare` behind `asyncio.to_thread`.

    @app.post("/sessions", status_code=status.HTTP_201_CREATED)
    def open_session(kf: Kingfisher = Depends(kingfisher_of)) -> dict[str, str]:  # noqa: B008
        """Start a session and return its id.

        The only way a session comes into existence with a name someone chose.
        A *request* may not create one -- its id may have come from whoever is
        calling the service, and an id that could create would let that caller
        choose, or guess, somebody else's. Hence no `session_id` in this body.
        """
        return {"session_id": kf.start_session()}

    @app.get("/sessions/{session_id}")
    def read_session(
        session_id: str,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    ) -> dict[str, object]:
        """Whether this session still exists, and when it was last used.

        Safe to expose to a holder of the id, because it tells them nothing
        they could not learn by using it. Enumeration is the part that leaks,
        and there is no endpoint for that.

        Asking does not disturb the session: no claim is taken and the idle
        clock retention reads is not refreshed.
        """
        info = kf.session(session_id)
        if info is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id!r}")
        return session_payload(info)

    @app.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
    def close_session(
        session_id: str,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    ) -> Response:
        """Dispose of a session, its files, its thread and its claim.

        Existence is checked first because `delete_session` answers `None` both
        for "there was no such session" and for "removed it" -- one of them is
        a 404 and the other a 204, and the library's return cannot tell them
        apart. A failure string means the removal was attempted and did not
        finish, which is ours rather than the caller's.
        """
        if kf.session(session_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id!r}")
        failure = kf.delete_session(session_id)
        if failure:
            raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, failure)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app

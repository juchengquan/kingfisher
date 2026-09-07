"""One line per request, with the session id left out of it."""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request, Response

#: Named for the package rather than `__name__`, so a deployment configures one
#: logger and gets everything the server says.
logger = logging.getLogger("kingfisher_service")


def route_of(request: Request) -> str:
    """The matched route's template, or a placeholder if nothing matched.

    `scope["route"]` is set during routing, so this is only meaningful after the
    response exists. A request that matched nothing has no template and must not fall
    back to the real path -- that is precisely the 404-probing case where a caller
    controls what gets written.
    """
    route = request.scope.get("route")
    template = getattr(route, "path", None)
    return template if isinstance(template, str) else "<unmatched>"


def install(app: FastAPI) -> None:
    """Log method, route, status and duration, once per request."""

    @app.middleware("http")
    async def log_one_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = perf_counter()
        response = await call_next(request)
        logger.info(
            "%s %s %s %.1fms",
            request.method,
            route_of(request),
            response.status_code,
            (perf_counter() - started) * 1000,
        )
        return response

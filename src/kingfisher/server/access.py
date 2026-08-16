"""One line per request, with the session id left out of it.

That omission is the whole design here. A session id is a bearer credential --
`UnknownSessionError` says so outright: holding one is how a caller proves the
session is theirs. It is also in the path of four of the five routes, so an
ordinary access log writes credentials to disk, ships them to whatever collects
logs, and leaves them in whatever that retains. A log is the one place a
credential leaks quietly and stays leaked.

So what is logged is the *route template* -- `/sessions/{session_id}/turns` --
which fastapi has already matched by the time a response exists. It says which
endpoint was called, which is what an access log is for, and says nothing about
whose session it was.

Anything that does need to tell requests apart should correlate on something the
caller can be given and can rotate. There is no such field yet; when there is,
it belongs here rather than the id being un-redacted.
"""

from __future__ import annotations

import logging
from time import perf_counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from fastapi import FastAPI, Request, Response

#: Named for the package rather than `__name__`, so a deployment configures one
#: logger and gets everything the server says.
logger = logging.getLogger("kingfisher.server")


def route_of(request: Request) -> str:
    """The matched route's template, or a placeholder if nothing matched.

    `scope["route"]` is set during routing, so this is only meaningful after the
    response exists. A request that matched nothing has no template and must not
    fall back to the real path -- that is precisely the 404-probing case where a
    caller controls what gets written.

    `<unmatched>` also covers a request that matched a route fastapi did not
    add: only its own `APIRoute` records itself in the scope, so `/openapi.json`
    and `/docs` are logged this way despite answering 200. Imprecise and
    deliberately not fixed -- the alternative is falling back to the path for
    *some* requests, and the rule is worth more when it has no exceptions.
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

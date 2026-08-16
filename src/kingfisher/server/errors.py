"""Which refusal is which, and what every refusal looks like.

Two rules, and they are separate.

**Which status.** By exact type, never by base class. Ten of the package's
eleven error types are `ValueError`, and so is `Request`'s empty-task check, and
so is whatever a dependency raises -- so `except ValueError` would turn a bug
into a refusal and a refusal into whichever status happened to be listed first.
Errors not named below are not caller-facing: they say the deployment is wrong
rather than the caller, and reaching one is a 500 on purpose.

**What shape.** One, everywhere. There were four: this module's, fastapi's
`{"detail": ...}` from `HTTPException`, fastapi's list-of-objects from request
validation, and a hand-written string in the body-size middleware. A client that
must recognise four shapes to find out what went wrong will parse one of them
and break on the rest, so every refusal now leaves through `problem` -- and the
routes raise rather than build responses, which is what leaves only one place
that knows the shape.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING

from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from kingfisher import (
    CapabilityError,
    QuotaExceededError,
    SessionBusyError,
    SkillError,
    SubagentError,
    UnknownReferenceError,
    UnknownSessionError,
    UnsafeReferenceError,
    UploadError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Request

#: Exception type -> (status, machine-readable code).
#:
#: The code is what a client should branch on. It comes from this same table so
#: it cannot drift from the status, and messages stay for humans -- a client
#: that parses one is a client that breaks when the wording improves.
STATUS: dict[type[Exception], tuple[int, str]] = {
    UnknownSessionError: (HTTPStatus.NOT_FOUND, "unknown_session"),
    SessionBusyError: (HTTPStatus.CONFLICT, "session_busy"),
    # 429 rather than 507. The meaning is gRPC's `RESOURCE_EXHAUSTED`, which
    # explicitly covers a disk quota, and 507 says the right thing while being
    # 5xx -- which makes generic clients retry something retrying cannot fix.
    # The code above is how a client tells this from a rate limit.
    QuotaExceededError: (HTTPStatus.TOO_MANY_REQUESTS, "quota_exceeded"),
    CapabilityError: (HTTPStatus.FORBIDDEN, "not_granted"),
    UploadError: (HTTPStatus.BAD_REQUEST, "bad_reference"),
    # 400 rather than 404: the session is the resource here, and it exists. What
    # is missing is something the request body named, which is a bad body.
    UnknownReferenceError: (HTTPStatus.BAD_REQUEST, "unknown_reference"),
    # Distinct from `bad_reference`, which is a name that did not resolve. This
    # one resolved somewhere it was not allowed to go, and a caller seeing it
    # has sent something that looks like an attempt rather than a typo.
    UnsafeReferenceError: (HTTPStatus.BAD_REQUEST, "unsafe_reference"),
    SkillError: (HTTPStatus.BAD_REQUEST, "bad_skill"),
    SubagentError: (HTTPStatus.BAD_REQUEST, "bad_subagent"),
}

#: Codes for refusals that are not a kingfisher error -- fastapi's own, and the
#: body-size limit. A status alone is not enough for a client to branch on:
#: `unknown_session` and a mistyped URL are both 404 and need different fixes.
CODE_FOR_STATUS: dict[int, str] = {
    HTTPStatus.NOT_FOUND: "not_found",
    HTTPStatus.METHOD_NOT_ALLOWED: "method_not_allowed",
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: "body_too_large",
    HTTPStatus.UNPROCESSABLE_ENTITY: "invalid_request",
}


def problem(status: int, code: str, message: str, **extra: object) -> JSONResponse:
    """The one shape a refusal takes.

    `error` is the contract; `message` is prose that may be reworded without
    warning. `extra` carries whatever only one refusal has -- the limit that was
    exceeded, the fields that failed validation -- rather than making every
    refusal carry a field that is usually null.
    """
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


def install(app: FastAPI) -> None:
    """Register the handlers that give every refusal that shape.

    Routes then *raise* -- `UnknownSessionError` where there is no such session,
    the library's own errors from a turn -- and nothing but this module builds a
    body. A route that returned its own response would be a second shape by
    definition, which is the thing being removed.
    """

    async def from_kingfisher(_: Request, error: Exception) -> JSONResponse:
        status, code = STATUS[type(error)]
        return problem(status, code, str(error))

    for error_type in STATUS:
        app.add_exception_handler(error_type, from_kingfisher)

    async def from_http(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, HTTPException)  # noqa: S101 -- registered for this type
        code = CODE_FOR_STATUS.get(error.status_code, "error")
        return problem(error.status_code, code, str(error.detail))

    app.add_exception_handler(HTTPException, from_http)

    async def from_validation(_: Request, error: Exception) -> JSONResponse:
        assert isinstance(error, RequestValidationError)  # noqa: S101
        # The per-field detail is kept, under its own key rather than as the
        # whole body: it is genuinely useful for a malformed request and
        # genuinely noise for every other refusal.
        return problem(
            HTTPStatus.UNPROCESSABLE_ENTITY,
            "invalid_request",
            "the request body is not valid",
            detail=error.errors(),
        )

    app.add_exception_handler(RequestValidationError, from_validation)

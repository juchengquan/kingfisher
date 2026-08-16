"""Which refusal is which, on the wire.

By exact type, never by base class. Ten of the package's eleven error types are
`ValueError`, and so is `Request`'s empty-task check, and so is whatever a
dependency raises -- so `except ValueError` would turn a bug into a refusal and
a refusal into whichever status happened to be first.

Errors not named here are not caller-facing: they say the deployment is wrong
rather than the caller, and reaching one is a 500 on purpose.
"""

from __future__ import annotations

from http import HTTPStatus

from kingfisher import (
    CapabilityError,
    QuotaExceededError,
    SessionBusyError,
    SkillError,
    SubagentError,
    UnknownSessionError,
    UploadError,
)

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
    SkillError: (HTTPStatus.BAD_REQUEST, "bad_skill"),
    SubagentError: (HTTPStatus.BAD_REQUEST, "bad_subagent"),
}


def refusal(error: Exception) -> tuple[int, dict[str, str]] | None:
    """The status and body for a refusal, or `None` if this is not one.

    `None` rather than a default 500 pair, so the caller decides what an
    unrecognised exception means. Here that is: let it out, and let the
    framework log it as the bug it is.
    """
    found = STATUS.get(type(error))
    if found is None:
        return None
    status, code = found
    return status, {"error": code, "message": str(error)}

"""Opening, checking and disposing of a session.

Session id in the path rather than the body, because a service in front has to
authorise "may this caller touch session X" -- and a gateway that must parse a
JSON body to make an access decision is a gateway that gets rewritten.

There is no collection endpoint. A session id is a bearer credential: holding
one is how a caller proves the session is theirs, and they hold one by having
been given it. Listing them hands out every credential on the box, and a limit
would only mean handing out a hundred instead of five thousand. Whatever knows
whose sessions are whose calls `Kingfisher.sessions()` in-process, which is who
that method was added for.

The handlers are `def` rather than `async def` on purpose. Each does filesystem
work -- `session` is a directory listing, 0.24ms at fifty sessions and 22ms at
five thousand -- and fastapi runs a sync endpoint on a worker thread, where an
`async def` one would hold the loop for exactly that long. Same reason `astream`
puts `_prepare` behind `asyncio.to_thread`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status

# Imported for real, not under `TYPE_CHECKING`: fastapi resolves a handler`s
# annotations at runtime, and an unresolvable one is read as a body field --
# which turns every request into a 422 asking for a "kf" object.
from kingfisher import Kingfisher
from kingfisher.server.dependencies import kingfisher_of
from kingfisher.server.payloads import session_payload

router = APIRouter(tags=["sessions"])


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def open_session(kf: Kingfisher = Depends(kingfisher_of)) -> dict[str, str]:  # noqa: B008
    """Start a session and return its id.

    The only way a session comes into existence with a name someone chose. A
    *request* may not create one -- its id may have come from whoever is calling
    the service, and an id that could create would let that caller choose, or
    guess, somebody else's. Hence no `session_id` in this body.
    """
    return {"session_id": kf.start_session()}


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
) -> dict[str, object]:
    """Whether this session still exists, and when it was last used.

    Safe to give a holder of the id, because it tells them nothing they could
    not learn by using it. Enumeration is the part that leaks, and there is no
    endpoint for that.

    Asking does not disturb the session: no claim is taken, and the idle clock
    retention reads is not refreshed -- otherwise a service checking an id would
    keep sessions alive by asking about them.
    """
    info = kf.session(session_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id!r}")
    return session_payload(info)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    session_id: str,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
) -> Response:
    """Dispose of a session, its files, its thread and its claim.

    Existence is checked first because `delete_session` answers `None` both for
    "there was no such session" and for "removed it" -- one is a 404 and the
    other a 204, and the library's return cannot tell them apart. A failure
    string means removal was attempted and did not finish, which is ours rather
    than the caller's.
    """
    if kf.session(session_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no session {session_id!r}")
    failure = kf.delete_session(session_id)
    if failure:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, failure)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

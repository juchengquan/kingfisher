"""Running a turn, streamed.

Two endpoints for one thing. `POST /sessions/{id}/turns` continues a
conversation; `POST /turns` asks one question and mints a session for it,
because omitting the session is something the library can do and the path form
cannot express -- and a stateless caller asking one question should not need
two round trips to preserve URL symmetry.

Both are `async def`, unlike the session routes: this is the loop's to hold,
and the blocking part -- `_prepare`, 15-46ms of filesystem work and agent
construction -- is already behind `asyncio.to_thread` inside `astream`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

# Imported for real, not under `TYPE_CHECKING`: fastapi resolves a handler`s
# annotations at runtime, and an unresolvable one is read as a body field.
from kingfisher import Kingfisher
from kingfisher import Request as TurnRequest
from kingfisher.server import streaming
from kingfisher.server.dependencies import kingfisher_of
from kingfisher.server.errors import refusal

if TYPE_CHECKING:
    from kingfisher.server.config import ServerConfig


class TurnBody(BaseModel):
    """What a caller sends to start a turn.

    `task` and nothing else, for now. Capabilities and file references follow;
    each is a decision with a shape of its own and neither is guessed at here.

    No `turn_id`. The library takes one and reuses that directory, but then runs
    the turn again in full -- so over HTTP a field of that name would read as an
    idempotency key while quietly doubling both the conversation and the bill.
    The id the work actually got comes back on `finished`, which is where
    correlation belongs: the id it has, not the one you hoped for.
    """

    #: No length rule here. `Request.__post_init__` already refuses an empty or
    #: whitespace-only task, and a `min_length` beside it is a second copy of
    #: that rule -- the kind that drifts, because this model is what people edit
    #: when adding a field. The refusal becomes a 422 in `turn_for`.
    task: str


def turn_for(task: str, session_id: str | None = None) -> TurnRequest:
    """Build the library's request, letting the library say what is valid.

    The narrow catch is the point. `Request` refuses an empty or whitespace-only
    task and that rule lives there; re-stating it in the model above would be
    two homes for one sentence. Catching `ValueError` around *one constructor
    whose only documented refusal is that rule* is not the same act as catching
    `ValueError` around a turn, which is what `errors.STATUS` exists to avoid --
    there it would swallow bugs.
    """
    try:
        return TurnRequest(task, session_id=session_id)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


async def stream_turn(
    kf: Kingfisher, request: TurnRequest, settings: ServerConfig
) -> Response:
    """Open the stream, having first checked there is one to open.

    The first event is pulled here, outside the response, because `astream` runs
    `_prepare` before yielding anything -- so a refusal is still a status code
    at this moment and stops being one immediately after. Handing the generator
    to `StreamingResponse` unopened would put 200 on the wire and bury every
    refusal in the body.

    An unrecognised exception is re-raised rather than becoming a 500 here. It
    is a bug, and it should be logged as one by the thing whose job that is.
    """
    events = kf.astream(request)
    try:
        first = await streaming.opening(events)
    except Exception as error:
        await streaming.close(events)
        answer = refusal(error)
        if answer is None:
            raise
        code, payload = answer
        return JSONResponse(payload, status_code=code)

    return StreamingResponse(
        streaming.body(events, first, heartbeat_s=settings.heartbeat_s),
        media_type="text/event-stream",
        headers={
            # A proxy that buffers would hold every token until the turn ended,
            # which is the whole thing this endpoint exists to avoid.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def turn_router(settings: ServerConfig) -> APIRouter:
    """The turn routes, closed over how often a quiet stream should ping."""
    router = APIRouter(tags=["turns"])

    @router.post("/sessions/{session_id}/turns")
    async def run_turn(
        session_id: str,
        body: TurnBody,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    ) -> Response:
        """Run one turn in an existing session, streaming as it goes.

        An unknown session is a 404 rather than a new session. A supplied id may
        resume but never create; that is what makes the id a credential instead
        of a name anyone can pick.
        """
        return await stream_turn(kf, turn_for(body.task, session_id), settings)

    @router.post("/turns")
    async def run_one_shot(
        body: TurnBody,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    ) -> Response:
        """Ask one question without having opened a session first.

        A session is still created -- a turn needs somewhere to live -- and its
        id comes back on `finished`, so a caller who decides to continue can.
        """
        return await stream_turn(kf, turn_for(body.task), settings)

    return router

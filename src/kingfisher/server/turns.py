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

from time import perf_counter
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

# Imported for real, not under `TYPE_CHECKING`: fastapi resolves a handler`s
# annotations at runtime, and an unresolvable one is read as a body field.
from kingfisher import Kingfisher
from kingfisher import Request as TurnRequest
from kingfisher.server import audit, streaming
from kingfisher.server.capabilities import CapabilitiesBody
from kingfisher.server.dependencies import kingfisher_of
from kingfisher.server.errors import outcome

if TYPE_CHECKING:
    from kingfisher.server.config import ServerConfig


class TurnBody(BaseModel):
    """What a caller sends to start a turn.

    A task, what the request asks to be allowed, and the files it brings.

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
    #: What this request asks to be allowed. Absent means the deployment's
    #: defaults, which is not the same as `{}` -- an empty object is a request
    #: that named no axis and gets the same defaults, while naming an axis as
    #: `null` asks for nothing on it.
    #:
    #: Only ever narrows. `Kingfisher` clamps with
    #: `grants.intersect(request.capabilities)`, so this states intent and the
    #: deployment decides what intent is honoured.
    capabilities: CapabilitiesBody | None = None
    #: Files, by id, resolved by whatever `FileStore` the deployment wired.
    #:
    #: Ids rather than bytes, so kingfisher never receives a payload over its
    #: own wire -- the same decision `skill_refs` made one phase earlier, for
    #: the same reason. `inputs` and `data` stay host paths for CLI and library
    #: callers; these are the remote form of the same two.
    #:
    #: The distinction is lifetime and nothing else: `data_refs` land in the
    #: session's `/data` and are there next turn, `input_refs` land in this
    #: turn's `input/` and leave with it.
    input_refs: list[str] = []
    data_refs: list[str] = []


def turn_for(body: TurnBody, session_id: str | None = None) -> TurnRequest:
    """Build the library's request, letting the library say what is valid.

    The narrow catch is the point. `Request` refuses an empty or whitespace-only
    task and that rule lives there; re-stating it in the model above would be
    two homes for one sentence. Catching `ValueError` around *one constructor
    whose only documented refusal is that rule* is not the same act as catching
    `ValueError` around a turn, which is what `errors.STATUS` exists to avoid --
    there it would swallow bugs.
    """
    # An absent capabilities object and one that names no axis are the same
    # request: both come out of `selected` as `Capabilities()`, which is what
    # `Request` defaults to anyway. One path rather than two.
    asked = body.capabilities if body.capabilities is not None else CapabilitiesBody()
    try:
        return TurnRequest(
            body.task,
            session_id=session_id,
            capabilities=asked.selected(),
            input_refs=tuple(body.input_refs),
            data_refs=tuple(body.data_refs),
        )
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error


async def stream_turn(
    kf: Kingfisher, body: TurnBody, session_id: str | None, settings: ServerConfig
) -> Response:
    """Open the stream, having first checked there is one to open.

    The first event is pulled here, outside the response, because `astream` runs
    `_prepare` before yielding anything -- so a refusal is still a status code
    at this moment and stops being one immediately after. Handing the generator
    to `StreamingResponse` unopened would put 200 on the wire and bury every
    refusal in the body.

    Nothing is caught by type here and no body is built. The handlers in
    `errors` turn a refusal into a status and anything else into a 500, which is
    what leaves one table deciding which is which and one function deciding what
    a refusal looks like.
    """
    attempt = audit.Attempt(
        session_id=session_id,
        task=body.task,
        started=perf_counter(),
        settings=settings,
    )
    # `turn_for` is inside the try, not before it. It refuses an empty task, and
    # that refusal names a session in the path -- so leaving it outside made a
    # 422 against a real session the one refusal nothing recorded.
    #
    # A body fastapi rejects outright is still invisible here: the endpoint does
    # not run, so there is nothing to audit and the access log is where that
    # request appears.
    events = None
    try:
        events = kf.astream(turn_for(body, session_id))
        first = await streaming.opening(events)
    except BaseException as error:
        # Let go, then let it out. The close is not what gives the claim back --
        # `_admit` already released it on the way out -- it is here for the
        # exception that does not come from the generator body, cancellation
        # being the one that matters, where the run is left suspended rather
        # than terminated.
        if events is not None:
            await streaming.close(events)
        # The one thing no other log sees: `JsonlRunLogger` is built inside a
        # turn, so a request refused before one exists writes nothing anywhere.
        status, code = outcome(error)
        audit.refused(attempt, error, status=status, code=code)
        raise

    watched = audit.watching(events, first, attempt)
    return StreamingResponse(
        streaming.body(watched, first, heartbeat_s=settings.heartbeat_s),
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
        return await stream_turn(kf, body, session_id, settings)

    @router.post("/turns")
    async def run_one_shot(
        body: TurnBody,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    ) -> Response:
        """Ask one question without having opened a session first.

        A session is still created -- a turn needs somewhere to live -- and its
        id comes back on `finished`, so a caller who decides to continue can.
        """
        return await stream_turn(kf, body, None, settings)

    return router

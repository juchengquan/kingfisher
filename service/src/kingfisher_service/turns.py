"""Running a turn, streamed.

Both are `async def`, unlike the session routes: this is the loop's to hold, and the
blocking part -- `_prepare`, 15-46ms of filesystem work and agent construction -- is
already behind `asyncio.to_thread` inside `astream`.
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
from kingfisher_service import audit, streaming
from kingfisher_service.capabilities import CapabilitiesBody
from kingfisher_service.dependencies import groups_of, kingfisher_of
from kingfisher_service.errors import outcome

if TYPE_CHECKING:
    from kingfisher_service.config import ServiceConfig


class TurnBody(BaseModel):
    """What a caller sends to start a turn."""

    #: No length rule here. `Request.__post_init__` already refuses an empty or
    #: whitespace-only task, and a `min_length` beside it is a second copy of
    #: that rule -- the kind that drifts, because this model is what people edit
    #: when adding a field. The refusal becomes a 422 in `turn_for`.
    task: str
    #: What this request asks to be allowed. Absent means the deployment's defaults,
    #: which is not the same as `{}` -- an empty object is a request that named no axis
    #: and gets the same defaults, while naming an axis as `null` asks for nothing on
    #: it.
    capabilities: CapabilitiesBody | None = None
    #: Files, by id, resolved by whatever `FileStore` the deployment wired.
    input_refs: list[str] = []
    data_refs: list[str] = []


def turn_for(body: TurnBody, session_id: str | None = None) -> TurnRequest:
    """Build the library's request, letting the library say what is valid."""
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
    kf: Kingfisher,
    body: TurnBody,
    session_id: str | None,
    settings: ServiceConfig,
    groups: tuple[str, ...] | None = None,
) -> Response:
    """Open the stream, having first checked there is one to open.

    The first event is pulled here, outside the response, because `astream` runs
    `_prepare` before yielding anything -- so a refusal is still a status code at
    this moment and stops being one immediately after. Handing the generator to
    `StreamingResponse` unopened would put 200 on the wire and bury every refusal in
    the body.
    """
    attempt = audit.Attempt(
        session_id=session_id,
        task=body.task,
        started=perf_counter(),
        settings=settings,
        groups=groups,
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
        events = kf.astream(turn_for(body, session_id), groups=groups)
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


def turn_router(settings: ServiceConfig) -> APIRouter:
    """The turn routes, closed over how often a quiet stream should ping."""
    router = APIRouter(tags=["turns"])

    @router.post("/sessions/{session_id}/turns")
    async def run_turn(
        session_id: str,
        body: TurnBody,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
        groups: tuple[str, ...] | None = Depends(groups_of),
    ) -> Response:
        """Run one turn in an existing session, streaming as it goes.

        An unknown session is a 404 rather than a new session. A supplied id may
        resume but never create; that is what makes the id a credential instead of a
        name anyone can pick.
        """
        return await stream_turn(kf, body, session_id, settings, groups)

    @router.post("/turns")
    async def run_one_shot(
        body: TurnBody,
        kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
        groups: tuple[str, ...] | None = Depends(groups_of),
    ) -> Response:
        """Ask one question without having opened a session first."""
        return await stream_turn(kf, body, None, settings, groups)

    return router

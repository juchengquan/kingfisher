"""Opening, checking and disposing of a session.

The handlers are `def` rather than `async def` on purpose. Each does filesystem work
-- `session` is a directory listing, 0.24ms at fifty sessions and 22ms at five
thousand -- and fastapi runs a sync endpoint on a worker thread, where an `async def`
one would hold the loop for exactly that long. Same reason `astream` puts `_prepare`
behind `asyncio.to_thread`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict

# Imported for real, not under `TYPE_CHECKING`: fastapi resolves a handler`s
# annotations at runtime, and an unresolvable one is read as a body field --
# which turns every request into a 422 asking for a "kf" object.
from kingfisher import Kingfisher, UnknownSessionError
from kingfisher_service.dependencies import groups_of, kingfisher_of
from kingfisher_service.payloads import session_payload

router = APIRouter(tags=["sessions"])


class OpenBody(BaseModel):
    """What opening a session takes: the agent it will run."""

    model_config = ConfigDict(extra="forbid")

    agent: str


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def open_session(
    body: OpenBody,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    groups: tuple[str, ...] | None = Depends(groups_of),
) -> dict[str, object]:
    """Start a session on one agent and say what that resolved to."""
    spec = kf.agent_named(body.agent, groups=groups)
    session_id = kf.start_session()
    # Fixed to the session at the same moment it is reported, so the two cannot
    # disagree: what this says is what every turn will be built from.
    kf.remember_agent(session_id, body.agent)
    held = kf.held_for(groups)
    mine = spec.declares(held)
    return {
        "session_id": session_id,
        **({} if groups is None else {"groups": list(groups)}),
        "agent": {
            "name": spec.name,
            "description": spec.description,
            "skills": _named(mine.skills),
            "subagents": _named(mine.subagents),
        },
    }


def _named(selection: object) -> object:
    """A selection as JSON says it: a list, `"*"`, or `null`."""
    return list(selection) if isinstance(selection, tuple) else selection


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    groups: tuple[str, ...] | None = Depends(groups_of),
) -> dict[str, object]:
    """Whether this session still exists, and when it was last used."""
    info = kf.session(session_id, groups=groups)
    if info is None:
        # The library's own error rather than a 404 built here. It is what a
        # turn on a missing session raises, so both paths answer identically --
        # and the status and the code come from the one table either way.
        missing = f"no session {session_id!r}"
        raise UnknownSessionError(missing)
    return session_payload(info)


@router.delete("/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
def close_session(
    session_id: str,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    groups: tuple[str, ...] | None = Depends(groups_of),
) -> Response:
    """Dispose of a session, its files, its thread and its claim.

    Asked for this caller, like reading one: a session they cannot run is a session
    they cannot destroy, and it answers 404 rather than 403 so that a leaked id is
    worth nothing at all rather than worth a confirmation.
    """
    if kf.session(session_id, groups=groups) is None:
        missing = f"no session {session_id!r}"
        raise UnknownSessionError(missing)
    failure = kf.delete_session(session_id)
    if failure:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, failure)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

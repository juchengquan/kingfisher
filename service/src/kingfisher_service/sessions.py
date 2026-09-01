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
from pydantic import BaseModel, ConfigDict

# Imported for real, not under `TYPE_CHECKING`: fastapi resolves a handler`s
# annotations at runtime, and an unresolvable one is read as a body field --
# which turns every request into a 422 asking for a "kf" object.
from kingfisher import Kingfisher, UnknownSessionError
from kingfisher_service.dependencies import groups_of, kingfisher_of
from kingfisher_service.payloads import session_payload

router = APIRouter(tags=["sessions"])


class OpenBody(BaseModel):
    """What opening a session takes: the agent it will run.

    Required, and here rather than on a turn because this is where the choice is
    actually made. A session runs one agent for its whole life, so the mistake
    happens once, at the point that decides it, instead of being re-checked on
    every turn afterwards.

    Still no `session_id`. An id names a conversation and the files beside it,
    so it is a bearer credential -- and one a caller could choose would let them
    name, or guess, somebody else's.
    """

    model_config = ConfigDict(extra="forbid")

    agent: str


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def open_session(
    body: OpenBody,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    groups: tuple[str, ...] | None = Depends(groups_of),
) -> dict[str, object]:
    """Start a session on one agent and say what that resolved to.

    The response carries the resolution rather than only the id, because it is
    the one moment a caller can be told what they got without running a turn:
    the agent is resolved and pinned right here. What it names -- its model, its
    delegates -- is decided now and cannot change for the life of the session.

    **Reported for this caller, not for the definition.** `declares` is asked
    with their groups, so what comes back is what their turns will actually
    have. Reported raw it would name delegates they cannot reach -- the
    enumeration filtering closes everywhere else -- and promise capabilities
    the very next request would not honour.

    The groups come back too. A caller behind a gateway usually cannot see what
    identity was asserted on their behalf, and this is the one place to find
    out. The names they were resolved as, not the `contains` expansion: what
    they hold is theirs to know, and how this deployment's vocabulary nests is
    not.
    """
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
    """A selection as JSON says it: a list, `"*"`, or `null`.

    The wire spelling rather than the library's, for the reason the capabilities
    model gives: a JSON caller writes `"*"` because there is nothing else to
    write.
    """
    return list(selection) if isinstance(selection, tuple) else selection


@router.get("/sessions/{session_id}")
def read_session(
    session_id: str,
    kf: Kingfisher = Depends(kingfisher_of),  # noqa: B008
    groups: tuple[str, ...] | None = Depends(groups_of),
) -> dict[str, object]:
    """Whether this session still exists, and when it was last used.

    Safe to give a holder of the id who can *use* it, because it tells them
    nothing they could not learn by doing so. Where a policy narrows who may
    run which agent those two stop being the same person, so the answer is
    asked for this caller: a session whose pinned agent they cannot reach reads
    as one that is not there, which is what `session(groups=)` decides and why
    the rule is there rather than here. Enumeration is the part that leaks, and
    there is no endpoint for that.

    Asking does not disturb the session: no claim is taken, and the idle clock
    retention reads is not refreshed -- otherwise a service checking an id would
    keep sessions alive by asking about them.
    """
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

    Asked for this caller, like reading one: a session they cannot run is a
    session they cannot destroy, and it answers 404 rather than 403 so that a
    leaked id is worth nothing at all rather than worth a confirmation.

    Existence is checked first because `delete_session` answers `None` both for
    "there was no such session" and for "removed it" -- one is a 404 and the
    other a 204, and the library's return cannot tell them apart. A failure
    string means removal was attempted and did not finish, which is ours rather
    than the caller's.
    """
    if kf.session(session_id, groups=groups) is None:
        missing = f"no session {session_id!r}"
        raise UnknownSessionError(missing)
    failure = kf.delete_session(session_id)
    if failure:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, failure)
    return Response(status_code=status.HTTP_204_NO_CONTENT)

"""What crosses the wire, written once and by hand."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingfisher import RunEvent, RunResult, SessionInfo


def session_payload(info: SessionInfo) -> dict[str, object]:
    """One session, as something outside is told about it."""
    return {"session_id": info.id, "last_used": info.last_used}


def result_payload(result: RunResult) -> dict[str, object]:
    """A finished turn, as the caller is told about it."""
    return {
        "session_id": result.session_id,
        "turn_id": result.turn_id,
        "answer": result.answer,
        "virtual_dir": result.virtual_dir,
        "artifacts": list(result.artifacts),
        "stop_reason": result.stop_reason,
    }


def event_payload(event: RunEvent) -> dict[str, object]:
    """One step of a run, without its kind."""
    body: dict[str, object] = {}
    if event.text:
        body["text"] = event.text
    if event.tool:
        body["tool"] = event.tool
    if event.tools:
        body["tools"] = list(event.tools)
    if event.args:
        body["args"] = [dict(one) for one in event.args]
    if event.usage:
        body["usage"] = dict(event.usage)
    if event.channel != "answer":
        body["channel"] = event.channel
    if event.agent is not None:
        # Which delegate produced this, or absent for the agent the caller
        # asked. Without it a delegate's prose and the caller's arrive on one
        # channel and the type cannot tell them apart -- both are chunks.
        body["agent"] = event.agent
    if event.result is not None:
        body["result"] = result_payload(event.result)
    return body


def frame(event: RunEvent) -> str:
    """One SSE frame: the kind as the event name, the rest as JSON."""
    return f"event: {event.kind}\ndata: {json.dumps(event_payload(event))}\n\n"

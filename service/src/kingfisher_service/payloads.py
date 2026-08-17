"""What crosses the wire, written once and by hand.

Deliberately not pydantic mirrors of the dataclasses. `RunResult` keeps
`run_dir` and `log_path` as `Path` precisely so `json.dumps` raises on them --
there is a test asserting they refuse to serialise -- and a mirrored model is a
second home for that rule. It is the kind of second home that gets it wrong
helpfully: adding a `Path` serialiser makes the error go away and ships exactly
the leak the original refuses.

So the rule lives in one function per type, here, and fastapi is used for
validating what comes *in* rather than for describing what goes out.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kingfisher import RunEvent, RunResult, SessionInfo


def session_payload(info: SessionInfo) -> dict[str, object]:
    """One session, as something outside is told about it.

    Two fields, and the absence of a third is the point: no directory. A
    caller handed one would start reading files out of it, and the layout would
    become a contract nobody wrote down. `SessionInfo` has no such field, so
    this is a rename rather than a filter -- and `id` becomes `session_id`
    because on the wire it is the only id there is a name for.
    """
    return {"session_id": info.id, "last_used": info.last_used}


def result_payload(result: RunResult) -> dict[str, object]:
    """A finished turn, as the caller is told about it.

    `run_dir` and `log_path` are absent and that is the whole reason this
    function exists rather than a `dataclasses.asdict`. They are the host's
    filesystem layout; a remote caller cannot read them and should not be told
    them. They are typed `Path` in the domain so `json.dumps` raises rather
    than quietly stringifying, and this is the one place that knows which two
    fields to leave behind.

    `virtual_dir` is the machine-independent name for the same directory --
    `/runs/t001`, the string the agent itself was given -- and `artifacts` are
    relative to the same root, so a caller joining them gets a path the agent
    would recognise.
    """
    return {
        "session_id": result.session_id,
        "turn_id": result.turn_id,
        "answer": result.answer,
        "virtual_dir": result.virtual_dir,
        "artifacts": list(result.artifacts),
        "cut_short": result.cut_short,
    }


def event_payload(event: RunEvent) -> dict[str, object]:
    """One step of a run, without its kind.

    The kind is the SSE event name, so repeating it in the body would be two
    places for a consumer to read the same thing and one place for them to
    disagree.

    Defaults are omitted rather than sent as empty: a `token` event carries
    `text` and nothing else, and a stream of them is the bulk of a turn's
    bytes. Sending seven null fields per token to be uniform is a cost paid
    thousands of times per turn for a uniformity nobody consumes.
    """
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
    """One SSE frame: the kind as the event name, the rest as JSON.

    Named events rather than a single `message` carrying `{"kind": ...}`, so a
    consumer can subscribe to what it wants. The risk that buys -- a client
    never seeing a kind it does not name -- is why `KINDS` is pinned by a test
    rather than described in prose.
    """
    return f"event: {event.kind}\ndata: {json.dumps(event_payload(event))}\n\n"

"""What kingfisher keeps about a session: its conversation, and a turn paused for an
answer.

Read and written through the session's `HarnessFiles`, so through its backend. This
module owns the formats, not where they live.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from kingfisher.domain.result import PendingDecision
from kingfisher.domain.transcript import Message, as_json, from_json
from kingfisher.layout import PAUSED_MARK, PAUSED_STATE, TRANSCRIPT_FILE


def read_transcript(harness: Any) -> tuple[Message, ...]:
    """What was said in this session before now, or nothing for a first turn."""
    held = harness.read(TRANSCRIPT_FILE)
    return () if held is None else from_json(held.decode("utf-8"))


def write_transcript(harness: Any, messages: tuple[Message, ...]) -> None:
    """Replace this session's transcript with what it now holds."""
    harness.write(TRANSCRIPT_FILE, as_json(messages).encode("utf-8"))


#: Which agent the paused graph was built from. Kept for *checking* a resume, never
#: for granting one: capabilities are re-presented every time, and a different agent
#: is a different graph shape that this checkpoint's nodes do not belong to.
AGENT_MARK = "agent"

#: The gated calls the pause is waiting on, as the caller was told them.
#:
#: Kept rather than read back out of the checkpoint, and the ids are the reason: a
#: caller answers what it was handed, so the two must be the same list rather than
#: two derivations of one. Deriving them again would also need a compiled graph,
#: which the admission path does not have until after it has dealt with the pause.
PENDING_MARK = "pending"


def pending_from_mark(mark: Mapping[str, Any]) -> tuple[PendingDecision, ...]:
    """The gated calls a mark records, or nothing where it records none."""
    return tuple(
        PendingDecision(
            call_id=str(item.get("call_id") or ""),
            tool=str(item.get("tool") or ""),
            args=dict(item.get("args") or {}),
            agent=item.get("agent") or None,
            decisions=tuple(item.get("decisions") or ()),
        )
        for item in (mark.get(PENDING_MARK) or ())
        if isinstance(item, Mapping)
    )


def pending_as_mark(waiting: Sequence[PendingDecision]) -> list[dict[str, Any]]:
    """Those calls as the mark stores them."""
    return [
        {
            "call_id": item.call_id,
            "tool": item.tool,
            "args": dict(item.args),
            "agent": item.agent,
            "decisions": list(item.decisions),
        }
        for item in waiting
    ]


def read_pause_mark(harness: Any) -> dict[str, Any] | None:
    """What the paused checkpoint beside this was built against, or `None`."""
    written = harness.read(PAUSED_MARK)
    if written is None:
        return None
    try:
        held = json.loads(written)
    except ValueError:
        # Unreadable is the same answer as absent, and deliberately: this file
        # exists to refuse a resume, so a damaged one refusing it too is right.
        return None
    return held if isinstance(held, dict) else None


def write_pause_mark(harness: Any, mark: Mapping[str, Any]) -> None:
    """Record what the checkpoint written beside this was built against."""
    harness.write(PAUSED_MARK, json.dumps(dict(mark), sort_keys=True).encode("utf-8"))


def clear_pause(harness: Any) -> None:
    """Drop a pause, answered or superseded. Safe where there was never one."""
    harness.delete(PAUSED_STATE, PAUSED_MARK)

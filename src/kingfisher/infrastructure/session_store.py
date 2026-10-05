"""What kingfisher keeps about a session: the agent it opened with, its conversation,
and a turn paused for an answer.

Read and written through `HarnessFiles`, so through the session's backend. This
module owns the formats; the backend owns where they live.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from kingfisher.domain.result import PendingDecision
from kingfisher.domain.transcript import Message, as_json, from_json
from kingfisher.infrastructure.steps import Steps, changing, reading
from kingfisher.kinds.documents import DefinitionText
from kingfisher.layout import (
    HARNESS,
    HARNESS_ROUTE,
    PAUSED_MARK,
    PAUSED_STATE,
    PINNED_AGENT,
    TRANSCRIPT_FILE,
)


class HarnessFiles:
    """What kingfisher keeps about one session under `/.harness`, through its backend.

    Kept as written and read back as found. The agent's shell reaches the same
    backend, so what keeps it from rewriting its own pinned agent, conversation or
    paused turn is the shell's fence: kingfisher's sandbox, or a backend of the
    deployment's that passes `shell_denied`. `kingfisher doctor` says where it can
    see neither.
    """

    def __init__(self, backend: Any, session_id: str) -> None:
        self._backend = backend
        self._session_id = session_id

    def fetch(self, name: str) -> Steps[bytes | None]:
        """`name`'s content, or `None` where the session has none."""
        (content,) = yield from self._download(name)
        return content

    def store(self, name: str, content: bytes) -> Steps[None]:
        """Replace `name`."""
        files = [(f"{HARNESS_ROUTE}{name}", content)]
        answers = yield changing(self._backend, "upload_files", files)
        refused = [a for a in answers if a.error]
        if refused:
            kept = ", ".join(f"{a.path}: {a.error}" for a in refused)
            msg = f"the session's backend would not keep {kept}"
            raise OSError(msg)

    def drop(self, *names: str) -> Steps[None]:
        """Drop these. Safe where they were never written."""
        paths = [f"{HARNESS_ROUTE}{name}" for name in names]
        for path in paths:
            yield changing(self._backend, "delete", path)
        # Asked again rather than trusting each answer: a backend reports a file that
        # was never there as an error, and telling that from a delete that failed
        # would mean reading its wording.
        answers = yield reading(self._backend, "download_files", paths)
        left = [a.path for a in answers if a.error is None]
        if left:
            msg = f"the session's backend kept {', '.join(left)} after deleting it"
            raise OSError(msg)

    def _download(self, *names: str) -> Steps[list[bytes | None]]:
        answers = yield reading(
            self._backend, "download_files", [f"{HARNESS_ROUTE}{name}" for name in names]
        )
        found: list[bytes | None] = []
        for answer in answers:
            if answer.error == "file_not_found":
                found.append(None)
            elif answer.error is not None:
                msg = f"the session's backend could not read {answer.path}: {answer.error}"
                raise OSError(msg)
            else:
                found.append(answer.content)
        return found


#: What a refusal of the pinned agent calls it. Only a name: the pin is kept through
#: `HarnessFiles`, so changing this moves nothing.
AGENT_SNAPSHOT = f"{HARNESS}/{PINNED_AGENT}"


def remember_agent(harness: Any, document: str) -> Steps[None]:
    """Keep the agent definition this session opened with.

    Written once and never rewritten: a later turn naming the same agent is built
    from what the session started with, not from whatever the catalogue says by
    then. A deploy mid-conversation is ordinary; an agent's prompt changing under
    a history that already happened is not.
    """
    if (yield from harness.fetch(PINNED_AGENT)) is not None:
        return
    yield from harness.store(PINNED_AGENT, document.encode("utf-8"))


def agent_started_with(harness: Any) -> Steps[DefinitionText | None]:
    """The agent document this session opened with, or `None` if it kept none.

    `None` covers two ordinary cases: a session that named no agent, and a
    deployment whose repository cannot hand over the document it parsed.
    """
    held = yield from harness.fetch(PINNED_AGENT)
    return None if held is None else DefinitionText(held.decode("utf-8"), Path(AGENT_SNAPSHOT))


def read_transcript(harness: Any) -> Steps[tuple[Message, ...]]:
    """What was said in this session before now, or nothing for a first turn."""
    held = yield from harness.fetch(TRANSCRIPT_FILE)
    return () if held is None else from_json(held.decode("utf-8"))


def write_transcript(harness: Any, messages: tuple[Message, ...]) -> Steps[None]:
    """Replace this session's transcript with what it now holds."""
    yield from harness.store(TRANSCRIPT_FILE, as_json(messages).encode("utf-8"))


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


def read_pause_mark(harness: Any) -> Steps[dict[str, Any] | None]:
    """What the paused checkpoint beside this was built against, or `None`."""
    written = yield from harness.fetch(PAUSED_MARK)
    if written is None:
        return None
    try:
        held = json.loads(written)
    except ValueError:
        # Unreadable is the same answer as absent, and deliberately: this file
        # exists to refuse a resume, so a damaged one refusing it too is right.
        return None
    return held if isinstance(held, dict) else None


def write_pause_mark(harness: Any, mark: Mapping[str, Any]) -> Steps[None]:
    """Record what the checkpoint written beside this was built against."""
    yield from harness.store(PAUSED_MARK, json.dumps(dict(mark), sort_keys=True).encode("utf-8"))


def clear_pause(harness: Any) -> Steps[None]:
    """Drop a pause, answered or superseded. Safe where there was never one."""
    yield from harness.drop(PAUSED_STATE, PAUSED_MARK)

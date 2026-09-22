"""A `SessionStore` over a directory.

Deliberately dull. It walks, it writes, it deletes. Everything interesting about this
design is in *when* a caller reaches for it, not in what happens when they do, and a
first implementation that was clever about batching or streaming would be optimising a
cost nobody has measured yet.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from kingfisher.domain.references import within
from kingfisher.domain.result import PendingDecision
from kingfisher.domain.transcript import Message, as_json, from_json
from kingfisher.layout import HARNESS, PAUSED_MARK, PAUSED_STATE, TRANSCRIPT_FILE

if TYPE_CHECKING:
    from kingfisher.domain.ports import SessionStore


class LocalSessionStore:
    """Sessions kept as directories under `root`, one per session id."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def _held(self, session_id: str) -> Path:
        """Where one session's files sit, refusing an id that would escape."""
        return within(self.root, session_id)

    def fetch(self, session_id: str) -> dict[str, bytes]:
        """Everything kept for this session, keyed by path relative to its root."""
        held = self._held(session_id)
        if not held.is_dir():
            return {}
        return {
            str(path.relative_to(held)): path.read_bytes()
            for path in sorted(held.rglob("*"))
            if path.is_file()
        }

    def save(self, session_id: str, files: Mapping[str, bytes]) -> None:
        """Keep these files, replacing any of the same name."""
        held = self._held(session_id)
        for name, content in files.items():
            target = within(held, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)

    def knows(self, session_id: str) -> bool:
        """Whether anything is held for this session, without reading it."""
        return self._held(session_id).is_dir()

    def forget(self, session_id: str) -> None:
        """Drop everything kept for this session. Idempotent."""
        shutil.rmtree(self._held(session_id), ignore_errors=True)


def restore_into(store: SessionStore, session_id: str, directory: Path) -> tuple[str, ...]:
    """Write back what the store kept, for a directory that has lost it."""
    written: list[str] = []
    for name, content in store.fetch(session_id).items():
        target = within(directory, name)
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        written.append(name)
    return tuple(written)


def keep_from(store: SessionStore, session_id: str, directory: Path, names: Sequence[str]) -> None:
    """Hand the named files to the store, reading them from `directory`."""
    store.save(
        session_id,
        {
            name: within(directory, name).read_bytes()
            for name in names
            if within(directory, name).is_file()
        },
    )


#: Where a session's conversation is kept: inside the session, so it is deleted
#: with the session, counted by `session_bytes`, and carried by whatever keeps
#: the rest -- and under `.harness`, so the agent cannot edit it.
#:
#: It sat at the session root, which bought the first three and not the last: the
#: shell roots at the session, so the conversation the next turn is rebuilt from
#: was one the current turn could rewrite.
TRANSCRIPT = f"{HARNESS}/{TRANSCRIPT_FILE}"


def read_transcript(directory: Path) -> tuple[Message, ...]:
    """What was said in this session before now, or nothing for a first turn."""
    held = Path(directory) / TRANSCRIPT
    if not held.is_file():
        return ()
    return from_json(held.read_text(encoding="utf-8"))


def write_transcript(directory: Path, messages: tuple[Message, ...]) -> None:
    """Replace this session's transcript with what it now holds."""
    # The transcript's own directory, not the session's: `TRANSCRIPT` is a path
    # under `.harness` now, and a session restored onto a host that has only the
    # store has neither yet.
    path = Path(directory) / TRANSCRIPT
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(as_json(messages), encoding="utf-8")


#: Where a paused turn's graph state and its provenance live. Under `.harness` for
#: the reasons the transcript is: deleted with the session, counted by
#: `session_bytes`, carried by whatever carries the rest, and out of reach of the
#: file tools. Out of the *shell's* reach only where the sandbox profile is running,
#: which is why what goes here is msgpack and never a pickle.
PAUSED = f"{HARNESS}/{PAUSED_STATE}"
PAUSED_PROVENANCE = f"{HARNESS}/{PAUSED_MARK}"

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


def paused_path(directory: Path) -> Path:
    """Where this session's paused graph state goes, whether or not it is there."""
    return Path(directory) / PAUSED


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


def read_pause_mark(directory: Path) -> dict[str, Any] | None:
    """What the paused checkpoint beside this was built against, or `None`."""
    path = Path(directory) / PAUSED_PROVENANCE
    if not path.is_file():
        return None
    try:
        held = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        # Unreadable is the same answer as absent, and deliberately: this file
        # exists to refuse a resume, so a damaged one refusing it too is right.
        return None
    return held if isinstance(held, dict) else None


def write_pause_mark(directory: Path, mark: Mapping[str, Any]) -> None:
    """Record what the checkpoint written beside this was built against."""
    path = Path(directory) / PAUSED_PROVENANCE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(mark), sort_keys=True), encoding="utf-8")


def clear_pause(directory: Path) -> None:
    """Drop a pause, answered or superseded. Safe where there was never one."""
    for name in (PAUSED, PAUSED_PROVENANCE):
        (Path(directory) / name).unlink(missing_ok=True)

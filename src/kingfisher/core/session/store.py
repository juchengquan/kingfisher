"""What kingfisher keeps about a session: the agent it opened with, its conversation,
the ledger of what its tools returned, and a turn paused for an answer.

This module owns the formats; the session's backend, through `HarnessFiles`, owns
where they live.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.layout import (
    HARNESS,
    HARNESS_ROUTE,
    LEDGER_FILE,
    PAUSED_MARK,
    PAUSED_STATE,
    PINNED_AGENT,
    PINNED_ZONE,
    TRANSCRIPT_FILE,
)
from kingfisher.core.result import LedgerEntry, Listed, PendingDecision

from .steps import Steps, changing, reading
from .transcript import Message, as_json, from_json


class HarnessFiles:
    """What kingfisher keeps about one session under `/.harness`, through its backend.

    Read back as found: the agent's shell reaches the same backend, so only the shell's
    fence keeps it from rewriting its own pinned agent, conversation or paused turn.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend

    def fetch(self, name: str) -> Steps[bytes | None]:
        """`name`'s content, or `None` where the session has none."""
        (content,) = yield from self._download(name)
        return content

    def fetch_each(self, *names: str) -> Steps[list[bytes | None]]:
        """`fetch`, for several names in one read of the backend."""
        return (yield from self._download(*names))

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
        # Asked again rather than trusting each answer: a backend reports a never-written file
        # as an error, and telling that from a failed delete would mean reading its wording.
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


#: What a refusal of the pinned agent calls it: only a name, so changing it moves nothing.
AGENT_SNAPSHOT = f"{HARNESS}/{PINNED_AGENT}"


def remember_agent(harness: Any, document: str) -> Steps[None]:
    """Keep the agent definition this session opened with.

    Written once and never rewritten: a later turn is built from what the session
    started with, so a deploy cannot change an agent under a history already made.
    """
    if (yield from harness.fetch(PINNED_AGENT)) is not None:
        return
    yield from harness.store(PINNED_AGENT, document.encode("utf-8"))


def _agent_text(held: bytes | None) -> DefinitionText | None:
    return None if held is None else DefinitionText(held.decode("utf-8"), Path(AGENT_SNAPSHOT))


def remember_zone(harness: Any, zone: str) -> Steps[None]:
    """Keep the datazone this session's first zoned turn ran in.

    Never rewritten: what that turn's tools returned is in the session for good, and a
    caller in another datazone is refused before they could reach this.
    """
    if (yield from harness.fetch(PINNED_ZONE)) is not None:
        return
    yield from harness.store(PINNED_ZONE, zone.encode("utf-8"))


def pinned_in(harness: Any) -> Steps[tuple[DefinitionText | None, str | None]]:
    """The agent this session opened with and the datazone it is in, in one read, since
    a listing asks it of every session."""
    agent, zone = yield from harness.fetch_each(PINNED_AGENT, PINNED_ZONE)
    return _agent_text(agent), None if zone is None else zone.decode("utf-8")


def read_transcript(harness: Any) -> Steps[tuple[Message, ...]]:
    """What was said in this session before now, or nothing for a first turn."""
    held = yield from harness.fetch(TRANSCRIPT_FILE)
    return () if held is None else from_json(held.decode("utf-8"))


def write_transcript(harness: Any, messages: tuple[Message, ...]) -> Steps[None]:
    """Replace this session's transcript with what it now holds."""
    yield from harness.store(TRANSCRIPT_FILE, as_json(messages).encode("utf-8"))


def entry_id(number: int) -> str:
    """The id a ledger entry is given: `A-` and at least four digits."""
    return f"A-{number:04d}"


#: The shape `entry_id` gives, its number the one group; a result's stamp is read by it too.
ENTRY_ID = re.compile(r"A-(\d+)")


def names_a_result(name: str) -> bool:
    """Whether a file in the tool results folder is one a recorder wrote.

    The agent writes in that folder too, so the sweep cannot treat everything there as
    a result whose row has gone.
    """
    # `match`, not `fullmatch`: the id is only the front of the file's name.
    return ENTRY_ID.match(name) is not None


#: On the closing line of a turn that resumed a paused one, and only there.
RESUMED = "resumed"

#: On the closing line of a turn that ended on a summarised conversation, and only there.
SUMMARISED = "summarised"


@dataclass(frozen=True)
class KeptLedger:
    """The ledger as a turn found it, and what the turn needs to know of it."""

    #: Its rows and closing lines as written, which the turn writes back ahead of its own.
    lines: tuple[str, ...] = ()
    #: The highest number an entry has had, so the next is one past it.
    last: int = 0
    #: Every file a row names, which the sweep keeps.
    paths: frozenset[str] = frozenset()
    #: The parent's `task` call id -> the access of each entry under it, kept across
    #: turns because a subagent paused at a gate finishes in the next one.
    runs: Mapping[str, tuple[Listed, ...]] = field(default_factory=dict)
    #: Each exchange's entries, oldest first, empty ones included. An exchange is a turn
    #: with a user message and the resumes after it, so a paused `task` and its answer
    #: share one.
    exchanges: tuple[tuple[LedgerEntry, ...], ...] = ()
    #: Whether a turn of the last exchange ended on a summarised conversation.
    summarised: bool = False

    @property
    def since(self) -> tuple[LedgerEntry, ...]:
        """The entries recorded since the last user message."""
        return self.exchanges[-1] if self.exchanges else ()


def read_ledger(harness: Any) -> Steps[KeptLedger]:
    """This session's ledger, or an empty one where no turn has written it."""
    held = yield from harness.fetch(LEDGER_FILE)
    return KeptLedger() if held is None else _kept(held.decode("utf-8"))


def _kept(text: str) -> KeptLedger:
    lines: list[str] = []
    last = 0
    paths: set[str] = set()
    runs: dict[str, list[Listed]] = {}
    exchanges: list[list[LedgerEntry]] = []
    summarised = False
    # Each turn's entries, until its closing line says whether it was a resume.
    unclosed: dict[str, list[LedgerEntry]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            # Dropped, not refused: a backend has no rename, so a crash mid-write can cut
            # the file anywhere, and refusing the rest would refuse every later turn. A
            # lost row's number may be given again; accepted, as for the transcript.
            continue
        if not isinstance(row, dict):
            continue
        lines.append(line)
        if isinstance(row.get("id"), str) and (number := ENTRY_ID.fullmatch(row["id"])):
            last = max(last, int(number.group(1)))
        if isinstance(row.get("path"), str):
            paths.add(row["path"])
        if isinstance(row.get("run"), str) and isinstance(row.get("access"), list):
            runs.setdefault(row["run"], []).append(_listed(row["access"]))
        if (entry := _entry(row)) is not None:
            unclosed.setdefault(entry.turn, []).append(entry)
        elif "id" not in row and isinstance(row.get("turn"), str):
            # By the turn a row names, not where it sits: rows a crash left unclosed would
            # otherwise be counted as the next turn's.
            closed = unclosed.pop(row["turn"], [])
            if row.get(RESUMED) and exchanges:
                exchanges[-1].extend(closed)
                summarised = summarised or bool(row.get(SUMMARISED))
            else:
                exchanges.append(closed)
                summarised = bool(row.get(SUMMARISED))
    return KeptLedger(
        lines=tuple(lines),
        last=last,
        paths=frozenset(paths),
        runs={run: tuple(found) for run, found in runs.items()},
        exchanges=tuple(tuple(one) for one in exchanges),
        summarised=summarised,
    )


def _listed(access: list[Any]) -> Listed:
    return tuple(one if isinstance(one, str) else tuple(one) for one in access)


_FIELDS = tuple(one.name for one in fields(LedgerEntry))


def _entry(row: Mapping[str, Any]) -> LedgerEntry | None:
    """A row as the entry it was written from, or `None` for a closing line."""
    if not all(name in row for name in _FIELDS):
        return None
    held = {name: row[name] for name in _FIELDS}
    if isinstance(held["access"], list):
        held["access"] = _listed(held["access"])
    return LedgerEntry(**held)


def write_ledger(  # noqa: PLR0913 -- two marks, by keyword so neither is passed for the other
    harness: Any,
    earlier: KeptLedger,
    entries: Sequence[LedgerEntry],
    *,
    turn: str,
    resumed: bool = False,
    summarised: bool = False,
) -> Steps[None]:
    """Replace the ledger with what it held, this turn's rows, and this turn's closing line.

    The closing line is written even with no rows: turn ids are not a sequence, so the
    file's order is the only record of the turns' order.
    """
    marks = {RESUMED: resumed, SUMMARISED: summarised}
    rows = [
        *earlier.lines,
        *(json.dumps(asdict(entry), ensure_ascii=False) for entry in entries),
        json.dumps({"turn": turn, **{mark: True for mark, said in marks.items() if said}}),
    ]
    yield from harness.store(LEDGER_FILE, ("\n".join(rows) + "\n").encode("utf-8"))


#: Which agent the paused graph was built from. For *checking* a resume, never granting
#: one: capabilities are presented afresh, and another agent's graph does not fit.
AGENT_MARK = "agent"

#: The gated calls the pause is waiting on, as the caller was told them. Kept, not
#: re-derived: a caller answers the ids it was handed, and deriving them needs a
#: compiled graph, which admission has only after it has dealt with the pause.
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
        # Unreadable answers as absent: this file exists to refuse a resume, so a
        # damaged one refusing it too is right.
        return None
    return held if isinstance(held, dict) else None


def write_pause_mark(harness: Any, mark: Mapping[str, Any]) -> Steps[None]:
    """Record what the checkpoint written beside this was built against."""
    yield from harness.store(PAUSED_MARK, json.dumps(dict(mark), sort_keys=True).encode("utf-8"))


def clear_pause(harness: Any) -> Steps[None]:
    """Drop a pause, answered or superseded. Safe where there was never one."""
    yield from harness.drop(PAUSED_STATE, PAUSED_MARK)

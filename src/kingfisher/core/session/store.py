"""What kingfisher keeps about a session beside its turn log: the ledger of what its tools
returned, and a turn paused for an answer.

This module owns the formats; the session's backend, through `HarnessFiles`, owns
where they live.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import MISSING, asdict, dataclass, field, fields
from pathlib import PurePosixPath
from typing import Any

from kingfisher.core.layout import (
    HARNESS_ROUTE,
    INPUTS_SEEN,
    KEPT_FILES,
    LEDGER_FILE,
    OFF_PATH_FILE,
    PAUSED_MARK,
    PAUSED_STATE,
    REWOUND_FILE,
)
from kingfisher.core.result import LedgerEntry, Listed, PendingDecision

from .steps import Steps, changing, reading

#: Beside each file kingfisher keeps about a session, its second copy: `ledger.jsonl.2`.
SECOND_COPY = ".2"


def sealed(content: bytes) -> bytes:
    """`content` with a last line giving its SHA-256."""
    seal = json.dumps({"sha256": hashlib.sha256(content).hexdigest()})
    return content + b"\n" + seal.encode("ascii") + b"\n"


def unsealed(held: bytes) -> bytes | None:
    """What `sealed` was handed, or `None` where `held` does not end in a seal matching what
    comes before it: a write that stopped partway, or a file written before seals were."""
    content, _, last = held.removesuffix(b"\n").rpartition(b"\n")
    try:
        seal = json.loads(last)
    except ValueError:
        return None
    return content if seal == {"sha256": hashlib.sha256(content).hexdigest()} else None


#: Which copy a read of a file kept twice is answered from.
_FIRST, _SECOND = "first", "second"


def _kept_twice(name: str) -> bool:
    # A copy under `files/` goes back into the session byte for byte on a rewind, where a
    # seal would end up in the user's file.
    return not name.startswith(f"{KEPT_FILES}/")


class HarnessFiles:
    """What kingfisher keeps about one session under `/.harness`, through its backend.

    Read back as found: the agent's shell reaches the same backend, so only the shell's
    fence keeps it from rewriting its own conversation or paused turn.
    """

    def __init__(self, backend: Any) -> None:
        self._backend = backend
        # Where this object's last read of each file found it whole, so a save knows which
        # copy to leave alone until the other is written.
        self._read_from: dict[str, str] = {}

    def fetch(self, name: str) -> Steps[bytes | None]:
        """`name`'s content, or `None` where the session has none."""
        held = yield from self._fetch_one(name)
        if held is None or not _kept_twice(name):
            return held
        if (whole := unsealed(held)) is not None:
            self._read_from[name] = _FIRST
            return whole
        # A save writes the copy a read is not answered from before the other, so a first
        # copy that does not match is one a save stopped in, and the second is whole.
        second = yield from self._fetch_one(f"{name}{SECOND_COPY}")
        if second is not None and (whole := unsealed(second)) is not None:
            self._read_from[name] = _SECOND
            return whole
        # Older than its second copy, and no save since has finished one.
        self._read_from[name] = _FIRST
        return held

    def store(self, name: str, content: bytes) -> Steps[None]:
        """Replace `name`."""
        if not _kept_twice(name):
            yield from self._store_one(name, content)
            return
        whole = sealed(content)
        read_from = self._read_from.pop(name, None)
        if read_from is None:
            yield from self.fetch(name)
            read_from = self._read_from.pop(name, _FIRST)
        # In uploads of their own, the copy a read is answered from last: a backend may
        # empty a file and then fill it, and a save stopping in the copy reads come from,
        # after one stopped in the other, would leave neither whole.
        second = f"{name}{SECOND_COPY}"
        for one in (name, second) if read_from == _SECOND else (second, name):
            yield from self._store_one(one, whole)
        self._read_from[name] = _FIRST

    def _fetch_one(self, name: str) -> Steps[bytes | None]:
        (answer,) = yield reading(self._backend, "download_files", [f"{HARNESS_ROUTE}{name}"])
        if answer.error == "file_not_found":
            return None
        if answer.error is not None:
            msg = f"the session's backend could not read {answer.path}: {answer.error}"
            raise OSError(msg)
        return answer.content

    def _store_one(self, name: str, content: bytes) -> Steps[None]:
        files = [(f"{HARNESS_ROUTE}{name}", content)]
        answers = yield changing(self._backend, "upload_files", files)
        refused = [a for a in answers if a.error]
        if refused:
            kept = ", ".join(f"{a.path}: {a.error}" for a in refused)
            msg = f"the session's backend would not keep {kept}"
            raise OSError(msg)

    def folders(self, name: str) -> Steps[tuple[str, ...]]:
        """The names of the folders directly under `name`; none where it is not there."""
        listing = yield reading(self._backend, "ls", f"{HARNESS_ROUTE}{name}")
        return tuple(
            PurePosixPath(one["path"]).name for one in listing.entries or () if one.get("is_dir")
        )

    def drop(self, *names: str) -> Steps[None]:
        """Drop these. Safe where they were never written."""
        for name in names:
            self._read_from.pop(name, None)
        paths = [
            f"{HARNESS_ROUTE}{one}"
            for name in names
            # The first copy first: a read that finds none answers that there is none.
            for one in ((name, f"{name}{SECOND_COPY}") if _kept_twice(name) else (name,))
        ]
        for path in paths:
            yield changing(self._backend, "delete", path)
        # Asked again rather than trusting each answer: a backend reports a never-written file
        # as an error, and telling that from a failed delete would mean reading its wording.
        answers = yield reading(self._backend, "download_files", paths)
        left = [a.path for a in answers if a.error is None]
        if left:
            msg = f"the session's backend kept {', '.join(left)} after deleting it"
            raise OSError(msg)


def read_inputs_seen(harness: HarnessFiles) -> Steps[dict[str, str | None] | None]:
    """The inputs folder as the last turn the agent heard from found it, or `None` where
    no turn kept it."""
    return (yield from read_listing(harness, INPUTS_SEEN))


def write_inputs_seen(harness: HarnessFiles, seen: Mapping[str, str | None]) -> Steps[None]:
    """Keep the inputs folder as this turn found it."""
    yield from write_listing(harness, INPUTS_SEEN, seen)


def read_listing(harness: HarnessFiles, name: str) -> Steps[dict[str, str | None] | None]:
    """Each file a listing kept as `name` holds, with its version mark, or `None` where no
    turn kept one."""
    held = yield from harness.fetch(name)
    if held is None:
        return None
    try:
        seen = json.loads(held)
    # Unreadable is unknown: told less for a turn beats a turn that fails.
    except ValueError:
        return None
    if not isinstance(seen, dict):
        return None
    return {
        str(path): mark if isinstance(mark, str) else None for path, mark in seen.items()
    }


def write_listing(harness: HarnessFiles, name: str, seen: Mapping[str, str | None]) -> Steps[None]:
    """Keep a listing as `name`."""
    yield from harness.store(
        name, json.dumps(dict(seen), sort_keys=True, ensure_ascii=False).encode("utf-8")
    )


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

#: On the closing line of a turn whose highest id is held by no row: the call that took it
#: failed, or was paused and run again.
RESERVED = "reserved"

#: On the closing line of a turn that wrote the conversation, how many messages it wrote.
#: Here because the ledger is written first: a conversation emptied by a full disk, cut on
#: a line, or never written reads back whole, and only this says it is short.
MESSAGES = "messages"


@dataclass(frozen=True)
class Drift:
    """The files a rewind left otherwise than the turn the next one follows left them."""

    #: Known to differ.
    changed: tuple[str, ...] = ()
    #: Not known either way: nothing recorded them when that turn ended, or the listing
    #: gives them no mark.
    unsure: tuple[str, ...] = ()


def read_drift(harness: HarnessFiles) -> Steps[Drift | None]:
    """The files the last rewind left otherwise than the conversation remembers them, or
    `None` where no rewind left a notice."""
    held = yield from harness.fetch(REWOUND_FILE)
    try:
        noted = json.loads(held) if held is not None else None
    except ValueError:
        return None
    if not isinstance(noted, dict):
        return None
    listed = {
        key: tuple(one for one in noted.get(key) or () if isinstance(one, str))
        for key in ("changed", "unsure")
    }
    return Drift(**listed)


def write_drift(harness: HarnessFiles, drift: Drift) -> Steps[None]:
    """Keep `drift` for the next turn to tell, or clear an older one where it holds none."""
    if not drift.changed and not drift.unsure:
        yield from harness.drop(REWOUND_FILE)
        return
    noted = {"changed": list(drift.changed), "unsure": list(drift.unsure)}
    yield from harness.store(REWOUND_FILE, json.dumps(noted).encode("utf-8"))


@dataclass(frozen=True)
class KeptLedger:
    """The ledger as a turn found it, and what the turn needs to know of it."""

    #: Its rows and closing lines as written, which the turn writes back ahead of its own.
    lines: tuple[str, ...] = ()
    #: Its closing lines, parsed, in the order the turns ran, on the path or not.
    closings: tuple[Mapping[str, Any], ...] = ()
    #: The highest number a call has been given, a row or not, so the next is one past it.
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
    #: Beside each exchange, the entries of turns a cut write left without a closing line
    #: before it. Kept out of `exchanges`, which a message lists, and aged with the
    #: exchange that closed next, so they expire no sooner than the turn after them.
    unclosed: tuple[tuple[LedgerEntry, ...], ...] = ()
    #: Whether a turn of the last exchange ended on a summarised conversation.
    summarised: bool = False
    #: How many messages the last turn to write the conversation wrote, or `None` where
    #: no closing line says.
    messages: int | None = None
    #: Every turn id a line names, a cut turn's rows included: ids a later turn may not take.
    turns: frozenset[str] = frozenset()
    #: The turns a rewind left behind, whose lines stay so a rewind can return to them.
    #: `exchanges`, `unclosed`, `summarised` and `messages` leave them out, being what the
    #: conversation the model is handed holds.
    off_path: frozenset[str] = frozenset()
    #: `exchanges` and `unclosed` with every branch in, in the order the turns ran, or
    #: `None` where no turn is off the path and the two are the same.
    every_exchange: tuple[tuple[LedgerEntry, ...], ...] | None = None
    every_unclosed: tuple[tuple[LedgerEntry, ...], ...] | None = None

    @property
    def all_exchanges(self) -> tuple[tuple[LedgerEntry, ...], ...]:
        """Every branch's exchanges: what `expired` counts rounds in, since a result left on
        a branch ages as one on the path does."""
        return self.exchanges if self.every_exchange is None else self.every_exchange

    @property
    def all_unclosed(self) -> tuple[tuple[LedgerEntry, ...], ...]:
        """`unclosed` beside `all_exchanges`."""
        return self.unclosed if self.every_unclosed is None else self.every_unclosed

    @property
    def since(self) -> tuple[LedgerEntry, ...]:
        """The entries recorded since the last user message."""
        return self.exchanges[-1] if self.exchanges else ()

    def row(self, number: str) -> LedgerEntry | None:
        """The row of result `number`, closed or not, on the path or not, or `None` where
        none is kept."""
        for group in (*self.all_exchanges, *self.all_unclosed):
            for entry in group:
                if entry.id == number:
                    return entry
        return None


def read_ledger(harness: HarnessFiles) -> Steps[KeptLedger]:
    """This session's ledger, or an empty one where no turn has written it."""
    held = yield from harness.fetch(LEDGER_FILE)
    if held is None:
        return KeptLedger()
    # Replaced, not refused: rows hold any language, and a cut inside a character raised
    # on every later turn.
    return _kept(held.decode("utf-8", errors="replace"), (yield from read_off_path(harness)))


def read_off_path(harness: HarnessFiles) -> Steps[frozenset[str]]:
    """The turns a rewind left off the session's path, or none where it never rewound.

    Unreadable reads as none: the ledger then lists a branch's results beside the path's,
    which is less than a turn refused for it.
    """
    held = yield from harness.fetch(OFF_PATH_FILE)
    try:
        listed = json.loads(held) if held is not None else []
    except ValueError:
        return frozenset()
    if not isinstance(listed, list):
        return frozenset()
    return frozenset(one for one in listed if isinstance(one, str))


def write_off_path(harness: HarnessFiles, turns: frozenset[str]) -> Steps[None]:
    """Keep `turns` as the ones off the session's path."""
    yield from harness.store(OFF_PATH_FILE, json.dumps(sorted(turns)).encode("utf-8"))


class _Track:
    """Exchanges as a closing line at a time adds to them."""

    def __init__(self) -> None:
        self.exchanges: list[list[LedgerEntry]] = []
        self.cut: list[list[LedgerEntry]] = []
        self.summarised = False

    def close(
        self, row: Mapping[str, Any], closed: list[LedgerEntry], left: list[LedgerEntry]
    ) -> None:
        if row.get(RESUMED) and self.exchanges:
            self.exchanges[-1].extend(closed)
            self.cut[-1].extend(left)
            self.summarised = self.summarised or bool(row.get(SUMMARISED))
        else:
            self.exchanges.append(closed)
            self.cut.append(left)
            self.summarised = bool(row.get(SUMMARISED))


def _kept(text: str, off_path: frozenset[str] = frozenset()) -> KeptLedger:
    lines: list[str] = []
    last = 0
    paths: set[str] = set()
    runs: dict[str, list[Listed]] = {}
    every, on_path = _Track(), _Track()
    messages: int | None = None
    turns: set[str] = set()
    closings: list[Mapping[str, Any]] = []
    # Each turn's entries, until its closing line says whether it was a resume.
    unclosed: dict[str, list[LedgerEntry]] = {}
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            # Dropped, not refused: a backend has no rename, so a crash mid-write can cut
            # the file anywhere, and refusing the rest would refuse every later turn. A
            # lost row's number may be given again; accepted.
            continue
        if not isinstance(row, dict):
            continue
        lines.append(line)
        for given in (row.get("id"), row.get(RESERVED)):
            if isinstance(given, str) and (number := ENTRY_ID.fullmatch(given)):
                last = max(last, int(number.group(1)))
        if isinstance(row.get("path"), str):
            paths.add(row["path"])
        if isinstance(row.get("run"), str) and isinstance(row.get("access"), list):
            runs.setdefault(row["run"], []).append(_listed(row["access"]))
        if (entry := _entry(row)) is not None:
            turns.add(entry.turn)
            unclosed.setdefault(entry.turn, []).append(entry)
        elif "id" not in row and isinstance(row.get("turn"), str):
            turns.add(row["turn"])
            closings.append(row)
            # By the turn a row names, not where it sits: rows a crash left unclosed would
            # otherwise be counted as the next turn's.
            closed = unclosed.pop(row["turn"], [])
            # Every write copies the lines before it and closes only its own turn, so a
            # turn still open here never closes: its write was cut.
            left = [entry for entries in unclosed.values() for entry in entries]
            unclosed.clear()
            # A list each: a resume extends the exchange it joins in place.
            every.close(row, list(closed), list(left))
            if row["turn"] not in off_path:
                if isinstance(row.get(MESSAGES), int):
                    messages = row[MESSAGES]
                on_path.close(row, list(closed), [one for one in left if one.turn not in off_path])
    return KeptLedger(
        lines=tuple(lines),
        closings=tuple(closings),
        last=last,
        paths=frozenset(paths),
        runs={run: tuple(found) for run, found in runs.items()},
        exchanges=tuple(tuple(one) for one in on_path.exchanges),
        unclosed=tuple(tuple(one) for one in on_path.cut),
        summarised=on_path.summarised,
        messages=messages,
        turns=frozenset(turns),
        off_path=off_path,
        every_exchange=tuple(tuple(one) for one in every.exchanges) if off_path else None,
        every_unclosed=tuple(tuple(one) for one in every.cut) if off_path else None,
    )


def _listed(access: list[Any]) -> Listed:
    return tuple(one if isinstance(one, str) else tuple(one) for one in access)


_FIELDS = tuple(one.name for one in fields(LedgerEntry))
#: A field with a default is one rows written before it lack: required, every older row
#: would read as a closing line and drop out of the exchanges.
_REQUIRED = tuple(one.name for one in fields(LedgerEntry) if one.default is MISSING)


def _entry(row: Mapping[str, Any]) -> LedgerEntry | None:
    """A row as the entry it was written from, or `None` for a closing line."""
    if not all(name in row for name in _REQUIRED):
        return None
    held = {name: row[name] for name in _FIELDS if name in row}
    if isinstance(held["access"], list):
        held["access"] = _listed(held["access"])
    return LedgerEntry(**held)


def expired(
    earlier: KeptLedger, rounds: int | None, *, turn: str, resumed: bool
) -> KeptLedger:
    """`earlier` with every kept result older than `rounds` exchanges marked expired in
    `turn`.

    The current exchange counts as one of the `rounds`: a resume carries on the last kept one.
    """
    if rounds is None:
        return earlier
    kept = rounds if resumed else rounds - 1
    # Every branch, in the order the turns ran: a result left on a branch ages as one on
    # the path does, or a rewind would keep its file past the rounds forever.
    old = max(len(earlier.all_exchanges) - kept, 0)
    # One still in the conversation as well as one filed: expired there, it gets no file
    # when it leaves, and its handle says to call the tool again.
    expiring = {
        entry.id
        for exchange in (*earlier.all_exchanges[:old], *earlier.all_unclosed[:old])
        for entry in exchange
        if entry.expired_in is None and not entry.unkept
    }
    return patched(earlier, {one: {"path": None, "expired_in": turn} for one in expiring})


def patched(earlier: KeptLedger, changes: Mapping[str, Mapping[str, Any]]) -> KeptLedger:
    """`earlier` with each row `changes` names by id given the fields it maps to."""
    if not changes:
        return earlier
    lines = []
    for line in earlier.lines:
        row = json.loads(line)
        # Patched as parsed rather than rewritten from the entry, which would drop any key
        # this version does not read.
        change = changes.get(row["id"]) if _entry(row) is not None else None
        lines.append(line if change is None else json.dumps({**row, **change}, ensure_ascii=False))
    return earlier if lines == list(earlier.lines) else _kept("\n".join(lines), earlier.off_path)


def write_ledger(  # noqa: PLR0913 -- three marks, by keyword so none is passed for another
    harness: HarnessFiles,
    earlier: KeptLedger,
    entries: Sequence[LedgerEntry],
    *,
    turn: str,
    resumed: bool = False,
    summarised: bool = False,
    reserved: str | None = None,
    messages: int | None = None,
) -> Steps[None]:
    """Replace the ledger with what it held, this turn's rows, and this turn's closing line.

    The closing line is written even with no rows: turn ids are not a sequence, so the
    file's order is the only record of the turns' order. `reserved` is the highest id
    this turn gave a call, where no row holds it; `messages`, how many the conversation
    this turn is about to write holds, where it writes one.
    """
    marks = {RESUMED: resumed, SUMMARISED: summarised}
    closing = {
        "turn": turn,
        **{mark: True for mark, said in marks.items() if said},
        **({RESERVED: reserved} if reserved is not None else {}),
        **({MESSAGES: messages} if messages is not None else {}),
    }
    rows = [
        *earlier.lines,
        *(json.dumps(asdict(entry), ensure_ascii=False) for entry in entries),
        json.dumps(closing),
    ]
    yield from harness.store(LEDGER_FILE, ("\n".join(rows) + "\n").encode("utf-8"))


#: Which agent the paused graph was built from. For *checking* a resume, never granting
#: one: capabilities are presented afresh, and another agent's graph does not fit.
AGENT_MARK = "agent"

#: A digest of each file the paused graph's tools, skills, subagents and middleware were
#: read from, by path. A resume on files that differ would run a turn planned against
#: others.
BUILT_FROM_MARK = "built_from"

#: The gated calls the pause is waiting on, as the caller was told them. Kept, not
#: re-derived: a caller answers the ids it was handed, and deriving them needs a
#: compiled graph, which admission has only after it has dealt with the pause.
PENDING_MARK = "pending"

#: The turn that began answering the pause, written before the approved calls run. A kill
#: stops a turn before the ending that clears its pause, and nothing else on disk says
#: whether those calls ran, so a second answer would run them again.
TAKEN_MARK = "taken_by"


def pending_from_mark(mark: Mapping[str, Any]) -> tuple[PendingDecision, ...]:
    """The gated calls a mark records as still waiting: none where it records none, or
    where a resume has taken them."""
    if mark.get(TAKEN_MARK):
        return ()
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


def read_pause_mark(harness: HarnessFiles) -> Steps[dict[str, Any] | None]:
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


def write_pause_mark(harness: HarnessFiles, mark: Mapping[str, Any]) -> Steps[None]:
    """Record what the checkpoint written beside this was built against."""
    yield from harness.store(PAUSED_MARK, json.dumps(dict(mark), sort_keys=True).encode("utf-8"))


def take_pause(harness: HarnessFiles, mark: Mapping[str, Any], turn_id: str) -> Steps[None]:
    """Record that `turn_id` is answering the pause `mark` describes."""
    yield from write_pause_mark(harness, {**mark, TAKEN_MARK: turn_id})


def clear_pause(harness: HarnessFiles) -> Steps[None]:
    """Drop a pause, answered or superseded. Safe where there was never one."""
    yield from harness.drop(PAUSED_STATE, PAUSED_MARK)

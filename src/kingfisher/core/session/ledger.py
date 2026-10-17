"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend
from deepagents.backends.utils import sanitize_tool_call_id

from kingfisher.core.layout import TOOL_RESULTS_ROUTE
from kingfisher.core.session.steps import Steps, changing, reading
from kingfisher.core.session.store import (
    KeptLedger,
    entry_id,
    names_a_result,
    write_ledger,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.result import LedgerEntry, Listed


class Ledger:
    """What every graph one turn built recorded, gathered for the turn to keep.

    One per turn, handed to every recorder the build makes; the graph is per turn too.
    """

    def __init__(self, turn: str, earlier: KeptLedger, *, resumed: bool = False) -> None:
        self.turn = turn
        self.earlier = earlier
        #: Whether this turn answers a paused one, which its closing line says: a resume
        #: adds no user message, and the next one's delta reaches back past it.
        self.resumed = resumed
        # Calls made together run on threads, each with its own recorder.
        self._lock = threading.Lock()
        self._entries: list[LedgerEntry] = []

    def add(self, row: Callable[[str], LedgerEntry]) -> LedgerEntry:
        """Number one result and keep its row, as one step.

        `row` builds the entry from the id it is given, under the lock, so two results
        arriving together cannot take one number.
        """
        with self._lock:
            entry = row(entry_id(self.earlier.last + len(self._entries) + 1))
            self._entries.append(entry)
        return entry

    def unkept(self, entry: LedgerEntry) -> None:
        """Mark one entry as having no file, because the backend would not take it."""
        with self._lock:
            self._entries[self._entries.index(entry)] = replace(entry, path=None)

    def access_in(self, run: str) -> tuple[Listed, ...]:
        """The access of every entry recorded under one run, before this turn as well."""
        with self._lock:
            mine = tuple(
                e.access for e in self._entries if e.run == run and e.access is not None
            )
        return (*self.earlier.runs.get(run, ()), *mine)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(self._entries)


def keep_ledger(
    files: Any,
    harness: Any,
    ledger: Ledger,
    *,
    summarised: bool,
    tool_results_route: str = TOOL_RESULTS_ROUTE,
) -> Steps[tuple[LedgerEntry, ...]]:
    """Keep this turn's entries, and say where each result is now.

    `summarised` is whether the turn ended on a summarised conversation.

    The ledger is written after every result file and before the conversation, so a
    row never names a missing file and a result the conversation shows always has a
    row. The sweep follows it, because until then a row may name a file it removes.
    """
    entries = yield from _repointed(files, ledger.entries)
    yield from write_ledger(
        harness,
        ledger.earlier,
        entries,
        turn=ledger.turn,
        resumed=ledger.resumed,
        summarised=summarised,
    )
    yield from _swept(
        files,
        ledger.earlier.paths | {e.path for e in entries if e.path},
        tool_results_route=tool_results_route,
    )
    return entries


def _offloaded_under(files: Any) -> str:
    """Where deepagents puts a result too long to show, read the way it reads it.

    `isinstance`, as deepagents asks: a backend that is no `CompositeBackend` evicts to
    `/` whatever attribute it carries.
    """
    root = files.artifacts_root if isinstance(files, CompositeBackend) else "/"
    return f"{root.rstrip('/')}/large_tool_results/"


def _repointed(files: Any, entries: tuple[LedgerEntry, ...]) -> Steps[tuple[LedgerEntry, ...]]:
    """Each entry deepagents offloaded, pointed at its copy rather than ours.

    Asked here, once, because eviction runs outside the recorder. Our own copy is left
    to the sweep, which removes it once no row names it.

    **Only onto a file that is this result.** An eviction is named by call id alone,
    which is unique only within a turn and not across an agent and its subagent, so a
    name is not an identity. The size decides: deepagents keeps the result with the id
    line this recorder appended, so the file is exactly that long. No size reported is
    no match, which keeps our copy -- safe, since it is this call's whole result.

    **On a tie, neither.** Two results of one length under one id cannot be told apart,
    and taking the first points a row at another call's answer and sweeps its own.
    """
    if not entries:
        return entries
    under = _offloaded_under(files)
    listing = yield reading(files, "ls", under)
    offloaded = {
        PurePosixPath(one["path"]).name: one.get("size")
        for one in listing.entries or ()
        if not one.get("is_dir")
    }
    def claimed(entry: LedgerEntry) -> str | None:
        """The offloaded file this row is the length of, or `None` for no match."""
        name = sanitize_tool_call_id(entry.call)
        whole = entry.size + len(f"\n[{entry.id}]")
        return f"{under}{name}" if offloaded.get(name) == whole else None

    # Counted over the whole turn before any row is pointed: one row cannot see a tie.
    claims = Counter(where for entry in entries if (where := claimed(entry)) is not None)
    return tuple(
        replace(entry, path=where)
        if (where := claimed(entry)) is not None and claims[where] == 1
        else entry
        for entry in entries
    )


def _swept(
    files: Any,
    named: frozenset[str] | set[str],
    *,
    tool_results_route: str = TOOL_RESULTS_ROUTE,
) -> Steps[None]:
    """Remove every result file a recorder wrote that no row names.

    Only a turn that died before writing its rows leaves one, beside our copies of
    repointed results. One turn runs at a time, so no running turn is about to name it.

    A recorder's own names rather than whatever the folder holds: the agent's file
    tools are refused this folder, but its shell is not.
    """
    listing = yield reading(files, "ls", tool_results_route)
    for one in listing.entries or ():
        if one.get("is_dir") or one["path"] in named:
            continue
        if not names_a_result(PurePosixPath(one["path"]).name):
            continue
        # Its answer is not checked: a file left behind is swept by the next turn.
        yield changing(files, "delete", one["path"])

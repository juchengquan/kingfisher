"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from .steps import Steps, on_host, reading
from .store import (
    KeptLedger,
    entry_id,
    expired,
    names_a_result,
    write_ledger,
)
from .unused import UnusedFiles, settle_unused

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.core.interfaces import UnusedResults
    from kingfisher.core.result import LedgerEntry, Listed


class Ledger:
    """What every graph one turn built recorded, gathered for the turn to keep.

    One per turn, handed to every recorder the build makes; the graph is per turn too.
    """

    def __init__(
        self,
        turn: str,
        earlier: KeptLedger,
        *,
        resumed: bool = False,
        keep_rounds: int | None = None,
    ) -> None:
        self.turn = turn
        #: Whether this turn answers a paused one, which its closing line says: a resume
        #: adds no user message, and the next one's delta reaches back past it.
        self.resumed = resumed
        #: As this turn writes it back, with every result older than `keep_rounds` exchanges
        #: expired; `expired` holds those entries as they were, naming the files to remove.
        #: Marked from the start, so clearing this turn hands no path the turn will remove.
        self.earlier, self.expired = expired(earlier, keep_rounds, turn=turn, resumed=resumed)
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
    harness: Any,
    ledger: Ledger,
    *,
    summarised: bool,
) -> Steps[tuple[LedgerEntry, ...]]:
    """Keep this turn's entries, and say where each result is now.

    `summarised` is whether the turn ended on a summarised conversation.

    The ledger is written after every result file and before the conversation, so a
    row never names a missing file and a result the conversation shows always has a
    row.
    """
    entries = ledger.entries
    yield from write_ledger(
        harness,
        ledger.earlier,
        entries,
        turn=ledger.turn,
        resumed=ledger.resumed,
        summarised=summarised,
    )
    return entries


def sweep_results(  # noqa: PLR0913 -- by keyword, so no route is passed for a session id
    files: Any,
    ledger: Ledger,
    entries: tuple[LedgerEntry, ...],
    *,
    tool_results_route: str,
    unused_results: UnusedResults,
    session_id: str,
) -> Steps[None]:
    """Hand `unused_results` the result files no row names, and remove the offloaded
    copies of expired ones, once the conversation is written.

    After the transcript, not the ledger: a file the sweep removes may still be named by
    the conversation the turn is about to write, and a crash between the two leaves that
    conversation naming a file that has gone.
    """
    yield from _swept(
        files,
        ledger.earlier.paths | {e.path for e in entries if e.path},
        tool_results_route=tool_results_route,
        unused_results=unused_results,
        session_id=session_id,
    )





def _swept(
    files: Any,
    named: frozenset[str] | set[str],
    *,
    tool_results_route: str,
    unused_results: UnusedResults,
    session_id: str,
) -> Steps[None]:
    """Hand over every result file a recorder wrote that no row names, and only those.

    Only a turn that died before writing its rows leaves one, beside the files of expired
    rows. One turn runs at a time, so no running turn is about to name it.

    A recorder's own names rather than whatever the folder holds: the agent's file
    tools are refused this folder, but its shell is not.
    """
    listing = yield reading(files, "ls", tool_results_route)
    unused = [
        (one["path"], one.get("size"))
        for one in listing.entries or ()
        if not one.get("is_dir")
        and one["path"] not in named
        and names_a_result(PurePosixPath(one["path"]).name)
    ]
    if unused:
        # On the host, and waited for by a cancel: the turn lets go of its claim only
        # after this, so no new turn can name a file while it is being dropped.
        yield on_host(
            settle_unused, unused_results, UnusedFiles(files, unused, session_id=session_id)
        )

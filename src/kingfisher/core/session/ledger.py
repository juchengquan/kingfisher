"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import bisect
import threading
from collections.abc import Sequence
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
        #: expired: no row names its file, so the sweep removes it. Marked from the start,
        #: so clearing this turn hands no path the turn will remove.
        self.earlier = expired(earlier, keep_rounds, turn=turn, resumed=resumed)
        # Calls made together run on threads, each with its own recorder.
        self._lock = threading.Lock()
        self._entries: list[LedgerEntry] = []
        #: How many numbers this turn has handed out, a row or not.
        self._reserved = 0
        #: The number each call took, by the message it was written in and its own id:
        #: whichever call of a batch arrives first numbers them all, and the rest read
        #: their own off it.
        self._numbers: dict[tuple[str, str], str] = {}

    def reserve(self, call: str, batch: Sequence[str], *, message: str) -> str:
        """The id `call`'s result will have, numbering every call of `batch` with it.

        `batch` is the recorded calls of `message`, the model's reply `call` was written
        in, in the order it wrote them. Numbered together, because calls made together
        reach here on threads in any order: taken one at a time as each arrives, a
        batch's ids run in the order its threads started, so `<ledger_delta>` lists them
        against the wrong calls and no two runs of one conversation number them the same
        way.

        The message as well as the call, because a model reuses a call id in a later
        reply and each of the two is a result of its own:
        `test_two_calls_of_one_id_in_a_turn_each_keep_their_own_result` holds that.

        Never handed out again, though the call may fail and leave no row: a middleware
        inside the recorder may already have named something after it.
        """
        with self._lock:
            if (message, call) not in self._numbers:
                for one in batch:
                    self._reserved += 1
                    self._numbers[message, one] = entry_id(self.earlier.last + self._reserved)
            return self._numbers[message, call]

    def add(self, entry: LedgerEntry) -> None:
        """Keep one result's row, under the id `reserve` gave its call.

        In id order, not the order results arrive: calls made together finish in any
        order, and a span of an exchange's ids is read off its first row and its last.
        """
        with self._lock:
            bisect.insort(self._entries, entry, key=_number)

    @property
    def reserved(self) -> str | None:
        """The highest id this turn handed out, or `None` if it handed out none."""
        with self._lock:
            return entry_id(self.earlier.last + self._reserved) if self._reserved else None

    def unkept(self, entry: LedgerEntry) -> LedgerEntry:
        """Mark one entry as having no file, because the backend would not take it, and
        hand back the entry as it now stands."""
        bare = replace(entry, path=None)
        with self._lock:
            self._entries[self._entries.index(entry)] = bare
        return bare

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


def _number(entry: LedgerEntry) -> int:
    # By number, not as text: `A-10000` sorts before `A-9999` as a string.
    return int(entry.id.removeprefix("A-"))


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
    reserved = ledger.reserved
    yield from write_ledger(
        harness,
        ledger.earlier,
        entries,
        turn=ledger.turn,
        resumed=ledger.resumed,
        summarised=summarised,
        # Said only where no row says it already: past every row, it is a number the next
        # turn would otherwise hand out a second time.
        reserved=None if entries and entries[-1].id == reserved else reserved,
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
    """Hand `unused_results` the result files no row names, once the conversation is
    written.

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

"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend
from deepagents.backends.utils import sanitize_tool_call_id

from kingfisher.infrastructure.session_store import (
    KeptLedger,
    entry_id,
    names_a_result,
    write_ledger,
)
from kingfisher.infrastructure.steps import Steps, changing, reading
from kingfisher.layout import TOOL_RESULTS_ROUTE

if TYPE_CHECKING:
    from collections.abc import Callable

    from kingfisher.domain.result import LedgerEntry, Listed


class Ledger:
    """What every graph one turn built recorded, gathered for the turn to keep.

    One per turn, handed to every recorder the build makes, and never outliving the
    turn: the graph is built per turn too.
    """

    def __init__(self, turn: str, earlier: KeptLedger, *, resumed: bool = False) -> None:
        self.turn = turn
        self.earlier = earlier
        #: Whether this turn answers a paused one. Its closing line says so, because a
        #: resume adds no user message and the next one's delta reaches back past it.
        self.resumed = resumed
        # Calls made together run on threads, each with its own recorder.
        self._lock = threading.Lock()
        self._entries: list[LedgerEntry] = []

    def add(self, row: Callable[[str], LedgerEntry]) -> LedgerEntry:
        """Number one result and keep its row, as one step.

        `row` builds the entry from the id it is given. Numbered by position, so two
        results arriving together are two numbers only because nothing else is
        numbered between the count and the append.
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
    files: Any, harness: Any, ledger: Ledger, *, summarised: bool
) -> Steps[tuple[LedgerEntry, ...]]:
    """Keep this turn's entries, and say where each result is now.

    `summarised` is whether the turn ended on a summarised conversation, which only
    its final state says.

    The ledger is written after every result file and before the conversation, so a
    row never names a missing file and a result the conversation shows always has a
    row. The sweep comes after it, because a file it removes may be named by a row
    until the ledger is written.
    """
    entries = yield from _repointed(files, ledger.entries, ledger.earlier.paths)
    yield from write_ledger(
        harness,
        ledger.earlier,
        entries,
        turn=ledger.turn,
        resumed=ledger.resumed,
        summarised=summarised,
    )
    yield from _swept(files, ledger.earlier.paths | {e.path for e in entries if e.path})
    return entries


def _offloaded_under(files: Any) -> str:
    """Where deepagents puts a result too long to show, read the way it reads it.

    `isinstance`, as it asks: a backend that is no `CompositeBackend` has its
    evictions at `/` whatever attribute it carries.
    """
    root = files.artifacts_root if isinstance(files, CompositeBackend) else "/"
    return f"{root.rstrip('/')}/large_tool_results/"


def _repointed(
    files: Any, entries: tuple[LedgerEntry, ...], claimed: frozenset[str] | set[str]
) -> Steps[tuple[LedgerEntry, ...]]:
    """Each entry deepagents offloaded, pointed at its copy rather than ours.

    Asked here, once, because eviction runs outside the recorder and nothing it can
    see says whether a result will be. Our own copy is left to the sweep, which
    removes it once the ledger has stopped naming it.

    Never onto a path a row already names, and never twice in one turn. deepagents
    names an eviction `large_tool_results/<call id>` -- no turn in the name, and the
    folder outlasts the turn -- so an id a provider used twice pointed the second row
    at the first result's file, and the sweep then removed the copy that was right.
    Every other call id here need only be unique within its turn, and nothing asks a
    provider for more than that. A collision keeps our own copy instead, which is this
    call's whole result rather than another call's.
    """
    if not entries:
        return entries
    under = _offloaded_under(files)
    listing = yield reading(files, "ls", under)
    offloaded = {
        PurePosixPath(one["path"]).name for one in listing.entries or () if not one.get("is_dir")
    }
    taken = set(claimed)
    pointed: list[LedgerEntry] = []
    for entry in entries:
        where = f"{under}{sanitize_tool_call_id(entry.call)}"
        if sanitize_tool_call_id(entry.call) in offloaded and where not in taken:
            taken.add(where)
            pointed.append(replace(entry, path=where))
        else:
            pointed.append(entry)
    return tuple(pointed)


def _swept(files: Any, named: frozenset[str] | set[str]) -> Steps[None]:
    """Remove every result file a recorder wrote that no row names.

    Only a turn that died before writing its rows leaves one, beside the copies of
    results repointed above. One turn runs at a time, so a file without a row is never
    one a turn still running is about to name.

    A recorder's own names rather than whatever the folder holds, because the agent is
    a writer here too: `layout.py` puts the results inside the scratchpad so it can
    read and grep them, and nothing refuses it the folder. Sweeping everything deleted
    the agent's file in the turn that wrote it, after the tool had told it the write
    succeeded.
    """
    listing = yield reading(files, "ls", TOOL_RESULTS_ROUTE)
    for one in listing.entries or ():
        if one.get("is_dir") or one["path"] in named:
            continue
        if not names_a_result(PurePosixPath(one["path"]).name):
            continue
        # Its answer is not checked: a file left behind is swept by the next turn.
        yield changing(files, "delete", one["path"])

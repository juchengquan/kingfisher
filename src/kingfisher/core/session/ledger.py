"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from deepagents.backends import CompositeBackend
from deepagents.backends.utils import sanitize_tool_call_id

from kingfisher.core.session.steps import Steps, changing, reading
from kingfisher.core.session.store import (
    KeptLedger,
    entry_id,
    names_a_result,
    write_ledger,
)
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
    files: Any,
    harness: Any,
    ledger: Ledger,
    *,
    summarised: bool,
    tool_results_route: str = TOOL_RESULTS_ROUTE,
) -> Steps[tuple[LedgerEntry, ...]]:
    """Keep this turn's entries, and say where each result is now.

    `summarised` is whether the turn ended on a summarised conversation, which only
    its final state says.

    The ledger is written after every result file and before the conversation, so a
    row never names a missing file and a result the conversation shows always has a
    row. The sweep comes after it, because a file it removes may be named by a row
    until the ledger is written.
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

    `isinstance`, as it asks: a backend that is no `CompositeBackend` has its
    evictions at `/` whatever attribute it carries.
    """
    root = files.artifacts_root if isinstance(files, CompositeBackend) else "/"
    return f"{root.rstrip('/')}/large_tool_results/"


def _repointed(files: Any, entries: tuple[LedgerEntry, ...]) -> Steps[tuple[LedgerEntry, ...]]:
    """Each entry deepagents offloaded, pointed at its copy rather than ours.

    Asked here, once, because eviction runs outside the recorder and nothing it can
    see says whether a result will be. Our own copy is left to the sweep, which
    removes it once the ledger has stopped naming it.

    **Only onto a file that is this result.** deepagents names an eviction
    `large_tool_results/<call id>` -- no turn in the name, and the folder outlasts the
    turn -- so a name is not an identity, and asking only whether a file of that name
    existed pointed a row at another call's result while the sweep removed the copy
    that was right. An id need only be unique within its turn for everything else
    here, and nothing asks a provider for more than that; a turn holding an agent and
    a subagent holds two conversations, and a provider numbering calls per response
    collides there by construction.

    The size is what tells them apart: deepagents keeps the result with the id line
    this recorder put on the end, so the file is exactly that long and the listing
    already carries the number. A backend that reports no size is read as no match,
    which keeps our own copy -- the safe direction, since our copy is this call's whole
    result either way.

    **On a tie, neither.** The size does not settle two results of one length under one
    id -- the same tool called twice -- and nothing here can: the listing carries a
    name and a length, and both rows have both. Taking the first was measured pointing
    a row that records `subject='one'` at the bytes of `subject='two'`, with the `one`
    result then swept and gone from the session altogether, under a row still carrying
    its size and its summary. `system.md` tells the model to read that file back rather
    than call the tool again, so what it reads is another call's answer with nothing
    saying so. Neither row claiming it keeps both results, each whole, in our own
    copies -- the direction the no-size case above already takes, and for the same
    reason: our copy is this call's whole result either way.

    It took the paths earlier rows named as well, until the size made that redundant:
    a row from a previous turn and a row from this one cannot both match a file one of
    them is the length of, unless they are the same length -- and then deepagents has
    overwritten the file, so this turn's row pointing at it is right and the earlier
    one was already stale. Two guards, each held by a rule of its own; a third that
    nothing could fail is how a guard stops being read.
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

    # Counted over the whole turn before any row is pointed, because the tie is the
    # question "how many rows could this be" and a row cannot answer it alone.
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

    Only a turn that died before writing its rows leaves one, beside the copies of
    results repointed above. One turn runs at a time, so a file without a row is never
    one a turn still running is about to name.

    A recorder's own names rather than whatever the folder holds, because the agent is
    a writer here too. Its *file tools* are refused this folder now, so what the
    narrowing earns is the shell: `execute` bypasses filesystem permissions, and
    sweeping everything deleted what a shell left here in the turn that wrote it. It
    said "nothing refuses it the folder", which stopped being true the day the route
    gained its deny rule.
    """
    listing = yield reading(files, "ls", tool_results_route)
    for one in listing.entries or ():
        if one.get("is_dir") or one["path"] in named:
            continue
        if not names_a_result(PurePosixPath(one["path"]).name):
            continue
        # Its answer is not checked: a file left behind is swept by the next turn.
        yield changing(files, "delete", one["path"])

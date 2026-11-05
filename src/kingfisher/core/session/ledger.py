"""One turn's record of what its tools returned, and keeping it once the turn is over."""

from __future__ import annotations

import bisect
import threading
from collections.abc import Sequence
from dataclasses import replace
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from kingfisher.core.agent.middlewares.recorded import filed_at, kept_text, stamped_id, unfiled
from kingfisher.core.logged import warn
from kingfisher.core.result import ResultError

from .steps import Steps, changing, on_host, reading
from .store import (
    HarnessFiles,
    KeptLedger,
    entry_id,
    expired,
    names_a_result,
    patched,
    read_ledger,
    read_transcript,
    write_ledger,
)
from .transcript import from_record
from .turnlog import read_turn_log
from .unused import UnusedFiles, settle_unused

if TYPE_CHECKING:
    from kingfisher.core.interfaces import UnusedResults
    from kingfisher.core.result import LedgerEntry, Listed

#: One result to file: its id, where it goes, and what the file holds.
Filing = tuple[str, str, bytes]


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
        #: so clearing this turn hands no path the turn will remove. An earlier result
        #: this turn files is patched in as it is filed.
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
        #: The access of what ran under a run with no row of its own: a call from code, or
        #: inside a compiled subagent, which the answer it feeds can repeat.
        self._unrecorded: dict[str, list[Listed]] = {}
        #: A subagent's results not yet filed, by id, each with the run it belongs to.
        self._held: dict[str, tuple[str | None, str, bytes]] = {}

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
        bare = replace(entry, path=None, unkept=True)
        with self._lock:
            self._entries[self._entries.index(entry)] = bare
        return bare

    def row(self, number: str) -> LedgerEntry | None:
        """The row of result `number`, this turn's or an earlier one's, as it now stands."""
        with self._lock:
            mine = next((one for one in self._entries if one.id == number), None)
            earlier = self.earlier
        return mine if mine is not None else earlier.row(number)

    def hold(self, entry: LedgerEntry, path: str, content: bytes) -> None:
        """Keep a subagent's result to file at `path` when its run ends, when the
        conversation holding it goes."""
        with self._lock:
            self._held[entry.id] = (entry.run, path, content)

    def held(self, run: str | None = None) -> tuple[Filing, ...]:
        """What `hold` kept that nothing has filed since: `run`'s, or every run's."""
        with self._lock:
            return tuple(
                (number, path, content)
                for number, (owner, path, content) in self._held.items()
                if run is None or owner == run
            )

    def filed(self, wanted: Sequence[Filing], answers: Any, *, lose: bool) -> frozenset[str]:
        """Note what a save of `wanted` answered, and hand back the ids it kept.

        `answers` is what `upload_files` answered, or what it raised. A result it refused
        is marked unkept where `lose` -- its last copy is going -- and is otherwise left as
        it was, for a later save to try. Logged, never raised: the tool has run, and a
        failure here is one the model may answer by running it twice.
        """
        failed = (
            dict.fromkeys((number for number, _, _ in wanted), unfiled(answers))
            if isinstance(answers, BaseException)
            else {
                number: unfiled(answer)
                for (number, _, _), answer in zip(wanted, answers, strict=True)
                if answer.error
            }
        )
        for number, path, _ in wanted:
            if number in failed:
                warn(
                    "result_not_filed",
                    f"result {number} was not filed at {path}: {failed[number]}",
                    result=number,
                    path=path,
                    reason=failed[number],
                )
        kept = {number: path for number, path, _ in wanted if number not in failed}
        changes: dict[str, dict[str, Any]] = {n: {"path": p} for n, p in kept.items()}
        if lose:
            changes.update({n: {"path": None, "unkept": True} for n in failed})
        with self._lock:
            for number in (*kept, *(failed if lose else ())):
                self._held.pop(number, None)
            for at, entry in enumerate(self._entries):
                if (change := changes.pop(entry.id, None)) is not None:
                    self._entries[at] = replace(entry, **change)
            # A result an earlier turn recorded, filed as it leaves this turn's conversation:
            # its line names the file, or the sweep removes it.
            self.earlier = patched(self.earlier, changes)
        return frozenset(kept)

    def ran(self, run: str, access: Listed) -> None:
        """Note what a call that gets no row read under one run."""
        with self._lock:
            self._unrecorded.setdefault(run, []).append(access)

    def access_in(self, run: str) -> tuple[Listed, ...]:
        """The access of everything read under one run: every entry recorded under it,
        before this turn as well, and every call this turn noted with no row."""
        with self._lock:
            mine = tuple(
                e.access for e in self._entries if e.run == run and e.access is not None
            )
            unrecorded = tuple(self._unrecorded.get(run, ()))
        return (*self.earlier.runs.get(run, ()), *mine, *unrecorded)

    @property
    def entries(self) -> tuple[LedgerEntry, ...]:
        with self._lock:
            return tuple(self._entries)


def _number(entry: LedgerEntry) -> int:
    # By number, not as text: `A-10000` sorts before `A-9999` as a string.
    return int(entry.id.removeprefix("A-"))


def file_left(
    files: Any,
    ledger: Ledger,
    away: Sequence[Any],
    *,
    tool_results_route: str,
) -> Steps[None]:
    """File, in one save, what left the conversation this turn with no file: each result
    behind the summary's cut, `away`, and a subagent's results its run did not file.

    Before the ledger is written, so its rows name the files. Only now for `away`: the
    whole of each is held in state until the turn ends.
    """
    wanted = list(ledger.held())
    for message in away:
        if (call := getattr(message, "tool_call_id", None)) is None:
            continue
        text = str(getattr(message, "text", ""))
        row = ledger.row(number) if (number := stamped_id(text)) is not None else None
        if row is None or row.call != call or row.path or row.expired_in or row.unkept:
            continue
        if (content := kept_text(text, row.id, row.size)) is not None:
            path = filed_at(tool_results_route, row.id, row.tool, content)
            wanted.append((row.id, path, content))
    if not wanted:
        return
    try:
        answers = yield changing(files, "upload_files", [(path, held) for _, path, held in wanted])
    except Exception as exc:  # noqa: BLE001 -- a save must never cost the turn its record
        answers = exc
    ledger.filed(wanted, answers, lose=True)


def keep_ledger(
    harness: HarnessFiles,
    ledger: Ledger,
    *,
    summarised: bool,
    messages: int | None = None,
) -> Steps[tuple[LedgerEntry, ...]]:
    """Keep this turn's entries, and say where each result is now.

    `summarised` is whether the turn ended on a summarised conversation, and `messages`
    how many messages the conversation it is about to write holds.

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
        messages=messages,
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
    """Hand `unused_results` every result file a recorder wrote that no row names, and only
    those, once the conversation is written.

    After the transcript, not the ledger: a file the sweep removes may still be named by
    the conversation the turn is about to write, and a crash between the two leaves that
    conversation naming a file that has gone.

    Only a turn that died before writing its rows leaves one, beside the files of expired
    rows. One turn runs at a time, so no running turn is about to name it.

    A recorder's own names rather than whatever the folder holds: the agent's file
    tools are refused this folder, but its shell is not.
    """
    named = ledger.earlier.paths | {e.path for e in entries if e.path}
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


def read_result(files: Any, number: str) -> Steps[str]:
    """Result `number`'s text, as its tool returned it: from its file where its row names
    one, else from the conversation, which holds it whole until it is filed -- an expired
    one too, until it leaves -- else from the turn log, which keeps it as first written
    once a rewind has left its turn behind.

    `ResultError` for an id no row has, and for a result kept nowhere.
    """
    row = (yield from read_ledger(HarnessFiles(files))).row(number)
    if row is None:
        msg = f"no result {number} in this session"
        raise ResultError(msg)
    if row.path is not None:
        (answer,) = yield reading(files, "download_files", [row.path])
        if answer.error or answer.content is None:
            msg = f"{number}: {answer.error or 'no content'} at {row.path}"
            raise ResultError(msg)
        return answer.content.decode("utf-8")
    said, _ = yield from read_transcript(HarnessFiles(files))
    logged = [
        from_record(line)
        for line in (yield from read_turn_log(HarnessFiles(files)))
        if line.get("turn") == row.turn and line.get("call_id") == row.call
    ]
    for message in (*said, *logged):
        if message.call_id == row.call and (
            content := kept_text(message.content, row.id, row.size)
        ) is not None:
            return content.decode("utf-8")
    if row.expired_in is not None:
        why = f"it expired in turn {row.expired_in}"
    elif row.unkept:
        why = "the session's backend refused its file"
    else:
        why = "it has no file"
    msg = f"{number} is kept nowhere: {why}, and the conversation no longer holds it whole"
    raise ResultError(msg)

"""The turn log kept as two files: the recent turns, which an ordinary turn reads and
writes, and the settled ones, which only a split, a rewind behind it and a listing read."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher import Kingfisher
from kingfisher.core.agent.middlewares.result_rows import expired_handle
from kingfisher.core.layout import HARNESS, RECENT_TURNS_FILE, TURNS_FILE
from kingfisher.core.session import turnlog
from kingfisher.core.session.store import SECOND_COPY, HarnessFiles
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, read_conversation, read_record, read_turns
from tests.unit.agent.middlewares.test_clearing import _one
from tests.unit.scripted import Scripted
from tests.unit.session.conftest import _calls, _say, _searches
from tests.unit.session.test_turn_log import _by_call, _ended_on, _handed_first


def _harness(cfg):
    return cfg.workspace / "sessions" / "s" / HARNESS


def _tasks(kf: Kingfisher) -> dict[str, str | None]:
    return {turn.id: turn.task for turn in kf.session("s").turns()}


@pytest.fixture
def fetched(monkeypatch) -> list[str]:
    """Each `.harness` file read, by name, in order."""
    names: list[str] = []
    original = HarnessFiles.fetch

    def fetch(self: HarnessFiles, name: str) -> Any:
        names.append(name)
        return (yield from original(self, name))

    monkeypatch.setattr(HarnessFiles, "fetch", fetch)
    return names


@pytest.fixture
def splitting(monkeypatch):
    """Every turn past the size a split waits for, so each one splits the log."""
    monkeypatch.setattr(turnlog, "SPLIT_BYTES", 0)


def _settled_by_summaries(cfg) -> tuple[Kingfisher, str]:
    """Three turns, two summarising, which settle the first; and a kingfisher whose turns
    summarise nothing more, since a turn that does splits, which reads both files."""
    summarising = "[{name: summarize-by-count, settings: {messages: 12}}]"
    an_agent(cfg, middlewares=summarising, **EVERY_TOOL)
    kf = Kingfisher(
        cfg, sessions=LocalSessions(), middlewares={"summarize-by-count": SummarizeByCount}
    )
    first = _say(kf, "dig", *_searches("a", 10), AIMessage("one"))
    _say(kf, "more", AIMessage("two"))
    _say(kf, "deeper", *_searches("b", 10), AIMessage("three"))
    settled = read_record(_harness(cfg) / TURNS_FILE)
    assert first.turn_id in settled, "the control: no summary settled the first turn"
    an_agent(cfg, **EVERY_TOOL)
    return Kingfisher(cfg, sessions=LocalSessions()), first.turn_id


def test_a_turn_after_a_summary_reads_the_recent_file_alone(logged, fetched):
    """The point of the split: read whole, the log costs a remote session a transfer of
    everything ever said on every turn. And a recent file that cannot rebuild the
    conversation alone hands the next turn a shorter one without a word."""
    kf, first = _settled_by_summaries(logged)
    rebuilt, at = read_conversation(_harness(logged)), len(Scripted.heard)
    fetched.clear()

    _say(kf, "and now?", AIMessage("four"))

    assert TURNS_FILE not in fetched
    assert RECENT_TURNS_FILE in fetched, "the control: the log was not read at all"
    assert _handed_first(at) == rebuilt
    assert _tasks(kf)[first] == "dig", "a settled turn lost its task in the listing"


def test_a_turn_that_read_the_settled_file_leaves_the_next_one_the_recent_file(
    logged, fetched
):
    """After a rewind to a settled turn, the next turn reads both files to rebuild it; one
    that leaves the recent file unable to rebuild the turn after it alone has every turn on
    that branch read the settled file too."""
    kf, first = _settled_by_summaries(logged)
    kf.session("s").rewind(first)
    _say(kf, "again", AIMessage("four"))
    assert TURNS_FILE in fetched, "the control: the turn after the rewind read one file"
    rebuilt, at = read_conversation(_harness(logged)), len(Scripted.heard)
    fetched.clear()

    _say(kf, "once more", AIMessage("five"))

    assert TURNS_FILE not in fetched
    assert _handed_first(at) == rebuilt


def test_a_rewind_behind_a_split_rebuilds_the_turn_from_the_settled_file(logged, splitting):
    """Past the size a split waits for, every turn but the one writing settles; a rewind to
    one of them rebuilt from the recent file alone hands the next turn the conversation
    without that turn's messages."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    one = AIMessage("one")
    first = _say(kf, "first", _calls(("web_search", "q", "c1")), one)
    ended = _ended_on(one)
    _say(kf, "second", AIMessage("two"))
    _say(kf, "third", AIMessage("three"))
    assert '"c1"' in read_record(_harness(logged) / TURNS_FILE), "the control: nothing settled"
    kf.session("s").rewind(first.turn_id)

    at = len(Scripted.heard)
    _say(kf, "fourth", AIMessage("four"))

    assert _handed_first(at) == ended


def test_a_log_kept_in_one_file_is_read_as_it_is_and_split_without_a_change(logged):
    """A session from before the split keeps its log in `turns.jsonl` alone; read from the
    new file only, it loses every turn it ran."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    first = _say(kf, "first", _calls(("web_search", "q", "c1")), AIMessage("one"))
    _say(kf, "second", AIMessage("two"))
    harness = _harness(logged)
    for name in (RECENT_TURNS_FILE, f"{RECENT_TURNS_FILE}{SECOND_COPY}"):
        (harness / name).rename(harness / name.replace(RECENT_TURNS_FILE, TURNS_FILE))
    before = read_turns(harness).view
    rebuilt, at = read_conversation(harness), len(Scripted.heard)

    _say(kf, "third", AIMessage("three"))

    assert _handed_first(at) == rebuilt
    assert (harness / RECENT_TURNS_FILE).exists()
    assert _tasks(kf)[first.turn_id] == "first"
    kept = [line for line in read_turns(harness).view if line in before]
    assert kept == list(before), "a line of a turn from before the split changed"


def test_a_split_dying_between_its_two_writes_loses_no_turn(logged, splitting, monkeypatch):
    """A split writes both files. Shrinking the recent file before the settled one holds
    what it dropped, a turn dying between the two loses the lines of every turn moved."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    first = _say(kf, "first", AIMessage("one"))
    second = _say(kf, "second", AIMessage("two"))
    rebuilt = read_conversation(_harness(logged))
    original = HarnessFiles.store
    stored: list[str] = []

    def store(self: HarnessFiles, name: str, content: bytes) -> Any:
        if name in (TURNS_FILE, RECENT_TURNS_FILE):
            stored.append(name)
            # The split's second write, whichever file it is.
            if len(stored) > 1:
                msg = "the backend went away"
                raise OSError(msg)
        return (yield from original(self, name, content))

    with monkeypatch.context() as patched:
        patched.setattr(HarnessFiles, "store", store)
        _say(kf, "third", AIMessage("three"))
    assert set(stored) == {TURNS_FILE, RECENT_TURNS_FILE}, "the control: the turn did not split"

    assert read_conversation(_harness(logged)) == rebuilt
    tasks = _tasks(kf)
    assert (tasks[first.turn_id], tasks[second.turn_id]) == ("first", "second")


def test_a_result_that_can_still_expire_settles_only_once_swapped(clearing, monkeypatch):
    """Settled whole, a result that expires later is never swapped for its handle there:
    `KINGFISHER_KEEP_RESULTS_ROUNDS` no longer bounds what the session keeps, and a rewind
    to that turn hands the model a result that has expired. Nor is it left whole in the
    conversation the recent file starts from, by a turn that does not split."""
    kf = Kingfisher(replace(clearing, keep_results_rounds=1), sessions=LocalSessions())
    with monkeypatch.context() as patched:
        patched.setattr(turnlog, "SPLIT_BYTES", 0)
        first = _say(kf, "first", _one("bulky", 1, n=1), AIMessage("done"))
        _say(kf, "second", AIMessage("two"))
    assert "row 1-0100" in read_record(_harness(clearing) / RECENT_TURNS_FILE), (
        "the control: the result was swapped before the turn could settle"
    )

    _say(kf, "third", *(_one("bulky", n, n=n) for n in range(2, 8)), AIMessage("done"))

    (row,) = first.ledger
    assert _by_call(clearing)["c1"] == expired_handle(replace(row, expired_in="any"))
    # Not settled yet, and so no settled file: its turn kept a result that could change.
    for kept in (_harness(clearing) / TURNS_FILE, _harness(clearing) / RECENT_TURNS_FILE):
        assert not kept.exists() or "row 1-0100" not in read_record(kept), kept.name

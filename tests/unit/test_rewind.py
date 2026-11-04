"""Rewinding a session: its conversation, the results its next turn is told of, and what
it heard of its inputs folder put back where an earlier turn left them, with the turns
after it kept.

Through real turns -- a scripted model, kingfisher's own build, deepagents' own
summariser -- because what a rewind rebuilds is what those turns wrote, and a log written
here would hold only what the test expected.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher import START, Kingfisher, RewindError, SessionBusyError, TurnIdError
from kingfisher.cli.__main__ import main
from kingfisher.core.layout import (
    HARNESS,
    INPUTS_SEEN,
    LEDGER_FILE,
    PAUSED_MARK,
    TRANSCRIPT_FILE,
)
from kingfisher.core.request import Decision, DecisionError
from kingfisher.core.result import AWAITING
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted
from tests.unit.test_turn_log import TOOLS, _calls, _resume, _say, _searches, _transcript


@pytest.fixture
def rewinding(scripted):
    """An agent with a search tool and a gated query, and the session `s`."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, interrupt_on="[sql_query]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _sent(index: int = 0) -> str:
    """Everything the model was handed on one call of the turn just run."""
    return "\n".join(str(message.content) for message in Scripted.heard[index])


def _harness_files(cfg) -> dict:
    harness = cfg.workspace / "sessions" / "s" / HARNESS
    return {path: path.read_bytes() for path in harness.rglob("*") if path.is_file()}


def test_a_rewind_puts_the_conversation_back_and_the_next_turn_carries_on_from_it(rewinding):
    """Left at the transcript as it was, the next turn answers from turns the caller took
    back; following the last turn the log holds, its record hangs off a turn its model
    never saw; told of an abandoned turn's results, the model cites them; and a result
    number given again would name two results."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    after_first = _transcript(rewinding)
    _say(kf, "TASK-SECOND", AIMessage("ANSWER-SECOND"))
    # Last, so its results are what the next turn would be told of were it still followed.
    third = _say(kf, "TASK-THIRD", _calls(("web_search", "q", "c1")), AIMessage("ANSWER-THIRD"))
    assert [entry.id for entry in third.ledger] == ["A-0001"]

    done = kf.session("s").rewind(first.turn_id)

    assert done.to == first.turn_id
    assert _transcript(rewinding) == after_first
    Scripted.heard.clear()
    fourth = _say(kf, "TASK-FOURTH", _calls(("web_search", "r", "c2")), AIMessage("ok"))
    sent = _sent()
    assert "ANSWER-FIRST" in sent
    assert not any(word in sent for word in ("TASK-SECOND", "ANSWER-THIRD", "A-0001"))
    assert [entry.id for entry in fourth.ledger] == ["A-0002"]
    turns = kf.session("s").turns()
    assert {turn.id: turn.parent for turn in turns}[fourth.turn_id] == first.turn_id
    assert [turn.id for turn in turns if turn.current] == [first.turn_id, fourth.turn_id]
    # A turn left behind still holds its id.
    with pytest.raises(TurnIdError):
        _say(kf, "again", turn_id=third.turn_id)


def test_a_rewind_to_before_a_summary_brings_back_what_the_summary_replaced(rewinding):
    """Rebuilt from the transcript, a turn before the last summary has nothing left to
    rebuild: its words are behind the summary, in a file the agent can write."""
    an_agent(
        rewinding,
        interrupt_on="[sql_query]",
        middlewares="[{name: summarize-by-count, settings: {messages: 12}}]",
        **EVERY_TOOL,
    )
    kf = Kingfisher(
        rewinding, sessions=LocalSessions(), middlewares={"summarize-by-count": SummarizeByCount}
    )
    first = _say(kf, "TASK-EARLY", *_searches("a", 3), AIMessage("ANSWER-EARLY"))
    after_first = _transcript(rewinding)
    _say(kf, "TASK-LATER", *_searches("b", 4), AIMessage("ANSWER-LATER"))
    assert Scripted.summaries >= 1, "the control: the second turn never summarised"
    assert _transcript(rewinding)[0].summary, "the control: the summary replaced nothing"

    kf.session("s").rewind(first.turn_id)

    assert _transcript(rewinding) == after_first


def test_a_rewind_returns_to_a_turn_it_left_behind(rewinding):
    """Deleted at the first rewind, the turns after it could never be returned to, and a
    rewind made by mistake would lose them for good."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    second = _say(kf, "TASK-SECOND", AIMessage("ANSWER-SECOND"))
    third = _say(kf, "TASK-THIRD", AIMessage("ANSWER-THIRD"))
    after_third = _transcript(rewinding)
    kf.session("s").rewind(first.turn_id)
    _say(kf, "TASK-OTHER", AIMessage("ANSWER-OTHER"))

    kf.session("s").rewind(third.turn_id)

    assert _transcript(rewinding) == after_third
    assert [turn.id for turn in kf.session("s").turns() if turn.current] == [
        first.turn_id,
        second.turn_id,
        third.turn_id,
    ]
    Scripted.heard.clear()
    _say(kf, "TASK-NEXT", AIMessage("ok"))
    assert "ANSWER-THIRD" in _sent()
    assert "ANSWER-OTHER" not in _sent()


def test_a_rewind_to_the_start_leaves_no_conversation(rewinding):
    """A turn after it that still found the old transcript, or followed the old turns,
    would be answered from a conversation the caller emptied."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))

    done = kf.session("s").rewind(START)

    assert done.to is None
    assert not (rewinding.workspace / "sessions" / "s" / HARNESS / TRANSCRIPT_FILE).exists()
    assert not any(turn.current for turn in kf.session("s").turns())
    Scripted.heard.clear()
    fresh = _say(kf, "TASK-FRESH", AIMessage("ok"))
    assert "ANSWER-FIRST" not in _sent()
    assert {turn.id: turn.parent for turn in kf.session("s").turns()}[fresh.turn_id] is None


def test_a_rewind_discards_the_pause_it_finds(rewinding):
    """Kept, the pause would let a resume run calls asked for in a conversation the
    session is no longer at."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    paused = _say(kf, "query it", _calls(("sql_query", "x", "g1")))
    assert paused.stop_reason == AWAITING, "the control: the turn never stopped at the gate"

    done = kf.session("s").rewind(first.turn_id)

    assert [one.tool for one in done.discarded] == ["sql_query"]
    assert not (rewinding.workspace / "sessions" / "s" / HARNESS / PAUSED_MARK).exists()
    with pytest.raises(DecisionError, match="not waiting"):
        _resume(kf, Decision(call_id=paused.pending[0].call_id, action="approve"))


def test_a_refused_rewind_changes_nothing(rewinding):
    """A rewind writing before its refusals would leave a session half on one branch and
    half on another: a turn the session never ran named, or a turn already holding it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    kept = _harness_files(rewinding)

    with pytest.raises(RewindError, match="no turn"):
        kf.session("s").rewind("t-never")
    held = LocalSessions().claim(rewinding, "s", stale_after=3600)
    assert held is not None
    with pytest.raises(SessionBusyError):
        kf.session("s").rewind(START)
    LocalSessions().release(rewinding, "s", held)

    assert _harness_files(rewinding) == kept


def test_a_result_left_on_a_branch_is_still_read_by_its_id(rewinding):
    """Out of the conversation and never filed, a result a rewind left behind was kept
    nowhere a caller could read it, though its row still named it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "look", _calls(("web_search", "q", "c1")), AIMessage("found"))

    kf.session("s").rewind(START)

    assert kf.session("s").result("A-0001") == "Ten results for q"


def test_a_result_left_on_a_branch_still_ages(rewinding):
    """Counted along the path only, a result a rewind left behind never expired, and
    `KINGFISHER_KEEP_RESULTS_ROUNDS` bounded nothing once a session had rewound."""
    cfg = replace(rewinding, keep_results_rounds=1)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    _say(kf, "look", _calls(("web_search", "q", "c1")), AIMessage("found"))
    kf.session("s").rewind(START)

    later = _say(kf, "something else", AIMessage("ok"))

    ledger = cfg.workspace / "sessions" / "s" / HARNESS / LEDGER_FILE
    (row,) = [one for one in map(json.loads, ledger.read_text().splitlines()) if one.get("id")]
    assert row["expired_in"] == later.turn_id


def test_what_the_agent_heard_of_its_inputs_goes_back_with_the_conversation(rewinding, tmp_path):
    """Left as the latest turn heard it, the inputs folder a rewound conversation never
    saw change would not be reported as changed, and the model would answer from a file
    it never read."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (tmp_path / "a.csv").write_text("a", encoding="utf-8")
    (tmp_path / "b.csv").write_text("b", encoding="utf-8")
    first = _say(kf, "read a", AIMessage("ok"), inputs=(tmp_path / "a.csv",))
    heard = (rewinding.workspace / "sessions" / "s" / HARNESS / INPUTS_SEEN).read_bytes()
    _say(kf, "read b", AIMessage("ok"), inputs=(tmp_path / "b.csv",))

    kf.session("s").rewind(first.turn_id)

    assert (rewinding.workspace / "sessions" / "s" / HARNESS / INPUTS_SEEN).read_bytes() == heard
    Scripted.heard.clear()
    _say(kf, "what is new?", AIMessage("ok"))
    assert "b.csv" in _sent()


def test_the_command_lists_turns_and_rewinds(rewinding, at_the_command_line, capsys):
    """The verbs a person without a library rewinds with: listing the turns marks where
    the session is, and rewinding by name moves it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("one"))
    _say(kf, "TASK-SECOND", AIMessage("two"))

    assert main(["rewind", "--session", "s", "--to", first.turn_id]) == 0
    assert main(["turns", "--session", "s"]) == 0

    printed = capsys.readouterr().out
    assert f"where turn {first.turn_id} ended" in printed
    marked = [line for line in printed.splitlines() if line.startswith("*")]
    assert len(marked) == 1
    assert first.turn_id in marked[0]

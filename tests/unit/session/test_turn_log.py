"""The turn log: every turn's messages as first written, the only record of what was said,
which every turn's conversation is rebuilt from.

Through real turns -- a scripted model, kingfisher's own build, deepagents' own
summariser -- because what a turn added is read off the state a real graph leaves, and a
state built here would only hold what the test thought it would. The rebuild is held to
what the model was handed, never to another file kingfisher wrote: a check against its
own output would pass with both wrong in the same way.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import replace
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.result_rows import expired_handle
from kingfisher.core.layout import HARNESS, LEDGER_FILE, RECENT_TURNS_FILE
from kingfisher.core.request import Decision, DecisionError, Resume
from kingfisher.core.result import AWAITING, END_TURN
from kingfisher.core.session.messages import Message
from kingfisher.core.session.runtime import as_messages
from kingfisher.core.session.turnlog import (
    ACTION,
    ANSWER,
    AT,
    CUT,
    ENDED,
    PARENT,
    PARTS,
    REASONING,
    STARTED,
    STOP_REASON,
    _rewritten,
)
from kingfisher.plugins import LocalSessions
from tests.conftest import (
    EVERY_TOOL,
    RecordedEvents,
    an_agent,
    read_conversation,
    read_record,
    read_turns,
    write_old_record,
)
from tests.unit.agent.middlewares.test_clearing import _one
from tests.unit.scripted import Scripted
from tests.unit.session.conftest import _calls, _resume, _say, _searches
from tests.unit.session.test_subagent_gates import _parent_with
from tests.unit.stand_ins import _Ordered


def _harness(cfg, name: str):
    return cfg.workspace / "sessions" / "s" / HARNESS / name


def _lines(cfg) -> list[dict[str, Any]]:
    """Every line of the log, both its files', each settled turn's after its opening."""
    return list(read_turns(cfg.workspace / "sessions" / "s" / HARNESS).view)


def _conversation(cfg) -> tuple[Message, ...]:
    """What the next turn is built on, as kingfisher rebuilds it from the log."""
    return read_conversation(cfg.workspace / "sessions" / "s" / HARNESS)


def _ended_on(*reply: AIMessage) -> tuple[Message, ...]:
    """The conversation as the model last saw it: what the last call was handed, and the
    reply it gave, where it gave one."""
    handed = [one for one in as_messages(Scripted.heard[-1]) if one.role != "system"]
    return (*handed, *as_messages(reply))


def _handed_first(at: int) -> tuple[Message, ...]:
    """What call `at`, the first of a turn, was handed before the turn's own message."""
    *before, asked = [one for one in as_messages(Scripted.heard[at]) if one.role != "system"]
    assert asked.role == "user", "the turn's own message was not the last thing handed"
    return tuple(before)


def _openings(cfg) -> list[dict[str, Any]]:
    return [line for line in _lines(cfg) if PARENT in line]


def _untimed(line: dict[str, Any]) -> dict[str, Any]:
    """`line` without the times it was written at, which a test cannot know beforehand."""
    return {key: value for key, value in line.items() if key not in (STARTED, ENDED)}


def _summaries(cfg, turn: str) -> int:
    return sum(CUT in line for line in _lines(cfg) if line["turn"] == turn)


def test_the_log_rebuilds_the_conversation_across_summaries_pauses_and_resumes(logged):
    """A summary's cut counted from where deepagents counts it rather than from what the
    log replays -- the whole state, the summary a resume carried -- rebuilds a
    conversation the model never saw; and a resume ending on the summary it carried
    logs it a second time, as a summary it never made.

    Summarised by message count, so the script decides where each summary falls: two
    inside one turn, one inside the resume carrying them, and none in a later resume.
    """
    an_agent(
        logged,
        interrupt_on="[sql_query]",
        middlewares="[{name: summarize-by-count, settings: {messages: 12}}]",
        **EVERY_TOOL,
    )
    kf = Kingfisher(
        logged, sessions=LocalSessions(), middlewares={"summarize-by-count": SummarizeByCount}
    )

    gate = _calls(("sql_query", "x", "g1"))
    paused = _say(kf, "dig", *_searches("a", 10), gate, note="brief")
    assert paused.stop_reason == AWAITING, "the turn never stopped at the gate"
    assert Scripted.summaries == 2, "the control: the turn did not summarise twice"
    assert _summaries(logged, paused.turn_id) == 1, "a turn ends on one summary"
    assert _conversation(logged) == _ended_on(gate)

    ok = AIMessage("ok")
    resumed = _resume(
        kf,
        Decision(call_id=paused.pending[0].call_id, action="approve"),
        script=(*_searches("b", 2), ok),
    )
    assert Scripted.summaries == 3, "the control: the resume did not summarise again"
    assert _conversation(logged) == _ended_on(ok)

    gate = _calls(("sql_query", "y", "g2"))
    again = _say(kf, "and then?", *_searches("c", 2), gate)
    assert Scripted.summaries == 4, "the control: the second pause did not summarise"
    assert _conversation(logged) == _ended_on(gate)
    done = AIMessage("done")
    last = _resume(
        kf,
        Decision(call_id=again.pending[0].call_id, action="approve"),
        script=(done,),
    )

    assert Scripted.summaries == 4, "the control: the last resume summarised"
    assert _summaries(logged, last.turn_id) == 0
    assert _conversation(logged) == _ended_on(done)
    # Past the count by now, which would summarise the next turn before its first call.
    an_agent(logged, interrupt_on="[sql_query]", **EVERY_TOOL)
    rebuilt, at = _conversation(logged), len(Scripted.heard)
    _say(Kingfisher(logged, sessions=LocalSessions()), "and now?", AIMessage("fin"))
    assert Scripted.summaries == 4, "the control: the next turn summarised before it was handed it"
    assert _handed_first(at) == rebuilt
    # The count the next turn holds its rebuild to, so the two must agree on a whole log.
    rows = [json.loads(one) for one in read_record(_harness(logged, LEDGER_FILE)).splitlines()]
    assert [one["messages"] for one in rows if "messages" in one][-1] == len(_conversation(logged))
    assert [{key: one[key] for key in ("turn", PARENT)} for one in _openings(logged)][:4] == [
        {"turn": paused.turn_id, PARENT: None},
        {"turn": resumed.turn_id, PARENT: paused.turn_id},
        {"turn": again.turn_id, PARENT: resumed.turn_id},
        {"turn": last.turn_id, PARENT: again.turn_id},
    ]
    first, second = _openings(logged)[:2]
    assert _untimed(first) == {"turn": paused.turn_id, PARENT: None}
    assert _untimed(second) == {"turn": resumed.turn_id, PARENT: paused.turn_id, "resumed": True}


def test_the_log_rebuilds_the_conversation_a_failed_turn_left(logged):
    """A turn that fails still writes what it said before it failed, and a log that
    skipped it would rebuild every later turn without those messages."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "first", AIMessage("one"))

    with pytest.raises(RuntimeError, match="the model broke"):
        _say(kf, "second", _calls(("web_search", "q", "c1")), RuntimeError("the model broke"))
    assert _conversation(logged) == _ended_on()
    assert any(one.call_id == "c1" for one in _conversation(logged)), "nothing was logged"

    rebuilt, at = _conversation(logged), len(Scripted.heard)
    _say(kf, "third", AIMessage("three"))

    assert _handed_first(at) == rebuilt


def test_the_log_rebuilds_the_conversation_after_a_pause_a_request_superseded(logged):
    """deepagents answers each call a superseded pause left with a message of its own,
    put after the turn's history; counted from anywhere else, a turn's messages lose those
    answers or repeat what came before them."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "go", _calls(("web_search", "q", "c1"), ("sql_query", "a", "ga")))

    ok = AIMessage("ok")
    superseding = _say(kf, "never mind", ok)

    own = [line for line in _lines(logged) if line["turn"] == superseding.turn_id]
    assert [line.get("call_id") for line in own[1:3]] == ["c1", "ga"], "nothing was patched"
    assert _conversation(logged) == _ended_on(ok)


def test_the_log_rebuilds_the_conversation_a_rewind_went_back_to(logged):
    """Rebuilt from the turn the log holds last rather than the one rewound to, the next
    turn is handed the turns the caller took back."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    one = AIMessage("one")
    first = _say(kf, "first", _calls(("web_search", "q", "c1")), one)
    ended = _ended_on(one)
    _say(kf, "second", AIMessage("two"))
    kf.session("s").rewind(first.turn_id)

    at = len(Scripted.heard)
    _say(kf, "third", AIMessage("three"))

    assert _handed_first(at) == ended


@pytest.mark.parametrize(
    "damaged",
    [
        '{"turn": "t", "role": "assistant", "tool_ca',
        '["a", "list"]',
        '{"turn": "t", "role": "assistant", "tool_calls": [1]}',
    ],
    ids=["cut mid-line", "not an object", "a message that does not read back"],
)
def test_the_reading_stops_at_the_first_damaged_line_and_the_turn_after_says_so(
    logged, damaged
):
    """A damaged line skipped let the result after it answer a call the model never sees
    made, which a provider refuses on every turn after; and one that raised failed every
    later turn in the session. A log neither copy's seal matches is one written before
    seals were, or by something other than kingfisher."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "first", _calls(("web_search", "q", "c1")), AIMessage("one"))
    log = _harness(logged, RECENT_TURNS_FILE)
    opening, asked, called, *rest = read_record(log).splitlines(keepends=True)
    assert '"c1"' in called, "the control: the line damaged is not the call"
    write_old_record(log, "".join([opening, asked, damaged + "\n", *rest]))

    at = len(Scripted.heard)
    short = _say(kf, "second", AIMessage("two"))

    assert short.history_lost is not None
    (handed,) = _handed_first(at)
    assert handed.role == "user" and handed.content.startswith("first")
    assert not any(one.get("call_id") == "c1" for one in _lines(logged)), (
        "the lines past the damage were written back"
    )


def test_a_turn_whose_log_was_never_written_is_reported_by_the_next(logged, monkeypatch):
    """Written after the ledger, a turn dying before its log leaves the ledger counting
    messages the log cannot rebuild, and the next turn is told its conversation is short
    rather than being handed one with a turn missing in silence."""
    from kingfisher.core.session import running

    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "first", AIMessage("one"))

    def dies(*args: Any, **kwargs: Any):
        msg = "the backend went away"
        raise OSError(msg)
        yield

    with monkeypatch.context() as patched:
        patched.setattr(running, "keep_turns", dies)
        died = _say(kf, "second", AIMessage("two"))
    assert died.answer == "two", "the control: the turn did not run"
    rebuilt, at = _conversation(logged), len(Scripted.heard)

    short = _say(kf, "third", AIMessage("three"))

    assert short.history_lost is not None
    assert "read 2 of the 4 messages" in short.history_lost
    assert _handed_first(at) == rebuilt


def test_each_answer_names_the_call_it_answered_and_not_its_place_in_the_pause(logged):
    """A decision's id is a position among the gated calls, which the ungated call ahead
    of them shifts away from the reply's own order; an answer logged against the wrong
    call says a person approved what they refused. Keyed `answer`, a decision reads to a
    reader of the log as an answer the turn handed back."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    paused = _say(
        kf,
        "go",
        _calls(("web_search", "q", "c1"), ("sql_query", "a", "ga"), ("sql_query", "b", "gb")),
    )
    first, second = (one.call_id for one in paused.pending)
    held = _harness(logged, RECENT_TURNS_FILE).read_text()
    with pytest.raises(DecisionError):
        _resume(kf, Decision(call_id="nobody#0", action="approve"))
    assert _harness(logged, RECENT_TURNS_FILE).read_text() == held, "a refused resume was logged"

    done = AIMessage("done")
    resumed = _resume(
        kf,
        Decision(call_id=second, action="approve"),
        Decision(call_id=first, action="reject", message="not that one"),
        script=(done,),
    )

    answers = [line for line in _lines(logged) if "decision" in line]
    assert answers == [
        {"turn": resumed.turn_id, "decision": second, "call_id": "gb", ACTION: "approve"},
        {"turn": resumed.turn_id, "decision": first, "call_id": "ga", ACTION: "reject"},
    ]
    assert [_untimed(line) for line in _lines(logged) if ANSWER in line] == [
        {"turn": paused.turn_id, ANSWER: paused.answer, STOP_REASON: AWAITING},
        {"turn": resumed.turn_id, ANSWER: "done", STOP_REASON: END_TURN},
    ]
    assert _conversation(logged) == _ended_on(done)


def test_a_subagents_answer_names_the_subagents_own_call(cfg):
    """A subagent's gated call is in no state the paused turn's snapshot holds, so
    read off that alone, its answer names no call at all."""
    kf = Kingfisher(cfg, graph=_parent_with("scribe"), sessions=LocalSessions())
    paused = kf.run(Request("delegate it", session_id="s"))
    assert paused.pending[0].agent == "scribe", "the control: the subagent did not pause"

    approved = Decision(call_id=paused.pending[0].call_id, action="approve")
    kf.run(Resume(session_id="s", decisions=(approved,)))

    (answer,) = (line for line in _lines(cfg) if "decision" in line)
    assert answer["call_id"] == "d1"


def test_a_result_a_later_turn_cleared_is_rebuilt_as_its_handle_and_a_rewind_gets_it_whole(
    clearing,
):
    """Clearing swaps an earlier turn's result for its handle in state, which a log of
    each turn's own messages never saw: the next turn was handed the whole result again,
    and the summariser counted it before clearing could take it back out. Logged over the
    line first written, a rewind to before the clearing would get the handle; logged after
    the answer, the turn's last line is not its answer."""
    kf = Kingfisher(clearing, sessions=LocalSessions())
    first = _say(kf, "first", _one("bulky", 1, n=1), AIMessage("done"))
    whole = _conversation(clearing)
    done = AIMessage("done")

    _say(kf, "second", *(_one("bulky", n, n=n) for n in range(2, 8)), done)

    said = {one.call_id: one.content for one in _conversation(clearing)}
    assert "cleared; full result" in said["c1"], "the control: the first turn's result stayed"
    assert any(AT in line for line in _lines(clearing)), "the control: no clearing was logged"
    assert _conversation(clearing) == _ended_on(done)
    # A reader takes a turn's last line as its answer; one ending on an `at` line has none.
    assert ANSWER in _lines(clearing)[-1]
    kf.session("s").rewind(first.turn_id)
    assert _conversation(clearing) == whole
    assert next(one for one in whole if one.call_id == "c1").content.startswith("row 1-0000")


def test_a_summary_after_a_clearing_cuts_where_the_model_saw_it_cut(clearing):
    """A line for a cleared result changes a message's text and never the count, so a
    later summary's cut still lands where deepagents put it; counted with that line as a
    message, the rebuild keeps the wrong tail."""
    an_agent(
        clearing, middlewares="[{name: summarize-by-count, settings: {messages: 26}}]", **EVERY_TOOL
    )
    kf = Kingfisher(
        clearing, sessions=LocalSessions(), middlewares={"summarize-by-count": SummarizeByCount}
    )
    _say(kf, "first", _one("bulky", 1, n=1), AIMessage("done"))
    _say(kf, "second", *(_one("bulky", n, n=n) for n in range(2, 8)), AIMessage("done"))
    assert Scripted.summaries == 0, "the control: the clearing turn summarised"
    assert any(AT in line for line in _lines(clearing)), "the control: no clearing was logged"
    done = AIMessage("done")

    third = _say(kf, "third", *(_one("bulky", n, n=n) for n in range(8, 12)), done)

    assert Scripted.summaries >= 1, "the control: the third turn never summarised"
    assert _summaries(clearing, third.turn_id) == 1
    assert _conversation(clearing) == _ended_on(done)


def test_an_expired_result_stays_whole_in_the_log_while_the_model_is_handed_it(logged):
    """Swapped for its handle as soon as it expired, a result the model was still handed
    whole came back to the next turn as a handle: the wording of what it had read changed
    under it, and the cached prefix with it from that result on."""
    kf = Kingfisher(replace(logged, keep_results_rounds=1), sessions=LocalSessions())
    first = _say(kf, "first", _calls(("web_search", "q", "c1")), AIMessage("one"))
    _say(kf, "second", AIMessage("two"))
    (row,) = first.ledger

    rows = [json.loads(one) for one in read_record(_harness(logged, LEDGER_FILE)).splitlines()]
    assert any(one.get("id") == row.id and one.get("expired_in") for one in rows), (
        "the control: the result never expired"
    )

    at = len(Scripted.heard)
    _say(kf, "third", AIMessage("three"))

    (line,) = (one for one in _lines(logged) if one.get("call_id") == "c1" and "role" in one)
    assert line["content"].startswith("Ten results for q")
    (handed,) = (one.content for one in _handed_first(at) if one.call_id == "c1")
    assert handed != expired_handle(replace(row, expired_in="any"))
    assert handed.startswith("Ten results for q")


def test_an_expired_result_cleared_out_of_view_is_kept_nowhere_whole(clearing):
    """`KINGFISHER_KEEP_RESULTS_ROUNDS` bounds what a session keeps on disk, and a log
    keeping every result whole forever would undo it; a file tool's answer that merely
    ends in that result's id is the agent's own file, and swapped, it is lost."""
    (clearing.workspace / "sessions" / "s" / "outputs" / "note.txt").write_text("[A-0001]\n")
    read = {"name": "read_file", "args": {"file_path": "/outputs/note.txt"}, "id": "r1"}
    kf = Kingfisher(replace(clearing, keep_results_rounds=1), sessions=LocalSessions())
    first = _say(
        kf,
        "first",
        _one("bulky", 1, n=1),
        AIMessage(content="", tool_calls=[read]),
        AIMessage("done"),
    )
    own = _by_call(clearing)["r1"]
    done = AIMessage("done")

    _say(kf, "second", *(_one("bulky", n, n=n) for n in range(2, 8)), done)

    (row,) = first.ledger
    assert _by_call(clearing)["c1"] == expired_handle(replace(row, expired_in="any"))
    # Past the preview a handle opens with, so only the whole result holds it.
    assert not any("row 1-0100" in json.dumps(line) for line in _lines(clearing))
    assert _by_call(clearing)["r1"] == own, "the agent's own file was swapped"
    assert _conversation(clearing) == _ended_on(done)


def _by_call(cfg) -> dict[str, str]:
    """Each message line's text by the call it answers, as first written."""
    return {
        line["call_id"]: line.get("content", "")
        for line in _lines(cfg)
        if "role" in line and "call_id" in line
    }


def test_a_conversation_the_log_cannot_say_is_warned_of(caplog):
    """The log can say only that a message's text changed; a turn that ended on more or
    fewer messages than its lines rebuild would have its next turn built on the wrong
    conversation without a word."""
    lines = [{"turn": "t", PARENT: None}, {"turn": "t", "role": "user"}]
    ended = (Message(role="user"), Message(role="assistant", content="extra"))

    with caplog.at_level(logging.WARNING, logger="kingfisher.warnings"):
        assert _rewritten("t", lines, ended) == []

    assert "turn t ended on 2 messages" in caplog.text


def test_the_log_is_written_after_the_ledger(logged):
    """Written before it, a turn dying between the two leaves a log holding a turn the
    ledger does not count, and nothing tells the next turn its results are missing."""
    backends = _Ordered()
    _say(Kingfisher(logged, sessions=backends), "go", AIMessage("done"))

    assert backends.at(LEDGER_FILE) < backends.at(RECENT_TURNS_FILE)


def _users(cfg) -> list[dict[str, Any]]:
    return [line for line in _lines(cfg) if line.get("role") == "user"]


def test_the_users_line_names_who_wrote_each_piece_of_the_message(logged):
    """A reader showing the conversation would show kingfisher's lines as the person's, and
    the deployment's note as theirs; and `turns()` listing more than the `user` pieces
    names a task the person never typed."""
    kf = Kingfisher(logged, sessions=LocalSessions())

    asked = _say(kf, "dig", AIMessage("ok"), note="brief")

    (line,) = _users(logged)
    assert "content" not in line
    by_whom = [(one["by"], one["text"]) for one in line[PARTS]]
    assert by_whom[:2] == [("user", "dig"), ("caller", "\n\nbrief")]
    assert by_whom[2][0] == "kingfisher"
    assert by_whom[2][1].startswith("\n\n/scratchpad/ is yours to work in")
    (sent,) = (one for one in Scripted.heard[0] if isinstance(one, HumanMessage))
    assert "".join(one["text"] for one in line[PARTS]) == sent.content
    assert _conversation(logged) == _ended_on(AIMessage("ok"))
    (record,) = kf.session("s").turns()
    assert (record.id, record.task) == (asked.turn_id, "dig")


class _Rewriter(AgentMiddleware):
    """A deployment's middleware that rewrites the turn's message in the graph's state,
    which no middleware of kingfisher's does: theirs change a single model call."""

    name = "rewriter"

    def before_agent(self, state: Any, runtime: Any) -> Any:
        (asked,) = (one for one in state["messages"] if isinstance(one, HumanMessage))
        return {"messages": [HumanMessage(id=asked.id, content="REWRITTEN")]}


def test_a_message_rewritten_in_the_state_is_logged_as_the_model_read_it(logged):
    """Logged as kingfisher's pieces, the rebuild gives the conversation a message the
    model never read, and a reader is told who wrote words nobody can vouch for."""
    an_agent(logged, interrupt_on="[sql_query]", middlewares="[rewriter]", **EVERY_TOOL)
    sink = RecordedEvents()
    kf = Kingfisher(
        logged, sessions=LocalSessions(), middlewares={"rewriter": _Rewriter}, run_events=sink
    )

    asked = _say(kf, "dig", AIMessage("ok"))

    (sent,) = (one for one in Scripted.heard[0] if isinstance(one, HumanMessage))
    assert sent.content == "REWRITTEN", "the control: nothing rewrote the message"
    (line,) = _users(logged)
    assert line[PARTS] == [{"text": "REWRITTEN"}]
    assert _conversation(logged) == _ended_on(AIMessage("ok"))
    (warned,) = sink.named("message_rewritten")
    assert warned["turn_id"] == asked.turn_id
    assert asked.turn_id in warned["message"]
    assert "REWRITTEN" not in json.dumps(warned, default=str), "the message reached the log"
    assert kf.session("s").turns()[0].task is None


def test_each_turn_ends_on_the_answer_its_caller_was_handed(logged):
    """Logged before the answer is cleaned, the line keeps the `<think>` block the caller
    never saw; logged off the turn's last words, a failed turn has an answer it never gave;
    an empty answer left unwritten leaves a reader guessing from the assistant lines; and
    without why each stopped, a caller retrying a turn its provider could not serve, or one
    that raised, reads it as a turn that answered nothing.
    """
    kf = Kingfisher(logged, sessions=LocalSessions())
    answered = _say(kf, "first", AIMessage("<think>mulling</think>\n\n42"))
    paused = _say(kf, "second", _calls(("sql_query", "q", "g1")))
    unserved = _say(kf, "third", TimeoutError("the provider took too long"))
    looking = AIMessage(
        content="Looking.", tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "c1"}]
    )
    with pytest.raises(RuntimeError, match="the model broke"):
        _say(kf, "fourth", looking, RuntimeError("the model broke"))
    failed = _openings(logged)[-1]["turn"]

    assert answered.answer == "42", "the control: the answer was not cleaned"
    assert paused.stop_reason == AWAITING, "the control: the turn never stopped at the gate"
    assert (unserved.answer, unserved.stop_reason) == ("", "model_unavailable")
    ends = {}
    for line in _lines(logged):
        ends[line["turn"]] = _untimed(line)
    assert ends == {
        answered.turn_id: {"turn": answered.turn_id, ANSWER: "42", STOP_REASON: END_TURN},
        paused.turn_id: {"turn": paused.turn_id, ANSWER: "", STOP_REASON: AWAITING},
        unserved.turn_id: {
            "turn": unserved.turn_id, ANSWER: "", STOP_REASON: "model_unavailable"
        },
        failed: {"turn": failed, ANSWER: "", STOP_REASON: None},
    }
    assert [(one.answer, one.stop_reason) for one in kf.session("s").turns()] == [
        ("42", END_TURN), ("", AWAITING), ("", "model_unavailable"), ("", None)
    ]
    assert _conversation(logged) == _ended_on()


def test_each_turn_keeps_when_it_started_and_ended(logged):
    """Without them nothing in the log lines up with the run events a client reads, and
    `turns()` cannot say when a turn ran; read off any line but its own, a resume shows the
    paused turn's start as its own."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    before = time.time()
    paused = _say(kf, "dig", _calls(("sql_query", "q", "g1")))
    between = time.time()
    (pending,) = paused.pending
    resumed = _resume(
        kf, Decision(call_id=pending.call_id, action="approve"), script=(AIMessage("done"),)
    )
    after = time.time()

    opened = {line["turn"]: line[STARTED] for line in _openings(logged)}
    ended = {line["turn"]: line[ENDED] for line in _lines(logged) if ANSWER in line}
    assert before <= opened[paused.turn_id] < ended[paused.turn_id] <= between
    assert between <= opened[resumed.turn_id] < ended[resumed.turn_id] <= after
    assert [(one.id, one.started, one.ended) for one in kf.session("s").turns()] == [
        (turn, opened[turn], ended[turn]) for turn in (paused.turn_id, resumed.turn_id)
    ]


def test_a_replys_reasoning_is_kept_on_its_line_and_never_handed_back(logged):
    """Kept on the message rather than its line, every turn after one that reasoned warns
    `conversation_not_logged` and writes no `at` line, since earlier turns reach the state
    as the rebuild handed them, without it; and a rebuild handing it back sends a reply's
    thinking to models that never wrote it."""
    sink = RecordedEvents()
    kf = Kingfisher(logged, sessions=LocalSessions(), run_events=sink)
    # As the two chat classes hand them back: Anthropic's thinking block, which MiniMax
    # sends too, and a Responses reply's reasoning summary.
    thought = AIMessage(
        content=[
            {"type": "thinking", "thinking": "Search before answering.", "signature": "s"},
            {"type": "text", "text": "Looking."},
        ],
        tool_calls=[{"name": "web_search", "args": {"query": "q"}, "id": "c1"}],
        response_metadata={"model_provider": "anthropic"},
    )
    summed = AIMessage(
        content=[
            {
                "type": "reasoning",
                "id": "rs_1",
                "summary": [{"type": "summary_text", "text": "The search answered it."}],
            },
            {"type": "text", "text": "Ten results.", "annotations": []},
        ],
        response_metadata={"model_provider": "openai"},
    )
    _say(kf, "find", thought, summed)
    at = len(Scripted.heard)
    _say(kf, "and?", AIMessage("nothing more"))

    replies = [line for line in _lines(logged) if line.get("role") == "assistant"]
    assert [line.get(REASONING) for line in replies] == [
        "Search before answering.",
        "The search answered it.",
        None,
    ]
    assert replies[0]["content"] == "Looking."
    handed = [one for one in Scripted.heard[at] if isinstance(one, AIMessage)]
    assert len(handed) == 2, "the control: the next turn was not handed the earlier replies"
    assert all(isinstance(one.content, str) and not one.additional_kwargs for one in handed)
    assert sink.named("conversation_not_logged") == []
    assert _conversation(logged) == _ended_on(AIMessage("nothing more"))


def test_a_reply_whose_reasoning_cannot_be_read_still_logs_its_turn(logged):
    """Raised out of the turn's end, a reasoning block langchain cannot read costs the turn
    its log, and the next turn is built without anything it said."""
    odd = AIMessage(
        content=[{"type": "text", "text": "Answered."}],
        additional_kwargs={"reasoning": "not a block"},
        response_metadata={"model_provider": "openai"},
    )
    with pytest.raises(TypeError):
        _ = odd.content_blocks  # the control: langchain cannot read it

    _say(Kingfisher(logged, sessions=LocalSessions()), "go", odd)

    (reply,) = (line for line in _lines(logged) if line.get("role") == "assistant")
    assert reply == {"turn": reply["turn"], "role": "assistant", "content": "Answered."}

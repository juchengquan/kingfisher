"""The turn log: every turn's messages as first written, which a rewind rebuilds the
conversation from once a summary has replaced the transcript's front.

Through real turns -- a scripted model, kingfisher's own build, deepagents' own
summariser -- because what a turn added is read off the state a real graph leaves, and a
state built here would only hold what the test thought it would.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher import Kingfisher, Request
from kingfisher.core.agent.middlewares.recorded import expired_handle
from kingfisher.core.layout import HARNESS, LEDGER_FILE, TRANSCRIPT_FILE, TURNS_FILE
from kingfisher.core.request import Decision, DecisionError, Resume
from kingfisher.core.result import AWAITING
from kingfisher.core.session.transcript import Message, from_json, from_record
from kingfisher.core.session.turnlog import CUT, FROM, PARENT, TASK
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, read_record, start, tools_dir, write_old_record
from tests.unit.scripted import Scripted
from tests.unit.session.test_ledger import _Ordered
from tests.unit.session.test_subagent_gates import _parent_with

TOOLS = '''
def web_search(query: str) -> str:
    """Search the web."""
    return f"Ten results for {query}"


def sql_query(query: str) -> str:
    """Run a query."""
    return f"two rows for {query}"


TOOLS = [web_search, sql_query]
'''


@pytest.fixture
def logged(scripted):
    """An agent with the tools above, gating `sql_query`, and the session `s`."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, interrupt_on="[sql_query]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _calls(*made: tuple[str, str, str]) -> AIMessage:
    """One reply asking for these `(tool, query, call id)` calls together."""
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"query": query}, "id": at} for name, query, at in made],
    )


def _say(kf: Kingfisher, task: str, *script: AIMessage | Exception, **request: Any):
    Scripted.script.extend(script)
    return kf.run(Request(task, agent="only", session_id="s", **request))


def _resume(kf: Kingfisher, *answers: Decision, script: tuple[AIMessage, ...] = ()):
    Scripted.script.extend(script)
    return kf.run(Resume(session_id="s", agent="only", decisions=answers))


def _harness(cfg, name: str):
    return cfg.workspace / "sessions" / "s" / HARNESS / name


def _lines(cfg) -> list[dict[str, Any]]:
    return [json.loads(line) for line in read_record(_harness(cfg, TURNS_FILE)).splitlines()]


def _transcript(cfg) -> tuple[Message, ...]:
    return from_json(read_record(_harness(cfg, TRANSCRIPT_FILE)))


def _replayed(lines: list[dict[str, Any]]) -> tuple[Message, ...]:
    """The conversation the log's last turn ended on, rebuilt from its lines alone: each
    turn on its path from the first, its parent's conversation kept to its `from`, its
    messages appended, and a summary standing for everything before its `cut`."""
    turns: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
    last = ""
    for line in lines:
        if PARENT in line:
            turns[line["turn"]] = (line, [])
            last = line["turn"]
        else:
            turns[line["turn"]][1].append(line)
    path = [last]
    while (parent := turns[path[-1]][0][PARENT]) is not None:
        path.append(parent)
    said: list[Message] = []
    for turn in reversed(path):
        opening, body = turns[turn]
        said = said[: opening[FROM]] if FROM in opening else said
        for line in body:
            if "role" not in line:
                continue
            message = from_record(line)
            said = [message, *said[line[CUT] :]] if CUT in line else [*said, message]
    return tuple(said)


def _openings(cfg) -> list[dict[str, Any]]:
    return [line for line in _lines(cfg) if PARENT in line]


def _searches(prefix: str, count: int) -> list[AIMessage]:
    return [_calls(("web_search", f"{prefix}{n}", f"{prefix}{n}")) for n in range(count)]


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

    paused = _say(kf, "dig", *_searches("a", 10), _calls(("sql_query", "x", "g1")), note="brief")
    assert paused.stop_reason == AWAITING, "the turn never stopped at the gate"
    assert Scripted.summaries == 2, "the control: the turn did not summarise twice"
    assert _summaries(logged, paused.turn_id) == 1, "a turn ends on one summary"
    assert _replayed(_lines(logged)) == _transcript(logged)

    resumed = _resume(
        kf,
        Decision(call_id=paused.pending[0].call_id, action="approve"),
        script=(*_searches("b", 2), AIMessage("ok")),
    )
    assert Scripted.summaries == 3, "the control: the resume did not summarise again"
    assert _replayed(_lines(logged)) == _transcript(logged)

    again = _say(kf, "and then?", *_searches("c", 2), _calls(("sql_query", "y", "g2")))
    assert Scripted.summaries == 4, "the control: the second pause did not summarise"
    last = _resume(
        kf,
        Decision(call_id=again.pending[0].call_id, action="approve"),
        script=(AIMessage("done"),),
    )

    assert Scripted.summaries == 4, "the control: the last resume summarised"
    assert _summaries(logged, last.turn_id) == 0
    assert _replayed(_lines(logged)) == _transcript(logged)
    assert [{key: one[key] for key in ("turn", PARENT)} for one in _openings(logged)] == [
        {"turn": paused.turn_id, PARENT: None},
        {"turn": resumed.turn_id, PARENT: paused.turn_id},
        {"turn": again.turn_id, PARENT: resumed.turn_id},
        {"turn": last.turn_id, PARENT: again.turn_id},
    ]
    first, second = _openings(logged)[:2]
    assert first == {"turn": paused.turn_id, PARENT: None, TASK: "dig", FROM: 0}
    assert second == {"turn": resumed.turn_id, PARENT: paused.turn_id, "resumed": True}


def test_the_log_rebuilds_the_conversation_a_failed_turn_left(logged):
    """A turn that fails still writes what it said before it failed, and a log that
    skipped it would rebuild every later turn without those messages."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "first", AIMessage("one"))

    with pytest.raises(RuntimeError, match="the model broke"):
        _say(kf, "second", _calls(("web_search", "q", "c1")), RuntimeError("the model broke"))
    assert _replayed(_lines(logged)) == _transcript(logged)
    assert any(line.get("call_id") == "c1" for line in _lines(logged)), "nothing was logged"

    _say(kf, "third", AIMessage("three"))

    assert _replayed(_lines(logged)) == _transcript(logged)


def test_the_log_rebuilds_the_conversation_after_a_pause_a_request_superseded(logged):
    """deepagents answers each call a superseded pause left with a message of its own,
    put after the turn's history; counted from anywhere else, a turn's messages lose those
    answers or repeat what came before them."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "go", _calls(("web_search", "q", "c1"), ("sql_query", "a", "ga")))

    superseding = _say(kf, "never mind", AIMessage("ok"))

    own = [line for line in _lines(logged) if line["turn"] == superseding.turn_id]
    assert [line.get("call_id") for line in own[1:3]] == ["c1", "ga"], "nothing was patched"
    assert _replayed(_lines(logged)) == _transcript(logged)


def test_a_turn_that_found_its_conversation_short_is_logged_from_where_it_began(logged):
    """A turn reads its transcript only as far as it is whole, and logged as following
    all its parent left, it would be credited with messages its model never saw."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    _say(kf, "first", AIMessage("one"))
    _say(kf, "second", AIMessage("two"))
    transcript = _harness(logged, TRANSCRIPT_FILE)
    write_old_record(transcript, "".join(read_record(transcript).splitlines(keepends=True)[:2]))

    short = _say(kf, "third", AIMessage("three"))

    assert short.history_lost is not None, "the control: nothing was found short"
    assert _openings(logged)[-1][FROM] == 2
    assert _replayed(_lines(logged)) == _transcript(logged)


def test_each_answer_names_the_call_it_answered_and_not_its_place_in_the_pause(logged):
    """A decision's id is a position among the gated calls, which the ungated call ahead
    of them shifts away from the reply's own order; an answer logged against the wrong
    call says a person approved what they refused."""
    kf = Kingfisher(logged, sessions=LocalSessions())
    paused = _say(
        kf,
        "go",
        _calls(("web_search", "q", "c1"), ("sql_query", "a", "ga"), ("sql_query", "b", "gb")),
    )
    first, second = (one.call_id for one in paused.pending)
    held = _harness(logged, TURNS_FILE).read_text()
    with pytest.raises(DecisionError):
        _resume(kf, Decision(call_id="nobody#0", action="approve"))
    assert _harness(logged, TURNS_FILE).read_text() == held, "a refused resume was logged"

    resumed = _resume(
        kf,
        Decision(call_id=second, action="approve"),
        Decision(call_id=first, action="reject", message="not that one"),
        script=(AIMessage("done"),),
    )

    answers = [line for line in _lines(logged) if "decision" in line]
    assert answers == [
        {"turn": resumed.turn_id, "decision": second, "call_id": "gb", "answer": "approve"},
        {"turn": resumed.turn_id, "decision": first, "call_id": "ga", "answer": "reject"},
    ]
    assert _replayed(_lines(logged)) == _transcript(logged)


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


def test_a_result_older_than_the_rounds_kept_is_a_handle_in_the_log_too(logged):
    """`KINGFISHER_KEEP_RESULTS_ROUNDS` bounds what a session keeps on disk, and a log
    keeping every result whole forever would undo it; a file tool's answer that merely
    ends in that result's id is the agent's own file, and swapped, it is lost.
    Beside the control: unset, the log keeps the result as the model first read it.
    """
    (logged.workspace / "sessions" / "s" / "outputs" / "note.txt").write_text("[A-0001]\n")
    read = {"name": "read_file", "args": {"file_path": "/outputs/note.txt"}, "id": "r1"}
    for rounds, swapped in ((1, True), (None, False)):
        kf = Kingfisher(replace(logged, keep_results_rounds=rounds), sessions=LocalSessions())
        first = _say(
            kf,
            "first",
            _calls(("web_search", "q", "c1")),
            AIMessage(content="", tool_calls=[read]),
            AIMessage("one"),
        )
        _say(kf, "second", AIMessage("two"))
        (row,) = first.ledger
        expired = replace(row, expired_in="any")

        by_call = {line.get("call_id"): line.get("content") for line in _lines(logged)}
        assert (by_call["c1"] == expired_handle(expired)) is swapped, rounds
        assert by_call["c1"].startswith("Ten results for q") is not swapped
        (own,) = (one.content for one in _transcript(logged) if one.call_id == "r1")
        assert by_call["r1"] == own, "the agent's own file was swapped"
        for name in (TURNS_FILE, TRANSCRIPT_FILE, LEDGER_FILE):
            _harness(logged, name).unlink()


def test_the_log_is_written_after_the_ledger_and_before_the_transcript(logged):
    """Written after the transcript, a turn dying between the two leaves a log missing a
    turn the next one builds on, and nothing says so; before it, the transcript is short
    of the ledger's count, which the next turn reports."""
    backends = _Ordered()
    _say(Kingfisher(logged, sessions=backends), "go", AIMessage("done"))

    assert backends.at(LEDGER_FILE) < backends.at(TURNS_FILE) < backends.at(TRANSCRIPT_FILE)

"""Who a turn runs for, handed to its tools on `ToolContext.caller` and never to the model."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.core.request import Decision, Request, Resume, caller_of
from kingfisher.core.result import AWAITING
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted

WHO = '''from langchain.tools import ToolRuntime

from kingfisher import ToolContext


def who(runtime: ToolRuntime[ToolContext]) -> str:
    """Say who this turn runs for."""
    caller = runtime.context.caller
    held = "none" if caller.source_ids is None else ",".join(sorted(caller.source_ids))
    return (
        f"{caller.context.get('user_id')} {held} {caller.datazone} "
        f"{caller.started.isoformat()}"
    )


TOOLS = [who]
'''


def _a_tool(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "who.py").write_text(WHO, encoding="utf-8")


def _calls_who() -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "who", "args": {}, "id": "call-who"}])


def _said(events: list[Any]) -> str:
    """What `who` answered, its ledger id left off."""
    (text,) = [e.text for e in events if e.kind == "tool_result" and e.tool == "who"]
    return text.rsplit("\n", 1)[0]


def test_a_tool_is_handed_the_requests_context_and_when_the_turn_started(scripted):
    """`context` is the caller's, for a tool to filter by, and the clock is kingfisher's."""
    _a_tool(scripted)
    an_agent(scripted, **EVERY_TOOL)
    Scripted.script.extend([_calls_who(), AIMessage(content="done")])
    before = datetime.now(UTC)
    start(scripted, "s")

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s", context={"user_id": "u-17"})
        )
    )

    user, held, zone, started = _said(events).split(" ")
    assert (user, held, zone) == ("u-17", "none", "None")
    assert before <= datetime.fromisoformat(started) <= datetime.now(UTC)
    assert datetime.fromisoformat(started).utcoffset() is not None, "a naive time names no zone"


def test_a_tool_is_handed_the_source_ids_and_datazone_the_caller_holds(scripted):
    """The datazone travels among the held names as `datazone=finance`; a tool is handed
    it apart, and the source ids without it."""
    _a_tool(scripted)
    an_agent(scripted, source_ids="[A]", **EVERY_TOOL)
    Scripted.script.extend([_calls_who(), AIMessage(content="done")])
    start(scripted, "s")

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s"),
            source_ids=["A", "B"],
            datazone="finance",
        )
    )

    assert _said(events).split(" ")[1:3] == ["A,B", "finance"]


@pytest.mark.parametrize("extras", [["common"], ["finance", "common"]], ids=["apart", "repeated"])
def test_a_tool_is_handed_the_datazone_the_caller_is_in_and_not_an_extra_one(scripted, extras):
    """Held, every zone is a `datazone=` name and the one the caller is in is told apart
    only by its type, which an extra repeating it, as a hardcoded list does, can push out
    of the set."""
    _a_tool(scripted)
    an_agent(scripted, source_ids="[A]", **EVERY_TOOL)
    Scripted.script.extend([_calls_who(), AIMessage(content="done")])
    start(scripted, "s")

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s"),
            source_ids=["A", "B"],
            datazone="finance",
            extra_datazones=extras,
        )
    )

    assert _said(events).split(" ")[1:3] == ["A,B", "finance"]


def test_the_model_is_never_sent_the_context(scripted):
    """Driven, not inspected: every message every call was handed, after every middleware."""
    an_agent(scripted)
    Scripted.heard.clear()
    Scripted.script.extend([AIMessage(content="done")])
    start(scripted, "s")

    Kingfisher(scripted, sessions=LocalSessions()).run(
        Request("go", agent="only", session_id="s", context={"user_id": "u-secret-17"})
    )

    assert Scripted.heard, "no call was made, so nothing was checked"
    sent = "".join(str(m.content) for call in Scripted.heard for m in call)
    assert "u-secret-17" not in sent


def test_a_resume_hands_the_tool_the_context_it_presents(scripted):
    """langgraph keeps the context out of the checkpoint, so the call a person approved runs
    for whoever resumes it -- with what they present, as their capabilities are."""
    _a_tool(scripted)
    an_agent(scripted, interrupt_on="[who]", **EVERY_TOOL)
    Scripted.script.extend([_calls_who(), AIMessage(content="done")])
    start(scripted, "s")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    paused = kf.run(Request("go", agent="only", session_id="s", context={"user_id": "first"}))
    assert paused.stop_reason == AWAITING, "the turn never stopped, so nothing is resumed"

    events = list(
        kf.stream(
            Resume(
                session_id="s",
                decisions=(Decision(call_id=paused.pending[0].call_id, action="approve"),),
                context={"user_id": "second"},
            )
        )
    )

    assert _said(events).split(" ")[0] == "second"


@pytest.mark.parametrize("context", [{"user_id": 17}, {17: "u"}])
def test_context_maps_names_to_text(context):
    """Anything else would reach a tool as whatever type the caller's JSON decoded to."""
    with pytest.raises(TypeError, match="maps names to text"):
        Request("go", context=context)


def test_context_is_copied_when_the_request_is_made():
    """The caller's own dict, changed after the call, would change what a tool reads."""
    given = {"user_id": "u-17"}
    request = Request("go", context=given)
    given["user_id"] = "someone else"

    assert request.context == {"user_id": "u-17"}
    with pytest.raises(TypeError):
        request.context["user_id"] = "x"


def test_a_caller_with_no_lock_to_answer_to_holds_nothing():
    """`None` is no caller named, which is not a caller holding no source id."""
    now = datetime.now(UTC)

    assert caller_of(Request("go"), None, now).source_ids is None
    assert caller_of(Request("go"), frozenset(), now).source_ids == frozenset()

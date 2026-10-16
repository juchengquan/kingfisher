"""A message a node writes back under its own id is not a new step of the run.

Clearing swaps older results for handles under the same ids, and deepagents' summariser
does the same when it clips a result that overflowed. The stream reports each node's
update, and read naively every such message became a `tool_result` or a `model_call` the
run never made -- measured on a real writing session, where every clearing pass
re-announced the results and replies it cleared, the replies with their old usage.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher import Kingfisher, Request, default_backends
from kingfisher.infrastructure.harness import runtime
from kingfisher.layout import HARNESS, TRANSCRIPT_FILE
from tests.conftest import an_agent, start, tools_dir
from tests.unit.scripted import Scripted

USAGE = {"input_tokens": 900, "output_tokens": 10, "total_tokens": 910}


def _events(namespace: Any, mode: str, chunk: Any, seen: runtime.SeenMessages) -> list[Any]:
    return list(runtime.events_in(namespace, mode, chunk, seen=seen))


def test_a_message_the_conversation_already_held_is_not_announced_again():
    """Driven through `events_in` with chunks shaped as langgraph streams them: the
    conversation as a step found it, then a step's updates holding one rewrite of each
    kind beside one message that is new."""
    reply = AIMessage(
        "", tool_calls=[{"name": "web_search", "args": {}, "id": "c1"}], id="a1",
        usage_metadata=USAGE,
    )
    result = ToolMessage("ten results", tool_call_id="c1", name="web_search", id="t1")
    seen = runtime.SeenMessages()
    held = {"messages": [HumanMessage("go", id="h1"), reply, result]}
    assert _events((), "values", held, seen) == []

    rewrites = {"messages": [result.model_copy(update={"content": "handle"}), reply]}
    fresh = {"messages": [ToolMessage("more", tool_call_id="c2", name="web_search", id="t2")]}
    events = _events((), "updates", {"clearing": rewrites, "tools": fresh}, seen)

    assert [(e.kind, e.text) for e in events] == [("tool_result", "more")]


def test_a_step_that_writes_twice_is_read_whole():
    """A model call that clears streams its reply and its handles as a list of two
    updates, and a reader expecting one mapping reported neither -- the calls that
    cleared went missing from the events once clearing moved inside the model call."""
    seen = runtime.SeenMessages()
    result = ToolMessage("ten results", tool_call_id="c1", name="web_search", id="t1")
    _events((), "values", {"messages": [HumanMessage("go", id="h1"), result]}, seen)
    reply = AIMessage(
        "", tool_calls=[{"name": "web_search", "args": {}, "id": "c2"}], id="a2",
        usage_metadata=USAGE,
    )
    written = [
        {"messages": [reply]},
        {"messages": [result.model_copy(update={"content": "handle"})]},
    ]

    events = _events((), "updates", {"model": written}, seen)

    assert [e.kind for e in events] == ["model_call"]


def test_each_graph_is_judged_by_its_own_conversation():
    """A subagent's conversation is another graph's, streamed under its namespace: what
    the main agent holds does not make a subagent's message a rewrite, nor the reverse."""
    seen = runtime.SeenMessages()
    result = ToolMessage("found", tool_call_id="c1", name="web_search", id="t1")
    _events((), "values", {"messages": [result]}, seen)
    _events(("task:1",), "values", {"messages": [HumanMessage("dig", id="s1")]}, seen)

    theirs = _events(("task:1",), "updates", {"tools": {"messages": [result]}}, seen)
    ours = _events((), "updates", {"clearing": {"messages": [result]}}, seen)

    assert [e.kind for e in theirs] == ["tool_result"]
    assert ours == [], "a subagent's conversation took the place of the agent's"


def test_without_the_stream_s_history_every_message_is_reported():
    """`events_in` called alone, as before this existed, still reports what it is
    handed: the history is the turn's to keep, and a caller with none gets no filter."""
    result = ToolMessage("found", tool_call_id="c1", name="web_search", id="t1")

    events = list(runtime.events_in((), "updates", {"tools": {"messages": [result]}}))

    assert [e.kind for e in events] == ["tool_result"]


TOOLS = '''
def bulky(n: int) -> str:
    """Return about 16,000 characters, under what deepagents offloads."""
    return "\\n".join(f"row {n}-{i:04d} " + "y" * 70 for i in range(200))


TOOLS = [bulky]
'''


def test_a_turn_with_a_clearing_pass_reports_each_call_and_result_once(scripted):
    """Through a real turn whose conversation passes a small clearing mark: one
    `model_call` per reply the model gave and one `tool_result` per call it made, while
    the transcript shows that results were cleared."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted)
    start(scripted, "s")
    models = scripted.models
    marked = replace(models.models[models.default], clear_results_at=12_000)
    entries = {**models.models, models.default: marked}
    cfg = replace(scripted, models=replace(models, models=entries))
    replies = [
        AIMessage(content="", tool_calls=[{"name": "bulky", "args": {"n": n}, "id": f"c{n}"}])
        for n in range(1, 7)
    ]
    Scripted.script.extend([*replies, AIMessage(content="done")])

    events = list(
        Kingfisher(cfg, backends=default_backends).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    transcript = (cfg.workspace / "sessions" / "s" / HARNESS / TRANSCRIPT_FILE).read_text()
    assert "bytes, cleared;" in transcript, "no pass ran, so nothing here was rewritten"
    assert sum(e.kind == "model_call" for e in events) == len(replies) + 1
    assert sum(e.kind == "tool_result" for e in events) == len(replies)

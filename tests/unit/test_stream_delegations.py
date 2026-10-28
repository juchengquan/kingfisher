"""Delegations side by side, told apart on the caller's stream.

Driven through real graphs rather than `tasks` chunks built here: which call a chunk
came from is read off the input langchain hands a `tools` task, and a fixture of that
shape would keep passing after langchain changed it.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.core.request import Decision, Request, Resume
from kingfisher.core.result import RunEvent
from kingfisher.core.session import runtime
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import EVERY_TOOL, FakeToolCallingModel, an_agent, start
from tests.unit.scripted import Scripted
from tests.unit.test_subagent_helpers import HELPER, REVIEWER, _define

#: What two runs of one subagent say, a word at a time.
SAID = ("a0 a1 a2 a3 a4", "b0 b1 b2 b3 b4")

class Slow(GenericFakeChatModel):
    """Streams a word at a time with a pause between, so two runs at once interleave."""

    def bind_tools(self, tools, **kwargs):
        return self

    def _stream(self, *args, **kwargs):
        for chunk in super()._stream(*args, **kwargs):
            time.sleep(0.01)
            yield chunk

    async def _astream(self, *args, **kwargs):
        async for chunk in super()._astream(*args, **kwargs):
            await asyncio.sleep(0.01)
            yield chunk


def _task(subagent: str, asked: str, at: str) -> dict[str, Any]:
    return {"name": "task", "args": {"description": asked, "subagent_type": subagent}, "id": at}


def _twice(cfg, session_dir) -> Any:
    """An agent starting `reviewer` twice in one message, then answering."""
    started = [
        _task("reviewer", "check the draft", "t1"),
        _task("reviewer", "check the tests", "t2"),
    ]
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[AIMessage(content="", tool_calls=started), AIMessage(content="THE ANSWER")]
        ),
        backend=backend_at(cfg, session_dir),
        subagents=[
            {
                "name": "reviewer",
                "description": "Checks.",
                "system_prompt": "You check.",
                "tools": [],
                "model": Slow(messages=iter([AIMessage(content=one) for one in SAID])),
            }
        ],
    )


def _drained(kf: Kingfisher, asked: Request | Resume) -> list[RunEvent]:
    return list(kf.stream(asked))


def _side_by_side(cfg, session_dir) -> list[RunEvent]:
    kf = Kingfisher(cfg, graph=_twice(cfg, session_dir), sessions=LocalSessions())
    return _drained(kf, Request("go", session_id=session_dir.name))


def test_two_runs_of_one_subagent_are_told_apart(cfg, session_dir):
    """Every token of both runs said `reviewer`, and the CLI printed the two answers
    merged word by word: `b0a0  b1a1  a2b2`."""
    events = _side_by_side(cfg, session_dir)

    said: dict[tuple[str, ...], str] = {}
    for event in events:
        if event.kind == "token" and event.agent == "reviewer":
            said[event.delegations] = said.get(event.delegations, "") + event.text

    assert set(said) == {("t1",), ("t2",)}
    # Which run got which reply is the scheduler's choice; that neither got a word of
    # the other's is the claim.
    assert sorted(said.values()) == sorted(SAID)


def test_a_delegation_is_announced_before_anything_inside_it(cfg, session_dir):
    """What lets the CLI decide to hold a run's output before any of it has printed."""
    events = _side_by_side(cfg, session_dir)

    starts = [e for e in events if e.kind == "delegation_start"]
    assert [(e.agent, e.delegations, e.text) for e in starts] == [
        ("reviewer", ("t1",), "check the draft"),
        ("reviewer", ("t2",), "check the tests"),
    ]
    first_inside = min(i for i, e in enumerate(events) if e.delegations and e not in starts)
    assert max(events.index(one) for one in starts) < first_inside


def test_the_result_ending_a_delegation_names_it(cfg, session_dir):
    """A `task` result said only `tool="task"`, so nothing said which run had finished."""
    events = _side_by_side(cfg, session_dir)

    ended = [e for e in events if e.kind == "tool_result" and e.tool == "task"]

    assert sorted((e.call, e.delegations) for e in ended) == [("t1", ()), ("t2", ())]


def test_the_main_agents_events_are_inside_no_delegation(cfg, session_dir):
    """`()` is the caller's agent, the control beside the chains above."""
    events = _side_by_side(cfg, session_dir)

    mains = [e for e in events if e.kind in ("model_call", "tool_result") and e.agent is None]

    assert len(mains) == 4
    assert {e.delegations for e in mains} == {()}


def test_a_helper_carries_the_whole_chain(scripted):
    """The innermost id alone would make two helpers of one subagent look alike again,
    and the outermost alone the same one level down."""
    _define(scripted, REVIEWER, HELPER)
    an_agent(scripted, subagents="[reviewer, second-opinion]", **EVERY_TOOL)
    start(scripted, "s")
    Scripted.script.extend(
        [
            AIMessage(content="", tool_calls=[_task("reviewer", "check it", "t-r")]),
            AIMessage(content="", tool_calls=[_task("second-opinion", "again", "t-h")]),
            AIMessage(content="HELPER-DONE"),
            AIMessage(content="REVIEWER-DONE"),
            AIMessage(content="done"),
        ]
    )

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    chains = {(e.kind, e.agent): e.delegations for e in events if e.kind != "token"}
    assert chains[("delegation_start", "reviewer")] == ("t-r",)
    assert chains[("delegation_start", "second-opinion")] == ("t-r", "t-h")
    assert chains[("model_call", "second-opinion")] == ("t-r", "t-h")
    assert chains[("model_call", "reviewer")] == ("t-r",)


def _gated_beside_ungated(cfg, session_dir) -> Any:
    """`scribe` stops at a gated write; `clerk`, started beside it, just answers."""
    write = {"name": "write_file", "args": {"file_path": "/outputs/s.txt", "content": "x"}}
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[_task("scribe", "write it", "p0"), _task("clerk", "say it", "p1")],
                ),
                AIMessage(content="parent done"),
            ]
        ),
        interrupt_on={"write_file": True},
        subagents=[
            {
                "name": "scribe",
                "description": "Writes.",
                "system_prompt": "Write.",
                "model": FakeToolCallingModel(
                    responses=[
                        AIMessage(content="", tool_calls=[{**write, "id": "d1"}]),
                        AIMessage(content="scribe done"),
                    ]
                ),
            },
            {
                "name": "clerk",
                "description": "Says.",
                "system_prompt": "Say.",
                "model": FakeToolCallingModel(responses=[AIMessage(content="clerk done")]),
            },
        ],
        checkpointer=InMemorySaver(),
        backend=backend_at(cfg, session_dir),
    )


def test_a_resumed_turn_names_the_delegation_it_re_enters(cfg, session_dir):
    """A resume repeats no `model_call`, and the CLI answers an approval in a new process:
    without the announcement nothing in the resumed stream would say what `p0` was."""
    kf = Kingfisher(cfg, graph=_gated_beside_ungated(cfg, session_dir), sessions=LocalSessions())
    paused = kf.run(Request("go", session_id=session_dir.name))
    assert paused.pending, "scribe never paused"

    answers = tuple(Decision(call_id=one.call_id, action="approve") for one in paused.pending)
    events = _drained(kf, Resume(session_id=paused.session_id, decisions=answers))

    starts = {e.delegations: e.agent for e in events if e.kind == "delegation_start"}
    assert starts[("p0",)] == "scribe"
    (scribes,) = [e for e in events if e.kind == "model_call" and e.agent == "scribe"]
    assert scribes.delegations == ("p0",)
    assert [e.call for e in events if e.kind == "tool_result" and e.tool == "task"][-1] == "p0"


# -- the map, where a real graph cannot reach -----------------------------------


def _started(call_id: str, *calls: dict[str, Any], on: str = "tools") -> dict[str, Any]:
    """A `tasks` start, in the shape the graphs above produce it."""
    return {"id": call_id, "name": on, "input": list(calls), "triggers": ()}


def test_a_segment_no_task_call_started_adds_nothing():
    """A supplied graph's own subgraph puts a segment in every chunk's namespace; read as
    a delegation, it would make every event look delegated."""
    delegations = runtime.Delegations()
    delegations.started((), _started("abc", _task("reviewer", "check", "t1")))

    assert delegations.within(("outer:zzz", "tools:abc")) == ("t1",)
    assert delegations.within(("outer:zzz",)) == ()


@pytest.mark.parametrize(
    "chunk",
    [
        _started("abc", {"name": "sql_query", "args": {}, "id": "c1"}),
        _started("abc", _task("reviewer", "a", "t1"), _task("reviewer", "b", "t2")),
        _started("abc", _task("reviewer", "a", "t1"), on="model"),
    ],
    ids=["another-tool", "two-calls-in-one-task", "another-node"],
)
def test_only_a_task_running_one_task_call_starts_a_delegation(chunk):
    """With two calls in one task, the task's id would stand for both, and every chunk
    inside would be filed under whichever was read last."""
    delegations = runtime.Delegations()

    assert delegations.started((), chunk) is None
    assert delegations.within(("tools:abc",)) == ()


def test_a_delegation_start_prints_as_its_header():
    """The line the CLI heads a held block with: the subagent and the first line of what
    it was asked, which is what tells two runs of one subagent apart."""
    event = RunEvent(
        kind="delegation_start",
        agent="reviewer",
        text="check the tests\n\nthen the docs, " + "x" * 300,
        delegations=("t2",),
    )

    assert str(event) == "[reviewer] check the tests"
    assert len(str(RunEvent(kind="delegation_start", agent="r", text="y" * 300))) < 120


def test_a_cut_reply_inside_a_delegation_says_which():
    """A reply cut at the output cap is reported on the `custom` stream, not the state,
    so it is the one `model_call` that does not come through `updates`."""
    delegations = runtime.Delegations()
    delegations.started((), _started("abc", _task("reviewer", "check", "t1")))
    reported = {runtime.CUT_OFF_MARK: {"input_tokens": 10, "output_tokens": 4}}

    (cut,) = runtime.events_in(("tools:abc",), "custom", reported, delegations=delegations)

    assert cut.cut_off
    assert cut.delegations == ("t1",)

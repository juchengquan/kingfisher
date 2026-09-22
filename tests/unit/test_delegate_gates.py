"""A gate reached from inside a delegate, and who the caller is told asked for it.

The half of approval gates that nothing drove. Every other gate test pauses at the
top level, where `_delegate_behind` returns on its first line because the task is not
the tool node -- so the branch that attributes a pause to a delegate, and the rule
that it declines to guess, shipped without running anywhere.

It is worth driving rather than asserting on a snapshot built here. What the pause
carries is decided by deepagents and langgraph between them: a delegate's interrupt
arrives on the *parent's* tool task, its own state is not reachable from the
top-level snapshot, and the delegate's name is nowhere in the interrupt payload. A
fixture written to match that description would keep passing after any of it changed.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.domain.request import Decision, Request, Resume
from kingfisher.domain.result import AWAITING, END_TURN
from tests.conftest import FakeToolCallingModel


def _writes(path: str, body: str, call_id: str) -> dict[str, Any]:
    return {"name": "write_file", "args": {"file_path": path, "content": body}, "id": call_id}


def _delegates_to(*names: str) -> AIMessage:
    """One parent message starting a delegate for each name, in one superstep."""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "task",
                "args": {"description": f"work for {name}", "subagent_type": name},
                "id": f"p{index}",
            }
            for index, name in enumerate(names)
        ],
    )


def _child(path: str) -> Any:
    """A delegate that proposes one gated write and then reports."""
    return FakeToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_writes(path, "from the delegate", "d1")]),
            AIMessage(content="delegate done"),
        ]
    )


def _parent_with(*names: str, backend: Any = None) -> Any:
    """An agent that hands off to `names`, gating `write_file` for itself and them."""
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[_delegates_to(*names), AIMessage(content="parent done")]
        ),
        tools=None,
        interrupt_on={"write_file": True},
        subagents=[
            {
                "name": name,
                "description": f"Writes, as {name}.",
                "system_prompt": "Write the file.",
                "model": _child(f"/derived/{name}.txt"),
            }
            for name in names
        ],
        checkpointer=InMemorySaver(),
        **({"backend": backend} if backend is not None else {}),
    )


def _session_dir(cfg, session_id: str):
    return cfg.workspace / "sessions" / session_id


def test_a_delegates_gated_call_stops_the_whole_turn(cfg):
    """A gate the parent never reaches still has to stop the turn, or work delegated
    away is work that escaped the gate -- which is the failure a workspace gating the
    shell would discover by having it run.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe"))

    result = kf.run(Request("delegate it"))

    assert result.stop_reason == AWAITING
    assert [call.tool for call in result.pending] == ["write_file"]
    assert result.pending[0].args["file_path"] == "/derived/scribe.txt"


def test_the_delegate_is_named_where_one_was_in_flight(cfg):
    """The attribution, which is the only part of a pause that says *who* asked.

    The name is not in the interrupt: it is read back off the `task` call in the
    parent's own last message. Nothing else in the suite exercises that.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe"))

    result = kf.run(Request("delegate it"))

    assert result.pending[0].agent == "scribe"


def test_a_top_level_gate_names_no_delegate(cfg):
    """The control beside it. Without this the assertion above passes against code
    that writes some fixed name onto every pause it sees.
    """
    kf = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(
                responses=[
                    AIMessage(content="", tool_calls=[_writes("/derived/own.txt", "x", "c1")]),
                    AIMessage(content="done"),
                ]
            ),
            tools=None,
            interrupt_on={"write_file": True},
            checkpointer=InMemorySaver(),
        ),
    )

    result = kf.run(Request("do it yourself"))

    assert result.pending[0].agent is None, "the agent the caller asked was named as a delegate"


def test_a_parents_own_call_is_not_blamed_on_a_delegate_it_started(cfg):
    """One message that both gates a call of the parent's own and starts a delegate.

    This is what the tool-node check is for, and nothing else reaches it. The name is
    read off the `task` call in the last message with tool calls -- and here that
    message holds the parent's gated `write_file` *and* a `task`, so without the check
    the parent's own call is attributed to the delegate it happened to start beside
    it. Exactly one delegate is in flight, so declining to guess does not save it.
    """
    kf = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            _writes("/derived/mine.txt", "the parent's", "c1"),
                            {
                                "name": "task",
                                "args": {"description": "go", "subagent_type": "scribe"},
                                "id": "p0",
                            },
                        ],
                    ),
                    AIMessage(content="parent done"),
                ]
            ),
            tools=None,
            interrupt_on={"write_file": True},
            subagents=[
                {
                    "name": "scribe",
                    "description": "Writes, as scribe.",
                    "system_prompt": "Write the file.",
                    "model": _child("/derived/scribe.txt"),
                }
            ],
            checkpointer=InMemorySaver(),
        ),
    )

    result = kf.run(Request("both at once"))

    mine = [call for call in result.pending if call.args["file_path"] == "/derived/mine.txt"]
    assert mine, "the parent's own gated call never paused"
    assert mine[0].agent is None, "the parent's own call was attributed to its delegate"


def test_two_delegates_in_flight_name_none_rather_than_guessing(cfg):
    """The rule that cost something to hold, and the reason it is worth a test.

    Both delegates pause in one superstep and the pauses arrive on the same tool task,
    with nothing in either saying which delegate it came from. Naming the first would
    be right half the time -- and a caller about to approve `rm -rf` being told the
    wrong actor asked for it is worse than being told none did. The tool and its
    arguments stay correct either way, which is what makes declining to guess cheap.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe", "clerk"))

    result = kf.run(Request("delegate both"))

    assert len(result.pending) >= 1, "neither delegate paused, so there is nothing to attribute"
    assert {call.agent for call in result.pending} == {None}
    # Still answerable, which is the point of refusing only the name.
    assert all(call.tool == "write_file" and call.call_id for call in result.pending)


def test_a_delegates_gate_is_answered_the_same_way(cfg, session_dir):
    """Approving it has to run the delegate's call, not the parent's -- the resume
    re-enters a nested graph, which is the part a top-level test cannot show.
    """
    from kingfisher import default_backend

    kf = Kingfisher(cfg, graph=_parent_with("scribe", backend=default_backend(cfg, session_dir)))
    paused = kf.run(Request("delegate it", session_id=session_dir.name))
    assert paused.pending, "the delegate never paused"

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=tuple(
                Decision(call_id=call.call_id, action="approve") for call in paused.pending
            ),
        )
    )

    assert answered.stop_reason == END_TURN
    assert (session_dir / "derived" / "scribe.txt").read_text() == "from the delegate"


def test_rejecting_a_delegates_call_leaves_it_unrun(cfg, session_dir):
    """The half a test asserting only on the turn's end would miss."""
    from kingfisher import default_backend

    kf = Kingfisher(cfg, graph=_parent_with("scribe", backend=default_backend(cfg, session_dir)))
    paused = kf.run(Request("delegate it", session_id=session_dir.name))

    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=tuple(
                Decision(call_id=call.call_id, action="reject") for call in paused.pending
            ),
        )
    )

    assert not (session_dir / "derived" / "scribe.txt").exists()

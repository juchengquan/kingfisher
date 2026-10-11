"""A gate reached from inside a subagent, and who the caller is told asked for it.

The half of approval gates that nothing drove. Every other gate test pauses at the
top level, where `_subagent_behind` returns on its first line because the task is not
the tool node -- so the branch that attributes a pause to a subagent, and the rule
that it declines to guess, shipped without running anywhere.

It is worth driving rather than asserting on a snapshot built here. What the pause
carries is decided by deepagents and langgraph between them: a subagent's interrupt
arrives on the *parent's* tool task, its own state is not reachable from the
top-level snapshot, and the subagent's name is nowhere in the interrupt payload. A
fixture written to match that description would keep passing after any of it changed.
"""

from __future__ import annotations

from typing import Any

from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher, default_backends
from kingfisher.domain.request import Decision, Request, Resume
from kingfisher.domain.result import AWAITING, END_TURN
from tests.conftest import FakeToolCallingModel


def _writes(path: str, body: str, call_id: str) -> dict[str, Any]:
    return {"name": "write_file", "args": {"file_path": path, "content": body}, "id": call_id}


def _delegates_to(*names: str) -> AIMessage:
    """One parent message starting a subagent for each name, in one superstep."""
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
    """A subagent that proposes one gated write and then reports."""
    return FakeToolCallingModel(
        responses=[
            AIMessage(content="", tool_calls=[_writes(path, "from the subagent", "d1")]),
            AIMessage(content="subagent done"),
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
                "model": _child(f"/outputs/{name}.txt"),
            }
            for name in names
        ],
        checkpointer=InMemorySaver(),
        **({"backend": backend} if backend is not None else {}),
    )


def _session_dir(cfg, session_id: str):
    return cfg.workspace / "sessions" / session_id


def test_a_subagents_gated_call_stops_the_whole_turn(cfg):
    """A gate the parent never reaches still has to stop the turn, or work delegated
    away is work that escaped the gate -- which is the failure a workspace gating the
    shell would discover by having it run.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe"), backends=default_backends)

    result = kf.run(Request("delegate it"))

    assert result.stop_reason == AWAITING
    assert [call.tool for call in result.pending] == ["write_file"]
    assert result.pending[0].args["file_path"] == "/outputs/scribe.txt"


def test_the_subagent_is_named_where_one_was_in_flight(cfg):
    """The attribution, which is the only part of a pause that says *who* asked.

    The name is not in the interrupt: it is read back off the `task` call in the
    parent's own last message. Nothing else in the suite exercises that.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe"), backends=default_backends)

    result = kf.run(Request("delegate it"))

    assert result.pending[0].agent == "scribe"


def test_a_top_level_gate_names_no_subagent(cfg):
    """The control beside it. Without this the assertion above passes against code
    that writes some fixed name onto every pause it sees.
    """
    kf = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(
                responses=[
                    AIMessage(content="", tool_calls=[_writes("/outputs/own.txt", "x", "c1")]),
                    AIMessage(content="done"),
                ]
            ),
            tools=None,
            interrupt_on={"write_file": True},
            checkpointer=InMemorySaver(),
        ),
        backends=default_backends,
    )

    result = kf.run(Request("do it yourself"))

    assert result.pending[0].agent is None, "the agent the caller asked was named as a subagent"


def test_a_parents_own_call_is_not_blamed_on_a_subagent_it_started(cfg):
    """One message that both gates a call of the parent's own and starts a subagent.

    This is what the tool-node check is for, and nothing else reaches it. The name is
    read off the `task` call in the last message with tool calls -- and here that
    message holds the parent's gated `write_file` *and* a `task`, so without the check
    the parent's own call is attributed to the subagent it happened to start beside
    it. Exactly one subagent is in flight, so declining to guess does not save it.
    """
    kf = Kingfisher(
        cfg,
        graph=create_deep_agent(
            model=FakeToolCallingModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            _writes("/outputs/mine.txt", "the parent's", "c1"),
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
                    "model": _child("/outputs/scribe.txt"),
                }
            ],
            checkpointer=InMemorySaver(),
        ),
        backends=default_backends,
    )

    result = kf.run(Request("both at once"))

    mine = [call for call in result.pending if call.args["file_path"] == "/outputs/mine.txt"]
    assert mine, "the parent's own gated call never paused"
    assert mine[0].agent is None, "the parent's own call was attributed to its subagent"


def test_two_subagents_in_flight_name_none_rather_than_guessing(cfg):
    """The rule that cost something to hold, and the reason it is worth a test.

    Both subagents pause in one superstep and the pauses arrive on the same tool task,
    with nothing in either saying which subagent it came from. Naming the first would
    be right half the time -- and a caller about to approve `rm -rf` being told the
    wrong actor asked for it is worse than being told none did. The tool and its
    arguments stay correct either way, which is what makes declining to guess cheap.
    """
    kf = Kingfisher(cfg, graph=_parent_with("scribe", "clerk"), backends=default_backends)

    result = kf.run(Request("subagent both"))

    assert len(result.pending) >= 1, "neither subagent paused, so there is nothing to attribute"
    assert {call.agent for call in result.pending} == {None}
    # Still answerable, which is the point of refusing only the name.
    assert all(call.tool == "write_file" and call.call_id for call in result.pending)


def test_a_subagents_gate_is_answered_the_same_way(cfg, session_dir):
    """Approving it has to run the subagent's call, not the parent's -- the resume
    re-enters a nested graph, which is the part a top-level test cannot show.
    """
    from kingfisher import backend_at

    kf = Kingfisher(
        cfg,
        graph=_parent_with("scribe", backend=backend_at(cfg, session_dir)),
        backends=default_backends,
    )
    paused = kf.run(Request("delegate it", session_id=session_dir.name))
    assert paused.pending, "the subagent never paused"

    answered = kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=tuple(
                Decision(call_id=call.call_id, action="approve") for call in paused.pending
            ),
        )
    )

    assert answered.stop_reason == END_TURN
    assert (session_dir / "outputs" / "scribe.txt").read_text() == "from the subagent"


def test_rejecting_a_subagents_call_leaves_it_unrun(cfg, session_dir):
    """The half a test asserting only on the turn's end would miss."""
    from kingfisher import backend_at

    kf = Kingfisher(
        cfg,
        graph=_parent_with("scribe", backend=backend_at(cfg, session_dir)),
        backends=default_backends,
    )
    paused = kf.run(Request("delegate it", session_id=session_dir.name))

    kf.run(
        Resume(
            session_id=paused.session_id,
            decisions=tuple(
                Decision(call_id=call.call_id, action="reject") for call in paused.pending
            ),
        )
    )

    assert not (session_dir / "outputs" / "scribe.txt").exists()

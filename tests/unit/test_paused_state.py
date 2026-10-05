"""What a turn that stopped for an answer leaves behind, and what picks it up again.

A turn paused at an approval gate ends, and the graph state it stopped in then has to
outlive the saver holding it -- the one thing `InMemorySaver` does not do. Everything
here drives a real graph through a real `interrupt()` rather than asserting on the
mappings: a test written against a fixture of its own making stays green through an
upstream change to the shapes it is copying, which is the failure this file exists to
avoid.
"""

from __future__ import annotations

import pickle
from typing import Any

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, StateGraph
from langgraph.types import Command, interrupt
from typing_extensions import TypedDict

from kingfisher.infrastructure.harness.checkpointing import paused_state, resumed_saver
from kingfisher.infrastructure.steps import drive
from kingfisher.layout import PAUSED_STATE
from tests.conftest import harness_of, start


class _State(TypedDict):
    asked: str
    answered: str
    #: Written by a task running beside the gate, never by the gate itself -- two
    #: nodes writing one key in a superstep is an update conflict, not a pause.
    sibling: str


def _gated_graph(saver: Any) -> Any:
    """A graph that stops for an answer, which is the shape an approval gate has."""

    def gate(state: _State) -> dict[str, Any]:
        return {"answered": interrupt({"for": state["asked"]})}

    # ty reads langgraph's `StateT` bound as unsatisfied by a `TypedDict` under
    # `from __future__ import annotations`. The graph compiles and runs; the
    # limitation is in the checker's model of the bound.
    builder: Any = StateGraph(_State)  # ty: ignore[invalid-argument-type]
    builder.add_node("gate", gate)
    builder.add_edge(START, "gate")
    return builder.compile(checkpointer=saver)


def _thread(name: str) -> Any:
    return {"configurable": {"thread_id": name}}


def test_a_pause_survives_the_saver_that_held_it():
    """Without this the resume finds an empty graph and asks the human all over again."""

    holding = InMemorySaver()
    stopped = _gated_graph(holding).invoke({"asked": "rm -rf /"}, config=_thread("s1"))
    # Asserted, so the half below cannot pass by the graph never having stopped.
    assert "__interrupt__" in stopped, "the graph ran to the end and paused nothing"
    state = paused_state(holding)

    resumed = _gated_graph(resumed_saver(state)).invoke(
        Command(resume="approve"), config=_thread("s1")
    )

    assert resumed["answered"] == "approve"
    assert resumed["asked"] == "rm -rf /", "the state the pause happened in did not carry"


def test_work_finished_beside_the_pause_is_not_done_twice():
    """A sibling task that completed in the superstep the gate stopped runs again on
    resume, unless the pending writes were kept.

    Which is the case an approval gate is *for*: a turn pauses on one tool call while
    another in the same step has already run, and re-running it means a tool firing
    twice for one decision. The state both paths end in is identical -- only the side
    effect tells them apart, so a test asserting on the result would never see it.
    """
    ran: list[str] = []

    def build(saver: Any) -> Any:
        def sibling(_state: _State) -> dict[str, Any]:
            ran.append("sibling")
            return {"sibling": "done"}

        def gate(state: _State) -> dict[str, Any]:
            return {"answered": interrupt({"for": state["asked"]})}

        builder: Any = StateGraph(_State)  # ty: ignore[invalid-argument-type]
        builder.add_node("sibling", sibling)
        builder.add_node("gate", gate)
        builder.add_edge(START, "sibling")
        builder.add_edge(START, "gate")
        return builder.compile(checkpointer=saver)

    holding = InMemorySaver()
    build(holding).invoke({"asked": "rm -rf /"}, config=_thread("s1"))
    assert ran == ["sibling"], "the sibling never ran, so re-running it cannot be seen"
    state = paused_state(holding)

    ran.clear()
    build(resumed_saver(state)).invoke(Command(resume="approve"), config=_thread("s1"))

    assert ran == [], "the finished sibling ran a second time on resume"


def test_a_resume_reaches_the_right_thread():
    """One file holds one session's threads, and a thread it does not hold is not a
    pause that silently answers itself.
    """
    holding = InMemorySaver()
    _gated_graph(holding).invoke({"asked": "one"}, config=_thread("s1"))
    state = paused_state(holding)

    elsewhere = _gated_graph(resumed_saver(state)).invoke(
        {"asked": "two"}, config=_thread("s2")
    )

    assert "__interrupt__" in elsewhere, "another thread resumed on this thread's answer"


def test_a_checkpoint_is_msgpack_and_a_pickle_is_refused_on_the_way_back():
    """`.harness` is kept from the shell by a macOS profile a deployment can switch off,
    so what is written there has to be a format that is only ever data.
    """
    holding = InMemorySaver()
    _gated_graph(holding).invoke({"asked": "x"}, config=_thread("s1"))
    state = paused_state(holding)

    assert state.split(b"\n", 1)[0] == b"msgpack"

    # The control beside the claim: the serialiser *refuses* a pickle rather than
    # merely declining to write one, so a file rewritten to claim that type is still
    # not code. Without this, the assertion above passes against a reader that would
    # happily unpickle whatever it was handed.
    with pytest.raises(NotImplementedError):
        resumed_saver(b"pickle\n" + pickle.dumps({"storage": [], "writes": [], "blobs": []}))


def test_no_part_of_a_checkpoint_resumes():
    """It was written aside and renamed into place, so a write that died left the one
    before it whole. Through a backend there is no rename, and what stands in for it is
    the format: every prefix of a paused state fails to deserialise rather than
    resuming from part of one. Every length, because a half is one case of thousands.
    """
    holding = InMemorySaver()
    _gated_graph(holding).invoke({"asked": "first"}, config=_thread("s1"))
    state = paused_state(holding)

    for cut in range(len(state)):
        with pytest.raises((ValueError, NotImplementedError)):
            resumed_saver(state[:cut])


def test_no_checkpoint_is_not_an_error(cfg):
    """Every ordinary turn ends without one, and the resume path asks regardless."""
    start(cfg, "s")
    assert drive(harness_of(cfg, "s").fetch(PAUSED_STATE)) is None


def test_a_turn_that_neither_paused_nor_resumed_deletes_nothing_at_its_end(cfg, monkeypatch):
    """Every turn's ending dropped the pause files whether or not there had been a
    pause: two deletes and a read to check them, three round trips on a remote backend
    out of the seven an ending made, for nothing.
    """
    from deepagents.backends import CompositeBackend

    from kingfisher import Kingfisher, Request, default_backends
    from tests.conftest import StubCheckpointer
    from tests.unit.test_run import StubAgent

    deleted: list[str] = []
    deleting = CompositeBackend.delete

    def recorded(self, path):
        deleted.append(path)
        return deleting(self, path)

    monkeypatch.setattr(CompositeBackend, "delete", recorded)
    start(cfg, "s")
    kf = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
    )

    assert kf.run(Request("go", session_id="s")).completed
    assert deleted == []

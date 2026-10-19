"""A conversation lives inside the session it belongs to.

One session used to be one logical thing kept in two stores keyed by the same id with
nothing linking them: a directory, and a row set in a database shared by every
session. Everything that went wrong with retention came from that seam -- `discard`
had to delete both in the right order, sessions that could not be removed left their
threads behind, and a directory deleted any other way orphaned its thread forever,
132 of them in one real workspace.
"""

from __future__ import annotations

from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.core.config import Config
from kingfisher.core.layout import HARNESS, TRANSCRIPT_FILE
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions, backend_at
from kingfisher.plugins.sessions.folders import session_bytes
from tests.conftest import FakeToolCallingModel, Through, an_agent
from tests.unit.scripted import Scripted
from tests.unit.test_run import StubAgent

TRANSCRIPT = f"{HARNESS}/{TRANSCRIPT_FILE}"


def _session_dir(cfg: Config, session_id: str):
    return cfg.workspace / "sessions" / session_id


# -- where it lives -------------------------------------------------------


def test_the_conversation_is_a_file_inside_the_session(cfg):
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())

    result = kf.run(Request("go"))

    assert (_session_dir(cfg, result.session_id) / TRANSCRIPT).is_file()


def test_the_conversation_counts_against_the_session_quota(cfg):
    """`session_max_bytes` measures a directory, so checkpoint state was invisible to it
    while it sat above every session -- the same blind spot the tool caches had
    before `HOME` moved into the session.
    """
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    result = kf.run(Request("go"))

    directory = _session_dir(cfg, result.session_id)
    counted = session_bytes(directory)

    assert counted >= (directory / TRANSCRIPT).stat().st_size > 0


# -- what it has to keep doing -------------------------------------------


def test_a_real_graph_carries_the_conversation_within_a_saver(cfg, session_dir):
    """The whole reason a checkpointer exists."""
    from langchain_core.messages import AIMessage

    from kingfisher.core.agent.assembly import build_agent
    from kingfisher.core.session.checkpointing import build_session_checkpointer
    from tests.conftest import FakeToolCallingModel

    saver = build_session_checkpointer()
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        checkpointer=saver,
        model=FakeToolCallingModel(responses=[AIMessage(content="one"), AIMessage(content="two")]),
    ).graph
    config: Any = {"configurable": {"thread_id": session_dir.name}, "recursion_limit": 10}

    graph.invoke({"messages": [("user", "remember this")]}, config=config)
    second = graph.invoke({"messages": [("user", "and now?")]}, config=config)

    # The second turn saw the first: continuity is what the store buys.
    assert len(second["messages"]) > 2, "the conversation did not carry"

    # Within one turn the saver is what carries the conversation between
    # supersteps, and that is all it now has to do -- across turns the transcript
    # carries it, and nothing here outlives the turn that made it.


def test_two_sessions_keep_separate_conversations(scripted):
    """A transcript shared across sessions would replay one caller's exchange to another."""
    an_agent(scripted, "plain")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.extend([AIMessage(content="answer one"), AIMessage(content="answer two")])

    kf.run(Request("question one", agent="plain", session_id="one"))
    kf.run(Request("question two", agent="plain", session_id="two"))

    one = (_session_dir(scripted, "one") / TRANSCRIPT).read_text(encoding="utf-8")
    two = (_session_dir(scripted, "two") / TRANSCRIPT).read_text(encoding="utf-8")
    assert "question one" in one and "question two" not in one, one
    assert "question two" in two and "question one" not in two, two


def test_deleting_a_session_takes_its_conversation_with_it(cfg, way):
    """No shared checkpointer involved, which is the point."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    result = kf.run(Request("go"))
    directory = _session_dir(cfg, result.session_id)
    assert (directory / TRANSCRIPT).is_file()

    assert Through(kf, way).delete(result.session_id) is None

    assert not directory.exists()


def test_a_sweep_needs_no_thread_store_at_all(cfg):
    """`reap` deleted threads because they lived elsewhere."""
    import time

    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    result = kf.run(Request("go"))

    swept = kf.reap(older_than_seconds=0, now=time.time())

    assert result.session_id in swept.removed
    assert swept.failures == ()


# -- or no conversation at all --------------------------------------------


def test_conversation_can_be_turned_off_entirely(cfg):
    """A graph takes `checkpointer=None` and runs; the turn simply starts cold."""
    from dataclasses import replace as replace_cfg

    stateless = replace_cfg(cfg, conversation_enabled=False)
    kf = Kingfisher(stateless, graph=StubAgent("ok"), sessions=LocalSessions())

    result = kf.run(Request("go"))

    assert result.answer == "ok"
    # Conversation off means nothing is remembered between turns, so there is
    # no transcript either -- the file and the flag say the same thing.
    assert not (_session_dir(cfg, result.session_id) / TRANSCRIPT).exists()


def test_files_survive_a_stateless_turn(cfg):
    """"Stateless" is about the conversation, not the session."""
    from dataclasses import replace as replace_cfg

    stateless = replace_cfg(cfg, conversation_enabled=False)
    kf = Kingfisher(stateless, graph=StubAgent("ok"), sessions=LocalSessions())
    first = kf.run(Request("go"))
    directory = _session_dir(cfg, first.session_id)
    (directory / "outputs" / "kept.txt").write_text("still here", encoding="utf-8")

    second = kf.run(Request("again", session_id=first.session_id))

    assert second.session_id == first.session_id
    assert (directory / "outputs" / "kept.txt").read_text() == "still here"


def test_with_conversation_off_a_turn_runs_on_no_saver(cfg):
    """A saver nobody reads from would be state kept for nothing, and a gated agent
    would find somewhere to pause that no resume could reach."""
    from dataclasses import replace as replace_cfg

    stateless = replace_cfg(cfg, conversation_enabled=False)
    service = Kingfisher(stateless, graph=StubAgent("ok"), sessions=LocalSessions())

    assert service._checkpointer() is None


def test_conversation_is_on_unless_a_deployment_says_otherwise(cfg):
    """A session that forgets is a surprising default for something that issues session
    ids.
    """
    assert cfg.conversation_enabled is True


def test_a_turns_working_state_does_not_reach_the_next_one(cfg, session_dir):
    """What the conversation carries, and what it deliberately does not.

    It holds because the saver is built per turn -- `build_session_checkpointer` in
    `_checkpointer`, released when the turn ends -- so the next turn's graph starts
    with nothing in its channels. That is structural rather than enforced, which is
    why it is worth a test: a saver kept across turns takes it back, and nothing else
    would say so.

    One turn's state *is* kept now, and only one: a turn that stopped at an approval
    gate, so a `Resume` can continue the graph it stopped in. That is the whole of
    the exception -- this turn ends normally and keeps nothing, which is what stops
    the exception quietly becoming the rule.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.core.agent.assembly import build_agent
    from kingfisher.core.session.checkpointing import build_session_checkpointer
    from tests.conftest import FakeToolCallingModel

    config: Any = {"configurable": {"thread_id": session_dir.name}, "recursion_limit": 10}
    plan = [{"content": "read the file", "status": "pending"}]

    first_saver = build_session_checkpointer()
    build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        checkpointer=first_saver,
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "write_todos", "args": {"todos": plan}, "id": "t1"}],
                ),
                AIMessage(content="planned"),
            ]
        ),
    ).graph.invoke({"messages": [("user", "plan it")]}, config=config)

    # Asserted, so the second half cannot pass by the plan never being written.
    written = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        checkpointer=first_saver,
        model=FakeToolCallingModel(responses=[]),
    ).graph.get_state(config)
    assert written.values.get("todos") == plan, "the first turn never wrote a plan"

    second_saver = build_session_checkpointer()
    assert second_saver is not first_saver, "a turn reused the previous turn's saver"

    carried = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        checkpointer=second_saver,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    ).graph.get_state(config)

    assert not carried.values.get("todos"), (
        "last turn's plan reached this one -- the agent resumes holding a checklist "
        "it did not write, for a task the caller may have dropped"
    )


def test_a_follow_up_turn_hands_the_model_the_earlier_exchange_once(scripted):
    """A saver that remembered earlier turns, beside the transcript replayed into each
    one, showed the model every earlier exchange twice -- measured, on a store a
    deployment passed as `threads=`."""
    an_agent(scripted, "plain")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.extend([AIMessage(content="first answer"), AIMessage(content="second")])
    kf.run(Request("first question", agent="plain", session_id="p"))
    Scripted.heard.clear()

    kf.run(Request("second question", agent="plain", session_id="p"))

    said = [str(message.content) for message in Scripted.heard[-1]]
    assert sum("first question" in text for text in said) == 1, said
    assert said.count("first answer") == 1, said


class _Hearing(FakeToolCallingModel):
    """A scripted model that keeps what each of its calls was shown."""

    heard: list[list[str]] = []

    def _generate(self, messages, *args, **kwargs):
        type(self).heard.append([str(message.content) for message in messages])
        return super()._generate(messages, *args, **kwargs)


@pytest.mark.parametrize("saver", [InMemorySaver, None], ids=["its own saver", "no saver"])
def test_a_pre_built_graph_hands_the_model_the_earlier_exchange_once(cfg, saver):
    """Compiled with a saver of its own, a supplied graph showed the model every earlier
    exchange twice; compiled with none, it left no state to write the transcript from and
    showed it nothing -- both measured."""
    _Hearing.heard = []
    model = _Hearing(responses=[AIMessage(content="first answer"), AIMessage(content="second")])
    graph = create_deep_agent(
        model=model, tools=None, checkpointer=saver() if saver is not None else None
    )
    kf = Kingfisher(cfg, graph=graph, sessions=LocalSessions())
    kf.run(Request("first question", session_id="g"))

    kf.run(Request("second question", session_id="g"))

    said = _Hearing.heard[-1]
    assert sum("first question" in text for text in said) == 1, said
    assert said.count("first answer") == 1, said

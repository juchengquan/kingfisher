"""A conversation lives inside the session it belongs to.

One session used to be one logical thing kept in two stores keyed by the same
id with nothing linking them: a directory, and a row set in a database shared by
every session. Everything that went wrong with retention came from that seam --
`discard` had to delete both in the right order, sessions that could not be
removed left their threads behind, and a directory deleted any other way
orphaned its thread forever, 132 of them in one real workspace.

The file is a *transcript* now rather than a checkpoint database, and every
claim below survived that: it is still one file in the session directory, still
deleted with it, still counted by the quota, still separate per session. What
changed is what is in it — kingfisher's own message records rather than
langgraph's resumable graph state, because a graph is never resumed here. See
`domain.transcript`.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kingfisher import Kingfisher
from kingfisher.config import Config
from kingfisher.domain.request import Request
from kingfisher.infrastructure.session_store import TRANSCRIPT
from kingfisher.infrastructure.workspace.fs import session_bytes
from tests.conftest import StubCheckpointer
from tests.unit.test_async import AsyncStubAgent
from tests.unit.test_run import StubAgent


def _session_dir(cfg: Config, session_id: str):
    return cfg.workspace / "sessions" / session_id


# -- where it lives -------------------------------------------------------


def test_the_conversation_is_a_file_inside_the_session(cfg):
    kf = Kingfisher(cfg, graph=StubAgent("ok"))

    result = kf.run(Request("go"))

    assert (_session_dir(cfg, result.session_id) / TRANSCRIPT).is_file()


def test_nothing_is_written_to_a_workspace_wide_database(cfg):
    """The shared file is what orphans came from. If one is still being opened,
    this whole change bought nothing."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"))

    kf.run(Request("go"))

    assert not (cfg.state_dir / "threads.db").exists()


def test_the_conversation_counts_against_the_session_quota(cfg):
    """`session_max_bytes` measures a directory, so checkpoint state was
    invisible to it while it sat above every session -- the same blind spot the
    tool caches had before `HOME` moved into the session."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"))
    result = kf.run(Request("go"))

    directory = _session_dir(cfg, result.session_id)
    counted = session_bytes(directory)

    assert counted >= (directory / TRANSCRIPT).stat().st_size > 0


# -- what it has to keep doing -------------------------------------------


def test_a_real_graph_checkpoints_into_the_session_database(cfg, session_dir):
    """The whole reason a checkpointer exists. Moving where it lives must not
    change what it does.

    Driven through a real graph rather than `StubAgent`, which replaces the
    graph outright and so never reaches a checkpointer at all -- the first
    version of this test asserted against a database nothing had written to.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from kingfisher.infrastructure.harness.checkpointing import build_session_checkpointer
    from tests.conftest import FakeToolCallingModel

    saver = build_session_checkpointer(session_dir)
    graph = build_agent(
        cfg,
        session_dir=session_dir,
        checkpointer=saver,
        model=FakeToolCallingModel(responses=[AIMessage(content="one"), AIMessage(content="two")]),
    )
    config: Any = {"configurable": {"thread_id": session_dir.name}, "recursion_limit": 10}

    graph.invoke({"messages": [("user", "remember this")]}, config=config)
    second = graph.invoke({"messages": [("user", "and now?")]}, config=config)

    # The second turn saw the first: continuity is what the store buys.
    assert len(second["messages"]) > 2, "the conversation did not carry"

    # Within one turn the saver is what carries the conversation between
    # supersteps, and that is all it now has to do -- across turns the transcript
    # carries it, and nothing here outlives the turn that made it.


def test_two_sessions_keep_separate_conversations(cfg):
    """Structural, not enforced: they are different files."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"))

    one, two = kf.run(Request("a")), kf.run(Request("b"))

    assert one.session_id != two.session_id
    assert (_session_dir(cfg, one.session_id) / TRANSCRIPT) != (
        _session_dir(cfg, two.session_id) / TRANSCRIPT
    )


def test_deleting_a_session_takes_its_conversation_with_it(cfg):
    """No `ThreadStore` involved, which is the point. An orphaned thread is not
    something the janitor cleans up here -- it is something that cannot happen.
    """
    kf = Kingfisher(cfg, graph=StubAgent("ok"))
    result = kf.run(Request("go"))
    directory = _session_dir(cfg, result.session_id)
    assert (directory / TRANSCRIPT).is_file()

    assert kf.delete_session(result.session_id) is None

    assert not directory.exists()


# -- the async path, which is the reason this reaches an API --------------


def test_astream_works_with_nothing_injected(cfg):
    """It did not before. `SqliteSaver.aget_tuple` raises `NotImplementedError`,
    so an async deployment had to pass its own saver -- and passing an instance
    means one database shared by every session, which is the contention this
    exists to avoid.
    """
    kf = Kingfisher(cfg, graph=AsyncStubAgent("ok"))

    async def go() -> str | None:
        session_id = None
        async for event in kf.astream(Request("go")):
            if event.kind == "finished":
                session_id = event.result.session_id
        return session_id

    session_id = asyncio.run(go())

    assert session_id is not None
    assert (_session_dir(cfg, session_id) / TRANSCRIPT).is_file()


def test_the_async_saver_actually_supports_async(cfg, session_dir):
    """The test above drives `AsyncStubAgent`, which replaces the graph -- so it
    never touches the saver, and it passed even with the async resolver swapped
    for the sync one. Mutation testing found that; this is the assertion that
    catches it.

    `SqliteSaver.aget_tuple` raises `NotImplementedError`, so calling it is the
    difference between a saver an event loop can use and one it cannot.
    """
    from contextlib import AsyncExitStack

    service = Kingfisher(cfg, graph=StubAgent("ok"))

    async def resolve_and_use() -> object:
        async with AsyncExitStack() as stack:
            saver = await service._async_checkpointer_for(stack, session_dir)
            return await saver.aget_tuple(
                {"configurable": {"thread_id": session_dir.name, "checkpoint_ns": ""}}
            )
        return None

    # No exception is the assertion: a sync saver refuses this outright.
    assert asyncio.run(resolve_and_use()) is None


# -- who owns the connection ---------------------------------------------


def test_an_injected_store_is_used_as_it_is_and_not_closed(cfg):
    """A deployment's own store outlives every turn. Closing it after one would
    break the next."""
    store = StubCheckpointer()
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=store)

    kf.run(Request("go"))
    kf.run(Request("go"))

    assert kf.threads is store
    # The injected store is what the graph ran on; the transcript is written
    # regardless, because it is kingfisher's record rather than the saver's.
    assert all((p / TRANSCRIPT).is_file() for p in (cfg.workspace / "sessions").iterdir())


def test_a_factory_is_asked_once_per_session(cfg):
    """The seam an async deployment uses: given a session, hand back a saver."""
    seen: list[str] = []

    def factory(session_dir):
        seen.append(session_dir.name)
        return StubCheckpointer()

    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=factory)
    first = kf.run(Request("a"))
    kf.run(Request("b", session_id=first.session_id))

    assert seen == [first.session_id, first.session_id]


def test_the_connection_does_not_outlive_the_turn(cfg):
    """A database per session is a file descriptor per session. A process
    serving many would otherwise hold every one it had ever touched."""
    closed: list[object] = []

    class Recorder(StubCheckpointer):
        def __init__(self) -> None:
            super().__init__()
            self.conn = self

        def close(self) -> None:
            closed.append(self)

    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=lambda _dir: Recorder())
    kf.run(Request("go"))

    assert len(closed) == 1, "the saver this service opened was not released"


def test_a_sweep_needs_no_thread_store_at_all(cfg):
    """`reap` deleted threads because they lived elsewhere. They do not any
    more, so the sweep is one `rmtree` and the reconciliation finds nothing."""
    import time

    kf = Kingfisher(cfg, graph=StubAgent("ok"))
    result = kf.run(Request("go"))

    swept = kf.reap(older_than_seconds=0, now=time.time())

    assert result.session_id in swept.removed
    assert swept.orphans == ()
    assert swept.failures == ()


@pytest.mark.parametrize("injected", [None, "factory"])
def test_the_default_and_a_factory_both_survive_two_turns(cfg, injected):
    """The two shapes this service opens for itself, driven rather than
    inspected."""
    threads = None if injected is None else (lambda _dir: StubCheckpointer())
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=threads)

    first = kf.run(Request("one"))
    second = kf.run(Request("two", session_id=first.session_id))

    assert second.answer == "ok"


# -- or no conversation at all --------------------------------------------


def test_conversation_can_be_turned_off_entirely(cfg):
    """A graph takes `checkpointer=None` and runs; the turn simply starts cold.

    For a request/response API that is the whole state story: no database, so
    nothing to contend on, orphan, or vacuum.
    """
    from dataclasses import replace as replace_cfg

    stateless = replace_cfg(cfg, conversation_enabled=False)
    kf = Kingfisher(stateless, graph=StubAgent("ok"))

    result = kf.run(Request("go"))

    assert result.answer == "ok"
    # Conversation off means nothing is remembered between turns, so there is
    # no transcript either -- the file and the flag say the same thing.
    assert not (_session_dir(cfg, result.session_id) / TRANSCRIPT).exists()
    assert not any((cfg.workspace / "sessions").rglob("*threads.db*"))


def test_files_survive_a_stateless_turn(cfg):
    """"Stateless" is about the conversation, not the session. `/derived` and
    `/memory` are on disk, and a resumed session still finds them -- so
    `--session` keeps naming the same files while the agent starts cold.
    """
    from dataclasses import replace as replace_cfg

    stateless = replace_cfg(cfg, conversation_enabled=False)
    kf = Kingfisher(stateless, graph=StubAgent("ok"))
    first = kf.run(Request("go"))
    directory = _session_dir(cfg, first.session_id)
    (directory / "derived" / "kept.txt").write_text("still here", encoding="utf-8")

    second = kf.run(Request("again", session_id=first.session_id))

    assert second.session_id == first.session_id
    assert (directory / "derived" / "kept.txt").read_text() == "still here"


def test_the_flag_wins_over_an_injected_store(cfg):
    """A deployment that says it wants no conversation means it whatever it
    wired earlier -- otherwise the flag would be advisory."""
    from dataclasses import replace as replace_cfg

    store = StubCheckpointer()
    stateless = replace_cfg(cfg, conversation_enabled=False)
    service = Kingfisher(stateless, graph=StubAgent("ok"), threads=store)

    saver, release = service._checkpointer_for(_session_dir(cfg, "anything"))

    assert saver is None
    assert release is None


def test_the_async_path_honours_it_too(cfg, session_dir):
    """Otherwise a deployment would be stateless on one entry point and not the
    other, which is the kind of gap that only shows up in the path nobody
    tested."""
    from contextlib import AsyncExitStack
    from dataclasses import replace as replace_cfg

    service = Kingfisher(replace_cfg(cfg, conversation_enabled=False), graph=StubAgent("ok"))

    async def resolve() -> object:
        async with AsyncExitStack() as stack:
            return await service._async_checkpointer_for(stack, session_dir)

    assert asyncio.run(resolve()) is None


def test_conversation_is_on_unless_a_deployment_says_otherwise(cfg):
    """A session that forgets is a surprising default for something that issues
    session ids."""
    assert cfg.conversation_enabled is True


def test_a_turns_working_state_does_not_reach_the_next_one(cfg, session_dir):
    """What the conversation carries, and what it deliberately does not.

    Messages cross a turn boundary, through the transcript. A turn's *graph*
    state does not, and `TodoListMiddleware`'s plan is the visible instance of
    that: an agent resuming a session should not find a half-finished checklist
    it has no memory of writing, from a task the caller may have abandoned.

    It holds because the saver is built per turn -- `build_session_checkpointer`
    at `service.py:1249`, released when the turn ends -- so the next turn's graph
    starts with nothing in its channels. That is structural rather than enforced,
    which is why it is worth a test: a deployment injecting a persistent
    `threads` factory takes it back, and nothing else would say so.

    The turn boundary is reconstructed here rather than driven through
    `Kingfisher.run`, which builds its own agent and takes no model to script.
    What makes the reconstruction faithful is the one line it copies: a fresh
    saver per turn.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from kingfisher.infrastructure.harness.checkpointing import build_session_checkpointer
    from tests.conftest import FakeToolCallingModel

    config: Any = {"configurable": {"thread_id": session_dir.name}, "recursion_limit": 10}
    plan = [{"content": "read the file", "status": "pending"}]

    first_saver = build_session_checkpointer(session_dir)
    build_agent(
        cfg,
        session_dir=session_dir,
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
    ).invoke({"messages": [("user", "plan it")]}, config=config)

    # Asserted, so the second half cannot pass by the plan never being written.
    written = build_agent(
        cfg,
        session_dir=session_dir,
        checkpointer=first_saver,
        model=FakeToolCallingModel(responses=[]),
    ).get_state(config)
    assert written.values.get("todos") == plan, "the first turn never wrote a plan"

    second_saver = build_session_checkpointer(session_dir)
    assert second_saver is not first_saver, "a turn reused the previous turn's saver"

    carried = build_agent(
        cfg,
        session_dir=session_dir,
        checkpointer=second_saver,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    ).get_state(config)

    assert not carried.values.get("todos"), (
        "last turn's plan reached this one -- the agent resumes holding a checklist "
        "it did not write, for a task the caller may have dropped"
    )

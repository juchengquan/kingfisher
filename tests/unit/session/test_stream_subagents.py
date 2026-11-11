"""A subagent's work, on the caller's stream."""

from __future__ import annotations

from dataclasses import replace

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.core.run import stream
from kingfisher.core.session import stream_chunks
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import FakeGraph, FakeToolCallingModel, an_agent, start
from tests.unit.conftest import define_each
from tests.unit.scripted import Scripted
from tests.unit.stand_ins import StubAgent


class StreamingFake(GenericFakeChatModel):
    """A fake that really streams, so a subagent produces token events."""

    def bind_tools(self, tools, **kwargs):
        return self


def _two_level(cfg, session_dir):
    """A real agent that delegates once, driven by scripted models."""
    return create_deep_agent(
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "task",
                            "args": {"description": "check it", "subagent_type": "reviewer"},
                            "id": "p1",
                        }
                    ],
                ),
                AIMessage(content="THE ANSWER"),
            ]
        ),
        backend=backend_at(cfg, session_dir),
        subagents=[
            {
                "name": "reviewer",
                "description": "A subagent.",
                "system_prompt": "You check things.",
                "model": StreamingFake(messages=iter([AIMessage(content="SUBAGENT PROSE")])),
                "tools": [],
            }
        ],
    )


def _events(cfg, session_dir, name="s"):
    start(cfg, name)
    return list(
        stream(
            Request("go", session_id=name),
            cfg=cfg,
            graph=_two_level(cfg, session_dir),
            sessions=LocalSessions(),
        )
    )


# -- it arrives at all ----------------------------------------------------


def test_a_subagents_prose_reaches_the_caller(cfg, session_dir):
    """A subagent's prose kept out of the caller's stream, so only the main agent is heard
    while a subagent works.
    """
    events = _events(cfg, session_dir)

    prose = "".join(e.text for e in events if e.kind == "token")

    assert "SUBAGENT PROSE" in prose


def test_the_subagents_own_steps_arrive_too(cfg, session_dir):
    """Not just its prose -- the model calls it made, which is where the cost of a
    subagent becomes visible rather than merely logged.
    """
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent == "reviewer"]


def test_a_subagents_own_tool_results_reach_the_caller(scripted):
    """The stream is the client's to filter, so a subagent's tool results are on it;
    nothing else held that, and a filter on a subagent's `updates` would drop them
    while its model calls and its `task` answer still arrived."""
    define_each(scripted, "name: reviewer\ndescription: R.\nbuiltin_tools: [ls]\n"
            "system_prompt: |\n  You review.\n")
    an_agent(scripted, subagents="[reviewer]", builtin_tools="[task, ls]")
    start(scripted, "s")
    asked = {"description": "look", "subagent_type": "reviewer"}
    Scripted.script.extend(
        [
            AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}]),
            AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "l1"}]),
            AIMessage(content="REVIEWER-DONE"),
            AIMessage(content="done"),
        ]
    )

    events = list(
        Kingfisher(scripted, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s")
        )
    )

    (listed,) = [e for e in events if e.kind == "tool_result" and e.tool == "ls"]
    assert (listed.agent, listed.delegations, listed.call) == ("reviewer", ("t1",), "l1")


def test_a_deployment_sets_how_much_of_a_result_the_stream_carries(scripted):
    """The cut was a constant, so a client wanting a subagent's result or answer whole
    had nothing to ask with; the control is the default cut at 300."""
    define_each(scripted, "name: reviewer\ndescription: R.\nbuiltin_tools: [read_file]\n"
            "system_prompt: |\n  You review.\n")
    an_agent(scripted, subagents="[reviewer]", builtin_tools="[task, write_file, read_file]")
    start(scripted, "s")
    body, answer = "b" * 600, "a" * 500

    def results(cfg) -> dict[str | None, int]:
        written = {"file_path": "/outputs/r.txt", "content": body}
        asked = {"description": "look", "subagent_type": "reviewer"}
        Scripted.script.extend(
            [
                AIMessage(content="", tool_calls=[{"name": "write_file", "args": written,
                                                   "id": "w"}]),
                AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t"}]),
                AIMessage(content="", tool_calls=[{"name": "read_file",
                                                   "args": {"file_path": "/outputs/r.txt"},
                                                   "id": "r"}]),
                AIMessage(content=answer),
                AIMessage(content="done"),
            ]
        )
        events = Kingfisher(cfg, sessions=LocalSessions()).stream(
            Request("go", agent="only", session_id="s")
        )
        return {e.tool: len(e.text) for e in events if e.kind == "tool_result"}

    assert results(scripted)["read_file"] == 300
    whole = results(replace(scripted, stream_result_chars=10_000))
    assert whole["read_file"] > len(body), "a subagent's result was still cut"
    assert whole["task"] >= len(answer), "a subagent's answer was still cut"


# -- and is distinguishable -----------------------------------------------


def test_every_subagent_event_says_which_subagent(cfg, session_dir):
    """The claim `SubagentNames` rests on, driven rather than asserted in prose."""
    events = _events(cfg, session_dir)

    from_subagents = [e for e in events if e.agent is not None]
    assert from_subagents, "no event was attributed to a subagent at all"
    assert {e.agent for e in from_subagents} == {"reviewer"}


def test_the_callers_own_events_are_not_attributed_to_anyone(cfg, session_dir):
    """`None` is the main agent."""
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent is None]


def test_a_subagents_line_is_tagged_with_its_name(cfg, session_dir):
    """`[model:reviewer]` rather than `[model]`, so a transcript reads without the
    reader having to track which lines came from where.
    """
    events = _events(cfg, session_dir)

    (first,) = [e for e in events if e.kind == "model_call" and e.agent == "reviewer"][:1]

    assert str(first).startswith("[model:reviewer]")


def test_a_run_without_subagents_renders_exactly_as_before(cfg):
    """The tag is appended, never a new column, so nothing that parsed these lines has
    to learn about subagents it never sees.
    """
    start(cfg, "plain")
    events = list(
        stream(
            Request("go", session_id="plain"),
            cfg=cfg,
            graph=StubAgent("42", updates=[{"agent": {"messages": [AIMessage(content="hi")]}}]),
            sessions=LocalSessions(),
        )
    )

    (call,) = [e for e in events if e.kind == "model_call"]
    assert str(call).startswith("[model] ")


# -- the answer belongs to the caller's agent -----------------------------


def test_a_subagents_values_chunk_is_not_the_runs_answer():
    """The bug `subgraphs=True` would otherwise have introduced."""
    theirs = {"messages": [AIMessage(content="SUBAGENT PROSE")]}

    assert stream_chunks.answer_in(("tools:abc",), "values", theirs) is None
    assert stream_chunks.answer_in((), "values", theirs) == "SUBAGENT PROSE"


class _SubagentSpeaksFirst(FakeGraph):
    """A subagent finishes, and the turn is cut short before anything else."""

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield (("tools:abc",), "values", {"messages": [AIMessage(content="belongs-to-subagent")]})
        yield ((), "values", {"messages": [AIMessage(content="belongs-to-caller")]})


def test_a_turn_cut_short_after_a_subagent_reports_no_subagent_answer(cfg):
    """The same thing end to end, through the path that actually stops early."""
    from dataclasses import replace

    start(cfg, "cut")

    events = list(
        stream(
            Request("go", session_id="cut"),
            cfg=replace(cfg, turn_timeout_s=0),
            graph=_SubagentSpeaksFirst(),
            sessions=LocalSessions(),
        )
    )

    (finished,) = [e for e in events if e.kind == "finished"]
    assert finished.result is not None
    assert finished.result.stop_reason == "max_duration"
    assert "subagent" not in finished.result.answer.lower()


def test_the_map_never_names_the_main_agent():
    """`()` is the caller's agent and has no name to learn, whatever metadata a chunk
    happens to carry.
    """
    for namespace in [(), ("tools:abc",)]:
        subagent_names = stream_chunks.SubagentNames()

        named = subagent_names.name(namespace, {"lc_agent_name": "reviewer"})

        assert named == (None if namespace == () else "reviewer")

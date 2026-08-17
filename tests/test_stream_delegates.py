"""A delegate's work, on the caller's stream.

It used to be invisible. A stream reports the graph it was started on, and a
delegate is a separate graph run inside a `task` tool call -- so the caller saw
the call go out and the answer come back, and nothing in between. The run *log*
saw all of it, because that rides on callbacks, which follow a config down into
anything nested. Two pipes, one run, different reach.

`subgraphs=True` is the other pipe reaching as far. What it costs is that the
caller's prose and a delegate's now arrive on one channel as the same type --
both `AIMessageChunk` -- so `RunEvent.agent` is what tells them apart, and one
`values` chunk from a delegate can no longer be mistaken for the run's answer.
"""

from __future__ import annotations

import pytest
from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from kingfisher.application.run import Request, stream
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.backend import build_backend
from tests.conftest import FakeToolCallingModel, StubCheckpointer, start
from tests.test_run import StubAgent


class StreamingFake(GenericFakeChatModel):
    """A fake that really streams, so a delegate produces token events."""

    def bind_tools(self, tools, **kwargs):
        return self


def _two_level(cfg, session_dir):
    """A real agent that delegates once, driven by scripted models.

    Real rather than stubbed: what is being tested is how LangGraph reports a
    nested run, which a stub would simply assert back at us.
    """
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
        backend=build_backend(cfg, session_dir),
        subagents=[
            {
                "name": "reviewer",
                "description": "A delegate.",
                "system_prompt": "You check things.",
                "model": StreamingFake(messages=iter([AIMessage(content="DELEGATE PROSE")])),
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
            agent=_two_level(cfg, session_dir),
            checkpointer=StubCheckpointer(),
        )
    )


# -- it arrives at all ----------------------------------------------------


def test_a_delegates_prose_reaches_the_caller(cfg, session_dir):
    """The whole point. Before this, the only trace of a delegate was the
    `tool_result` its `task` call returned."""
    events = _events(cfg, session_dir)

    prose = "".join(e.text for e in events if e.kind == "token")

    assert "DELEGATE PROSE" in prose


def test_the_delegates_own_steps_arrive_too(cfg, session_dir):
    """Not just its prose -- the model calls it made, which is where the cost
    of a delegate becomes visible rather than merely logged."""
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent == "reviewer"]


# -- and is distinguishable -----------------------------------------------


def test_every_delegate_event_says_which_delegate(cfg, session_dir):
    """The claim `Delegates` rests on, driven rather than asserted in prose.

    Only `messages` chunks carry `lc_agent_name`; `updates` carry no metadata
    at all. That is only survivable because a delegate's first model call is
    streamed before the node update reporting it -- so the map is filled before
    anything needs it. If that order ever changes, this goes red rather than
    quietly emitting unnamed events.
    """
    events = _events(cfg, session_dir)

    delegated = [e for e in events if e.agent is not None]
    assert delegated, "no event was attributed to a delegate at all"
    assert {e.agent for e in delegated} == {"reviewer"}


def test_the_callers_own_events_are_not_attributed_to_anyone(cfg, session_dir):
    """`None` is the main agent. A run without delegates is unchanged, which is
    what keeps this from being a breaking change for every existing consumer.
    """
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent is None]


def test_a_delegates_line_is_tagged_with_its_name(cfg, session_dir):
    """`[model:reviewer]` rather than `[model]`, so a transcript reads without
    the reader having to track which lines came from where."""
    events = _events(cfg, session_dir)

    (first,) = [e for e in events if e.kind == "model_call" and e.agent == "reviewer"][:1]

    assert str(first).startswith("[model:reviewer]")


def test_a_run_without_delegates_renders_exactly_as_before(cfg):
    """The tag is appended, never a new column, so nothing that parsed these
    lines has to learn about delegates it never sees."""
    start(cfg, "plain")
    events = list(
        stream(
            Request("go", session_id="plain"),
            cfg=cfg,
            agent=StubAgent("42", updates=[{"agent": {"messages": [AIMessage(content="hi")]}}]),
            checkpointer=StubCheckpointer(),
        )
    )

    (call,) = [e for e in events if e.kind == "model_call"]
    assert str(call).startswith("[model] ")


# -- the answer belongs to the caller's agent -----------------------------


def test_a_delegates_values_chunk_is_not_the_runs_answer():
    """The bug `subgraphs=True` would otherwise have introduced.

    `answer_in` keeps the last `values` chunk it is given, and streaming into
    delegates makes theirs arrive here too. A turn that ends normally is
    unharmed, because the caller's agent speaks last -- but `turn_timeout_s`
    stops between chunks, and stopping just after a delegate finished would
    have carried the delegate's answer out as the run's.
    """
    theirs = {"messages": [AIMessage(content="DELEGATE PROSE")]}

    assert runtime.answer_in(("tools:abc",), "values", theirs) is None
    assert runtime.answer_in((), "values", theirs) == "DELEGATE PROSE"


class _DelegateSpeaksFirst:
    """A delegate finishes, and the turn is cut short before anything else.

    Purpose-built rather than `StubAgent`, which always emits its `values` at
    the root: the scenario needs a delegate's `values` chunk to be the last one
    the loop sees, which is exactly what stopping between chunks can produce.
    """

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield (("tools:abc",), "values", {"messages": [AIMessage(content="belongs-to-delegate")]})
        yield ((), "values", {"messages": [AIMessage(content="belongs-to-caller")]})


def test_a_turn_cut_short_after_a_delegate_reports_no_delegate_answer(cfg):
    """The same thing end to end, through the path that actually stops early.

    The trigger is a wall clock rather than a graph, so the turn is given no
    time at all: the first chunk is also the last, and it is a delegate's.
    Without the namespace guard the run carries `belongs-to-delegate` out as
    its own answer, which is the failure -- silent, and only on the timeout
    path, so nothing about a normal run would have shown it.
    """
    from dataclasses import replace

    start(cfg, "cut")

    events = list(
        stream(
            Request("go", session_id="cut"),
            cfg=replace(cfg, turn_timeout_s=0),
            agent=_DelegateSpeaksFirst(),
            checkpointer=StubCheckpointer(),
        )
    )

    (finished,) = [e for e in events if e.kind == "finished"]
    assert finished.result is not None
    assert finished.result.cut_short
    assert "delegate" not in finished.result.answer.lower()


@pytest.mark.parametrize("namespace", [(), ("tools:abc",)])
def test_the_map_never_names_the_main_agent(namespace):
    """`()` is the caller's agent and has no name to learn, whatever metadata
    a chunk happens to carry."""
    delegates = runtime.Delegates()

    named = delegates.name(namespace, {"lc_agent_name": "reviewer"})

    assert named == (None if namespace == () else "reviewer")

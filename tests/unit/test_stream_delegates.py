"""A delegate's work, on the caller's stream."""

from __future__ import annotations

from deepagents import create_deep_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from kingfisher.application.run import Request, stream
from kingfisher.infrastructure.harness import runtime
from kingfisher.infrastructure.harness.backend import build_backend
from tests.conftest import FakeToolCallingModel, StubCheckpointer, start
from tests.unit.test_run import StubAgent


class StreamingFake(GenericFakeChatModel):
    """A fake that really streams, so a delegate produces token events."""

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
            graph=_two_level(cfg, session_dir),
            checkpointer=StubCheckpointer(),
        )
    )


# -- it arrives at all ----------------------------------------------------


def test_a_delegates_prose_reaches_the_caller(cfg, session_dir):
    """The whole point."""
    events = _events(cfg, session_dir)

    prose = "".join(e.text for e in events if e.kind == "token")

    assert "DELEGATE PROSE" in prose


def test_the_delegates_own_steps_arrive_too(cfg, session_dir):
    """Not just its prose -- the model calls it made, which is where the cost of a
    delegate becomes visible rather than merely logged.
    """
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent == "reviewer"]


# -- and is distinguishable -----------------------------------------------


def test_every_delegate_event_says_which_delegate(cfg, session_dir):
    """The claim `Delegates` rests on, driven rather than asserted in prose."""
    events = _events(cfg, session_dir)

    delegated = [e for e in events if e.agent is not None]
    assert delegated, "no event was attributed to a delegate at all"
    assert {e.agent for e in delegated} == {"reviewer"}


def test_the_callers_own_events_are_not_attributed_to_anyone(cfg, session_dir):
    """`None` is the main agent."""
    events = _events(cfg, session_dir)

    assert [e for e in events if e.kind == "model_call" and e.agent is None]


def test_a_delegates_line_is_tagged_with_its_name(cfg, session_dir):
    """`[model:reviewer]` rather than `[model]`, so a transcript reads without the
    reader having to track which lines came from where.
    """
    events = _events(cfg, session_dir)

    (first,) = [e for e in events if e.kind == "model_call" and e.agent == "reviewer"][:1]

    assert str(first).startswith("[model:reviewer]")


def test_a_run_without_delegates_renders_exactly_as_before(cfg):
    """The tag is appended, never a new column, so nothing that parsed these lines has
    to learn about delegates it never sees.
    """
    start(cfg, "plain")
    events = list(
        stream(
            Request("go", session_id="plain"),
            cfg=cfg,
            graph=StubAgent("42", updates=[{"agent": {"messages": [AIMessage(content="hi")]}}]),
            checkpointer=StubCheckpointer(),
        )
    )

    (call,) = [e for e in events if e.kind == "model_call"]
    assert str(call).startswith("[model] ")


# -- the answer belongs to the caller's agent -----------------------------


def test_a_delegates_values_chunk_is_not_the_runs_answer():
    """The bug `subgraphs=True` would otherwise have introduced."""
    theirs = {"messages": [AIMessage(content="DELEGATE PROSE")]}

    assert runtime.answer_in(("tools:abc",), "values", theirs) is None
    assert runtime.answer_in((), "values", theirs) == "DELEGATE PROSE"


class _DelegateSpeaksFirst:
    """A delegate finishes, and the turn is cut short before anything else."""

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield (("tools:abc",), "values", {"messages": [AIMessage(content="belongs-to-delegate")]})
        yield ((), "values", {"messages": [AIMessage(content="belongs-to-caller")]})


def test_a_turn_cut_short_after_a_delegate_reports_no_delegate_answer(cfg):
    """The same thing end to end, through the path that actually stops early."""
    from dataclasses import replace

    start(cfg, "cut")

    events = list(
        stream(
            Request("go", session_id="cut"),
            cfg=replace(cfg, turn_timeout_s=0),
            graph=_DelegateSpeaksFirst(),
            checkpointer=StubCheckpointer(),
        )
    )

    (finished,) = [e for e in events if e.kind == "finished"]
    assert finished.result is not None
    assert finished.result.stop_reason == "max_duration"
    assert "delegate" not in finished.result.answer.lower()


def test_the_map_never_names_the_main_agent():
    """`()` is the caller's agent and has no name to learn, whatever metadata a chunk
    happens to carry.
    """
    for namespace in [(), ("tools:abc",)]:
        delegates = runtime.Delegates()

        named = delegates.name(namespace, {"lc_agent_name": "reviewer"})

        assert named == (None if namespace == () else "reviewer")

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.app.run import Request, stream
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


def _agent_with_a_tool_call() -> StubAgent:
    return StubAgent(
        "42",
        updates=[
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {"name": "execute", "args": {"command": "echo hi"}, "id": "c1"}
                            ],
                            usage_metadata={
                                "input_tokens": 100,
                                "output_tokens": 5,
                                "total_tokens": 105,
                                "input_token_details": {"cache_read": 80},
                            },
                        )
                    ]
                }
            },
            {"tools": {"messages": [ToolMessage(content="hi", name="execute", tool_call_id="c1")]}},
        ],
    )


def _events(cfg, agent):
    return list(
        stream(Request("t", session_id="s"), cfg=cfg, agent=agent, checkpointer=StubCheckpointer())
    )


def test_stream_yields_progress_then_a_terminal_result(cfg):
    events = _events(cfg, _agent_with_a_tool_call())
    kinds = [e.kind for e in events]

    assert kinds[0] == "run_start"
    assert "model_call" in kinds
    assert "tool_result" in kinds
    assert kinds[-1] == "finished"
    # Only the terminal event carries the result.
    assert sum(e.result is not None for e in events) == 1


def test_stream_surfaces_tool_names_and_cache_usage(cfg):
    events = _events(cfg, _agent_with_a_tool_call())

    (call,) = [e for e in events if e.kind == "model_call"]
    assert call.tools == ("execute",)
    assert call.usage["cache_read"] == 80

    (tool,) = [e for e in events if e.kind == "tool_result"]
    assert tool.tool == "execute"
    assert tool.text == "hi"


def test_finished_event_carries_the_normalized_answer(cfg):
    agent = StubAgent("<think>reasoning</think>\n\n42")
    (finished,) = [e for e in _events(cfg, agent) if e.kind == "finished"]

    assert finished.result is not None
    assert finished.result.answer == "42"
    assert finished.result.session_id == "s"


def test_events_render_as_readable_lines(cfg):
    """`for e in stream(...): print(e)` should be useful with no formatting code."""
    rendered = [str(e) for e in _events(cfg, _agent_with_a_tool_call())]
    assert any("execute" in line for line in rendered)
    assert any("cached=80" in line for line in rendered)


def test_run_is_a_drain_of_stream(cfg):
    """One orchestration path: run() must not re-implement the sequence."""
    from kingfisher.app.run import run

    agent = StubAgent("<think>x</think>7")
    result = run(Request("t", session_id="drained"), cfg=cfg, agent=agent, checkpointer=StubCheckpointer())

    assert result.answer == "7"
    assert result.run_dir.is_dir()
    assert agent.config["configurable"]["thread_id"] == "drained"


def test_multiline_tool_output_renders_on_one_line(cfg):
    """Scannable progress: `text` keeps full fidelity, `__str__` collapses it."""
    agent = StubAgent(
        "ok",
        updates=[
            {
                "tools": {
                    "messages": [
                        ToolMessage(content="line one\nline two\n\nline three", name="execute", tool_call_id="c")
                    ]
                }
            }
        ],
    )
    (tool,) = [e for e in _events(cfg, agent) if e.kind == "tool_result"]

    assert "\n" in tool.text  # data preserved
    assert "\n" not in str(tool)  # display collapsed
    assert "line one line two line three" in str(tool)

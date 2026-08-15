from __future__ import annotations

from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage

from kingfisher.app.run import Request, stream
from tests.conftest import StubCheckpointer, start
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
    start(cfg, "s")
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


def _agent_with_content_blocks() -> StubAgent:
    """What the Responses API returns: content as a list of blocks.

    Chat Completions returns a plain string, so this shape did not arise before
    the openai style moved to `/v1/responses`.
    """
    return StubAgent(
        "42",
        updates=[
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            content=[
                                {"type": "reasoning", "summary": []},
                                {"type": "text", "text": "The answer is 42."},
                            ]
                        )
                    ]
                }
            },
            {
                "tools": {
                    "messages": [
                        ToolMessage(
                            content=[{"type": "text", "text": "hi"}],
                            name="execute",
                            tool_call_id="c1",
                        )
                    ]
                }
            },
        ],
    )


def test_content_blocks_are_read_as_text_not_repr(cfg):
    """`str(content)` on a block list renders its Python repr into the answer.

    The assistant half of this now travels as tokens rather than as a
    completed message -- see `test_token_content_blocks_are_read_as_text`.
    """
    tool_result = next(
        e for e in _events(cfg, _agent_with_content_blocks()) if e.kind == "tool_result"
    )

    assert tool_result.text == "hi"
    assert "'type':" not in tool_result.text


def test_a_message_that_is_only_a_state_shuffle_emits_nothing(cfg):
    """No tools, no text, no usage: nobody took a turn, so there is nothing to say.

    Reasoning-only content blocks are the shape that raised this — `.text`
    yields "" for them.
    """
    message = AIMessage(content=[{"type": "reasoning", "summary": []}])
    agent = StubAgent("42", updates=[{"agent": {"messages": [message]}}])

    assert not [e for e in _events(cfg, agent) if e.kind == "model_call"]


def test_a_turn_that_only_spoke_is_still_a_model_call(cfg):
    """`message` is collapsed: a completed turn is a completed turn.

    Its prose is not repeated here; that is the token stream's job.
    """
    agent = StubAgent(
        "ok", updates=[{"agent": {"messages": [AIMessage(content="thinking out loud")]}}]
    )
    events = _events(cfg, agent)

    assert "message" not in [e.kind for e in events]
    (call,) = [e for e in events if e.kind == "model_call"]
    assert call.tools == ()
    assert call.args == ()
    assert "thinking out loud" not in str(call)


def test_a_tool_call_carries_its_arguments(cfg):
    """The run log has recorded these all along; the terminal never showed them."""
    (call,) = [e for e in _events(cfg, _agent_with_a_tool_call()) if e.kind == "model_call"]

    assert call.tools == ("execute",)
    assert call.args == ({"command": "echo hi"},)
    assert len(call.args) == len(call.tools)
    assert "execute(command=echo hi)" in str(call)


def test_a_large_tool_argument_cannot_flood_the_line(cfg):
    """`write_file` takes an entire file as one of its arguments."""
    agent = StubAgent(
        "ok",
        updates=[
            {
                "agent": {
                    "messages": [
                        AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "write_file",
                                    "args": {"file_path": "/data/x", "content": "x" * 5000},
                                    "id": "c1",
                                }
                            ],
                        )
                    ]
                }
            }
        ],
    )
    (call,) = [e for e in _events(cfg, agent) if e.kind == "model_call"]

    assert "/data/x" in str(call)  # the argument you needed is there
    assert len(str(call)) < 300  # the one you did not is bounded
    assert "…" in str(call)


def test_tools_and_arguments_cannot_be_constructed_out_of_step():
    """Two parallel tuples are only safe if nothing can make them disagree."""
    import pytest as _pytest

    from kingfisher.domain.result import RunEvent

    with _pytest.raises(ValueError, match="parallel"):
        RunEvent(kind="model_call", tools=("a", "b"), args=({},))


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


def test_prose_arrives_as_token_fragments(cfg):
    """Chunks split mid-word; reassembly is the renderer's job, not the domain's."""
    agent = StubAgent(
        "42",
        tokens=[
            (AIMessageChunk(content="7 to"), {"langgraph_node": "model"}),
            (AIMessageChunk(content=" seven.txt"), {"langgraph_node": "model"}),
        ],
    )
    tokens = [e for e in _events(cfg, agent) if e.kind == "token"]

    assert [t.text for t in tokens] == ["7 to", " seven.txt"]
    assert all(t.channel == "answer" for t in tokens)


def test_a_token_renders_as_its_bare_text(cfg):
    """A fragment is not a line: a tag would assert a boundary that is not there."""
    agent = StubAgent("x", tokens=[(AIMessageChunk(content="a\n\nb"), {})])
    (token,) = [e for e in _events(cfg, agent) if e.kind == "token"]

    assert str(token) == "a\n\nb"  # neither collapsed nor prefixed


def test_token_content_blocks_are_read_as_text(cfg):
    """The Responses API shape reaches this stream too."""
    chunk = AIMessageChunk(content=[{"type": "text", "text": "The answer is 42."}])
    agent = StubAgent("42", tokens=[(chunk, {})])
    (token,) = [e for e in _events(cfg, agent) if e.kind == "token"]

    assert token.text == "The answer is 42."
    assert "'type':" not in token.text


def test_tool_results_on_the_token_stream_are_not_prose(cfg):
    """They arrive here untruncated; `tool_result` already carries them, bounded."""
    agent = StubAgent(
        "ok",
        tokens=[
            (
                ToolMessage(content="x" * 5000, name="read_file", tool_call_id="c"),
                {"langgraph_node": "tools"},
            )
        ],
    )

    assert not [e for e in _events(cfg, agent) if e.kind == "token"]


def test_a_usage_only_chunk_produces_no_token(cfg):
    """The last chunk of a turn carries the usage and no text."""
    agent = StubAgent("ok", tokens=[(AIMessageChunk(content=""), {"langgraph_node": "model"})])

    assert not [e for e in _events(cfg, agent) if e.kind == "token"]


def test_the_adapter_owns_the_stream_modes(cfg):
    """`values` and `messages` are LangGraph's words, not orchestration's."""
    from kingfisher.adapters import runtime

    values = {"messages": [AIMessage(content="42")]}

    assert runtime.answer_in("values", values) == "42"
    assert runtime.answer_in("updates", values) is None
    assert list(runtime.events_in("values", values)) == []


def test_run_is_a_drain_of_stream(cfg):
    """One orchestration path: run() must not re-implement the sequence."""
    start(cfg, "drained")
    from kingfisher.app.run import run

    agent = StubAgent("<think>x</think>7")
    result = run(
        Request("t", session_id="drained"),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

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
                        ToolMessage(
                            content="line one\nline two\n\nline three",
                            name="execute",
                            tool_call_id="c",
                        )
                    ]
                }
            }
        ],
    )
    (tool,) = [e for e in _events(cfg, agent) if e.kind == "tool_result"]

    assert "\n" in tool.text  # data preserved
    assert "\n" not in str(tool)  # display collapsed
    assert "line one line two line three" in str(tool)

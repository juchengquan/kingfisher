"""Where the conversation's cache breakpoint goes when a call ends in something sent for it
alone, and that nothing else moves."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from langchain.agents.middleware import ModelRequest
from langchain_anthropic.middleware import AnthropicPromptCachingMiddleware
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares import standing
from kingfisher.core.agent.middlewares.caching import kept_last
from kingfisher.core.layout import MEMORY
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel
from tests.unit.test_session_instructions import Sent, _breakpoints

#: Where a turn's second call is past 80% of its steps, so carries the step budget's note.
LIMIT = 6

#: How the step budget's note on a last call begins.
NOTE = "This is your last model call"


class SentElsewhere(Sent):
    """The same model on a transport other than the direct API, where langchain itself
    puts the top-level breakpoint on the last block."""

    @property
    def _llm_type(self) -> str:
        return "anthropic-chat-elsewhere"


class Heard(FakeToolCallingModel):
    """Keeps the messages of every call, on a model Anthropic's caching skips."""

    heard: list[list[BaseMessage]] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.heard.append(list(messages))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def _ls() -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": "c1"}])


def _cut() -> AIMessage:
    """An answer cut at its output limit, which `ModelReplyMiddleware` asks for again."""
    return AIMessage(
        content="The quarter's figures are",
        response_metadata={"stop_reason": "max_tokens"},
        usage_metadata={"input_tokens": 10, "output_tokens": 32_768, "total_tokens": 32_778},
    )


def _turn(cfg, session_dir, model: Any, way: str, limit: int = 150) -> None:
    """One turn with memory on, stopped where the step limit stops it."""
    wired = replace(cfg, memory_enabled=True, recursion_limit=limit)
    (session_dir / MEMORY / "AGENTS.md").write_text("- The caller is Ada.\n", encoding="utf-8")
    built = build_agent(wired, backend=backend_at(wired, session_dir), model=model)
    asked = {"messages": [{"role": "user", "content": "hi"}]}
    try:
        if way == "async":
            asyncio.run(
                built.graph.ainvoke(asked, context=built.context, config={"recursion_limit": limit})
            )
        else:
            built.graph.invoke(asked, context=built.context, config={"recursion_limit": limit})
    except Exception as exc:  # noqa: BLE001 -- the limit ends the turn after the call read
        assert type(exc).__name__ == "GraphRecursionError", exc


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message["content"]
    return [{"type": "text", "text": content}] if isinstance(content, str) else content


@pytest.mark.parametrize("transport", [Sent, SentElsewhere], ids=["direct", "elsewhere"])
def test_the_step_note_leaves_the_breakpoint_on_what_the_conversation_keeps(
    cfg, session_dir, way, transport
):
    """Marked where the note ended the call, the conversation's breakpoint wrote an entry
    no later call sends, so every call past 80% of a turn's steps paid to cache what
    nobody read. Moved rather than added: with memory on, a fifth is refused."""
    model = transport(model="claude-sonnet-4-5", api_key="x", script=[_ls(), AIMessage("done")])
    _turn(cfg, session_dir, model, way, limit=LIMIT)

    first, noted = model.sent
    result, note = _blocks(noted["messages"][-1])
    assert note["text"].startswith(NOTE), "the second call no longer carries the note"
    assert "cache_control" not in note
    assert result["type"] == "tool_result"
    assert "cache_control" in result
    assert "cache_control" not in noted
    assert _breakpoints(noted) == _breakpoints(first) == 4


def test_a_cut_answer_asked_again_leaves_the_breakpoint_on_the_task(cfg, session_dir, way):
    """The cut answer and the note asking again are sent once and never kept, so the
    entry ending in them is never read; the task before them is what the next call sends."""
    model = Sent(model="claude-sonnet-4-5", api_key="x", script=[_cut(), AIMessage("done")])
    _turn(cfg, session_dir, model, way)

    again = model.sent[1]["messages"]
    task, cut, note = again[-3:]
    assert [m["role"] for m in (task, cut, note)] == ["user", "assistant", "user"]
    assert "cache_control" in _blocks(task)[-1]
    assert not [b for m in (cut, note) for b in _blocks(m) if "cache_control" in b]
    assert "cache_control" not in model.sent[1]
    assert _breakpoints(model.sent[1]) == 4


def _as_deepagents_caches(monkeypatch) -> None:
    """The slot as deepagents fills it, which kingfisher's must match where nothing is
    sent for one call alone."""
    monkeypatch.setattr(
        standing,
        "PromptCachingMiddleware",
        lambda: AnthropicPromptCachingMiddleware(unsupported_model_behavior="ignore"),
    )


def test_a_call_ending_in_what_the_conversation_keeps_is_sent_as_before(
    cfg, session_dir, way, monkeypatch
):
    """The control: a move made on every call would cost the cache it is there to keep."""
    sent: dict[str, list[dict[str, Any]]] = {}
    for slot in ("kingfisher", "deepagents"):
        if slot == "deepagents":
            _as_deepagents_caches(monkeypatch)
        model = Sent(model="claude-sonnet-4-5", api_key="x", script=[_ls(), AIMessage("done")])
        _turn(cfg, session_dir, model, way)
        sent[slot] = model.sent

    assert len(sent["kingfisher"]) == 2
    assert sent["kingfisher"] == sent["deepagents"]


def test_a_model_that_is_not_anthropics_is_sent_the_note_as_before(
    cfg, session_dir, way, monkeypatch
):
    """Anthropic's caching does nothing for another model, and neither may the move."""
    heard: dict[str, list[list[BaseMessage]]] = {}
    for slot in ("kingfisher", "deepagents"):
        if slot == "deepagents":
            _as_deepagents_caches(monkeypatch)
        model = Heard(responses=[_ls(), AIMessage("done")])
        _turn(cfg, session_dir, model, way, limit=LIMIT)
        heard[slot] = model.heard

    assert heard["kingfisher"][1][-1].text.startswith(NOTE)
    assert [[m.content for m in call] for call in heard["kingfisher"]] == [
        [m.content for m in call] for call in heard["deepagents"]
    ]


#: The top-level breakpoint as Anthropic's caching passes it.
MARK = {"type": "ephemeral", "ttl": "5m"}


@pytest.mark.parametrize(
    ("result", "kept"),
    [("", True), ([], True), ("['/inputs/']", False)],
    ids=["empty-text", "no-blocks", "nothing-kept"],
)
def test_a_call_with_no_block_to_move_the_breakpoint_to_is_marked_as_before(result, kept):
    """A breakpoint on an empty text block is refused, failing the call the move was there
    to make cheaper; and with nothing the conversation keeps, there is nowhere to put it."""
    task = HumanMessage("hi", id="h1")
    answered = ToolMessage(result, tool_call_id="c1", id="t1")
    request = ModelRequest(
        model=FakeToolCallingModel(responses=[]),
        messages=[task, answered, HumanMessage(NOTE)],
        system_message=None,
        tools=[],
        state={"messages": [task, answered] if kept else []},
        model_settings={"cache_control": MARK},
    )

    assert kept_last(request) is request

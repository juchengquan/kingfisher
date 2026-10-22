"""A deployment's text for one session: where in the prompt it goes, and which graphs get it."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Any

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field

from kingfisher import Kingfisher, Request
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.caching import CACHING, PromptCachingMiddleware
from kingfisher.core.config import ConfigError
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.layout import MEMORY
from kingfisher.plugins import LocalSessions, Plugins, backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    agent_named,
    an_agent,
    subagents_dir,
)
from tests.unit.scripted import Scripted

TEXT = "This session is for the Q3 audit."


class Sent(ChatAnthropic):
    """Keeps every request as it would go out, cache breakpoints and all, and answers from
    a script. A `ChatAnthropic` because only one is sent breakpoints."""

    sent: list[dict[str, Any]] = Field(default_factory=list)
    script: list[AIMessage] = Field(default_factory=list)

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.sent.append(self._get_request_payload(messages, stop=stop, **kwargs))
        reply = self.script.pop(0) if self.script else AIMessage(content="ok")
        return ChatResult(generations=[ChatGeneration(message=reply)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        return self._generate(messages, stop=stop, **kwargs)


def _model(*script: AIMessage) -> Sent:
    return Sent(model="claude-sonnet-4-5", api_key="not-a-real-key", script=list(script))


def _task(subagent: str, at: str) -> AIMessage:
    call = {"description": "look into it", "subagent_type": subagent}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": call, "id": at}])


def _system(payload: dict[str, Any]) -> list[dict[str, Any]]:
    system = payload.get("system") or []
    return [{"type": "text", "text": system}] if isinstance(system, str) else list(system)


def _breakpoints(payload: dict[str, Any]) -> int:
    """Every cache breakpoint one request carries, of the four Anthropic allows."""
    blocks = [*_system(payload), *payload.get("tools", ())]
    for message in payload.get("messages", ()):
        if isinstance(message.get("content"), list):
            blocks.extend(b for b in message["content"] if isinstance(b, dict))
    return sum("cache_control" in b for b in blocks) + ("cache_control" in payload)


def _first_request(cfg, session_dir, way: str = "sync", **wiring: Any) -> dict[str, Any]:
    """What one turn with memory on sends the model first."""
    wired = replace(cfg, memory_enabled=True)
    (session_dir / MEMORY / "AGENTS.md").write_text("- The caller is Ada.\n", encoding="utf-8")
    model = _model()
    built = build_agent(wired, backend=backend_at(wired, session_dir), model=model, **wiring)
    asked = {"messages": [{"role": "user", "content": "hi"}]}
    if way == "async":
        asyncio.run(built.graph.ainvoke(asked, context=built.context))
    else:
        built.graph.invoke(asked, context=built.context)
    return model.sent[0]


def test_the_text_follows_the_static_prompts_breakpoint_and_takes_none(cfg, session_dir, way):
    """Sent ahead of the static prompt's breakpoint, text that differs between sessions
    costs every session that cache; given one of its own, a fifth breakpoint, which
    Anthropic refuses."""
    payload = _first_request(cfg, session_dir, way, session_instructions=TEXT)
    blocks = _system(payload)
    (at,) = [i for i, block in enumerate(blocks) if TEXT in block["text"]]

    assert "cache_control" not in blocks[at]
    assert "cache_control" in blocks[at - 1], "the static prompt lost its breakpoint"
    assert "The caller is Ada." in blocks[at + 1]["text"], "not ahead of the memory block"
    assert "cache_control" in blocks[at + 1]
    assert _breakpoints(payload) == 4


def _cached_prefix(payload: dict[str, Any]) -> Any:
    """The tools and the system blocks up to the static prompt's breakpoint, as sent."""
    blocks = _system(payload)
    marked = next(i for i, block in enumerate(blocks) if "cache_control" in block)
    return payload.get("tools"), blocks[: marked + 1], payload.get("cache_control")


def test_sessions_with_different_text_share_the_cached_prefix(cfg, session_dir):
    """The point of sending it after the breakpoint, and the check that the slot marks as
    deepagents' own did: a session told nothing is the third of the three."""
    told = [
        _cached_prefix(_first_request(cfg, session_dir, session_instructions=text))
        for text in (TEXT, "This session is for payroll.", None)
    ]

    assert told[0] == told[1] == told[2]


def test_no_text_leaves_the_request_as_it_was(cfg, session_dir):
    """The control: a deployment that wired the plugin and has nothing to say this turn
    is sent what one that wired none is, so its cache is not broken by the wiring."""
    assert _first_request(cfg, session_dir, session_instructions="") == _first_request(
        cfg, session_dir
    )


class _Told(FakeToolCallingModel):
    """Keeps the system message of every call, on a model Anthropic's caching skips."""

    heard: list[str] = []

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.heard.extend(m.text for m in messages if isinstance(m, SystemMessage))
        return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)


def test_a_model_that_is_not_anthropics_is_sent_it_too(cfg, session_dir):
    """The slot is Anthropic's caching, which does nothing for another model; the text
    must not go with it."""
    model = _Told(responses=[AIMessage(content="ok")])
    built = build_agent(
        cfg, backend=backend_at(cfg, session_dir), model=model, session_instructions=TEXT
    )

    built.graph.invoke({"messages": [{"role": "user", "content": "hi"}]}, context=built.context)

    assert TEXT in model.heard[0]


REVIEWER = """name: reviewer
description: Reviews.
builtin_tools: [task]
subagents: [checker]
system_prompt: |
  You review.
"""

CHECKER = """name: checker
description: Checks.
system_prompt: |
  You check.
"""

COMPILED = '''
from langchain.agents import create_agent

SUBAGENTS = [
    {
        "name": "researcher",
        "description": "Researches, on a graph of its own.",
        "build": lambda model, tools: create_agent(model, tools),
    }
]
'''


def _every_kind(cfg) -> Kingfisher:
    """An agent consulting a declared subagent, its helper and a compiled subagent."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "reviewer.yaml").write_text(REVIEWER, encoding="utf-8")
    (subagents_dir(cfg) / "checker.yaml").write_text(CHECKER, encoding="utf-8")
    (subagents_dir(cfg) / "researcher.py").write_text(COMPILED, encoding="utf-8")
    an_agent(cfg, "lead", subagents="[reviewer, checker, researcher]", **EVERY_TOOL)
    return Kingfisher(cfg, sessions=LocalSessions())


@pytest.mark.parametrize("text", [TEXT, None], ids=["told", "untold"])
def test_every_graph_kingfisher_builds_is_sent_it_and_a_compiled_one_is_not(
    cfg, session_dir, way, text
):
    """A rule for the session that reached the agent and nothing it delegated to is one
    delegated work escapes. A helper, which deepagents gives no prompt caching, is sent
    the text and still no breakpoint, nor one where there is no text to send. A compiled
    subagent writes its own prompt."""
    kf = _every_kind(cfg)
    model = _model(
        _task("reviewer", "t1"),
        _task("checker", "t2"),
        AIMessage(content="checked"),
        AIMessage(content="reviewed"),
        _task("general-purpose", "t3"),
        AIMessage(content="looked"),
        _task("researcher", "t4"),
        AIMessage(content="researched"),
        AIMessage(content="done"),
    )
    built = build_agent(
        cfg,
        agent=agent_named(kf, "lead"),
        backend=backend_at(cfg, session_dir),
        model=model,
        session_instructions=text,
    )

    asked = {"messages": [{"role": "user", "content": "go"}]}
    if way == "async":
        asyncio.run(built.graph.ainvoke(asked, context=built.context))
    else:
        built.graph.invoke(asked, context=built.context)

    by_graph: dict[str, list[dict[str, Any]]] = {}
    for payload in model.sent:
        blocks = _system(payload)
        opening = blocks[0]["text"] if blocks else ""
        graph = next(
            (
                name
                for name, prompt in (
                    ("reviewer", "You review."),
                    ("checker", "You check."),
                    ("general-purpose", "In order to complete"),
                )
                if opening.startswith(prompt)
            ),
            "agent" if blocks else "researcher",
        )
        by_graph.setdefault(graph, []).append(payload)
    told = {
        graph: all(any(TEXT in b["text"] for b in _system(p)) for p in sent)
        for graph, sent in by_graph.items()
    }

    assert told == {
        "agent": text is not None,
        "reviewer": text is not None,
        "checker": text is not None,
        "general-purpose": text is not None,
        "researcher": False,
    }
    assert {graph: _breakpoints(sent[0]) for graph, sent in by_graph.items()} == {
        "agent": 3,
        "reviewer": 3,
        "checker": 0,
        "general-purpose": 3,
        "researcher": 0,
    }


STAMP = "Every figure is checked twice."


class _Stamps(AgentMiddleware):
    """A deployment's middleware that writes the prompt, as a house rule added late does."""

    name = "Stamps"

    def _stamped(self, request: Any) -> Any:
        system = request.system_message
        blocks = [] if system is None else list(system.content_blocks)
        return request.override(
            system_message=SystemMessage(
                content_blocks=[*blocks, {"type": "text", "text": f"\n\n{STAMP}"}]
            )
        )

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._stamped(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._stamped(request))


def test_a_helpers_session_text_follows_what_its_own_middleware_writes(
    cfg, session_dir, way
):
    """A helper's stack meets no merge to put the session's text in a slot, so listed
    ahead of the helper's own middleware it was followed by what that middleware wrote."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "reviewer.yaml").write_text(REVIEWER, encoding="utf-8")
    (subagents_dir(cfg) / "checker.yaml").write_text(
        CHECKER.replace("system_prompt:", "middlewares: [stamps]\nsystem_prompt:"),
        encoding="utf-8",
    )
    model = _model(
        _task("reviewer", "t1"),
        _task("checker", "t2"),
        AIMessage(content="checked"),
        AIMessage(content="reviewed"),
        AIMessage(content="done"),
    )
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=model,
        middleware_registry={"stamps": _Stamps},
        capabilities=Capabilities(subagents=("reviewer", "checker")),
        session_instructions=TEXT,
    )

    asked = {"messages": [{"role": "user", "content": "go"}]}
    if way == "async":
        asyncio.run(built.graph.ainvoke(asked, context=built.context))
    else:
        built.graph.invoke(asked, context=built.context)

    (checking,) = [
        payload
        for payload in model.sent
        if _system(payload) and _system(payload)[0]["text"].startswith("You check.")
    ]
    told = "".join(block["text"] for block in _system(checking))
    assert told.index(STAMP) < told.index(TEXT)


class _OwnCaching(AgentMiddleware):
    """A deployment's replacement for deepagents' prompt caching, as one is written."""

    name = CACHING


def _own_caching(cfg, session_dir, **wiring: Any):
    return build_agent(
        cfg,
        agent=AgentSpec(
            name="cached",
            description="an agent replacing the prompt caching",
            system_prompt="You work.",
            middlewares=("caching",),
        ),
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"caching": _OwnCaching},
        **wiring,
    )


@pytest.mark.parametrize("text", [TEXT, ""])
def test_a_deployments_own_prompt_caching_is_refused_beside_it(cfg, session_dir, text):
    """Both take one slot and the deployment's wins, so the text would never be sent --
    refused whatever this turn's text, or a deployment would see it on some sessions."""
    with (
        pytest.warns(UserWarning, match=CACHING),
        pytest.raises(ConfigError, match="session_instructions="),
    ):
        _own_caching(cfg, session_dir, session_instructions=text)


def test_a_deployments_own_prompt_caching_still_builds_without_it(cfg, session_dir):
    """The control: replacing deepagents' caching is a deployment's to do, and is only
    warned about, until it wires text that slot sends."""
    with pytest.warns(UserWarning, match=CACHING):
        built = _own_caching(cfg, session_dir)

    assert [m for m in built.middleware if isinstance(m, _OwnCaching)]
    assert not [m for m in built.middleware if isinstance(m, PromptCachingMiddleware)]


class Asked:
    """Answers with the session and how many times it has been asked, and records whether
    it was asked on an event loop."""

    def __init__(self) -> None:
        self.asked: list[str] = []
        self.on_a_loop: bool | None = None

    def text(self, cfg, session_id: str) -> str:
        self.asked.append(session_id)
        try:
            asyncio.get_running_loop()
            self.on_a_loop = True
        except RuntimeError:
            self.on_a_loop = False
        return f"Session {session_id}, turn {len(self.asked)}."


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_the_plugin_is_asked_once_a_turn_for_its_session(scripted, awaited):
    """Asked once at startup, a session's text could not follow it; asked on the event
    loop, a plugin that reads a database would hold up every other turn."""
    an_agent(scripted, **EVERY_TOOL)
    asked = Asked()
    wiring = Plugins(session_instructions=asked).keywords()
    kf = Kingfisher(scripted, sessions=LocalSessions(), **wiring)

    for task in ("go", "again"):
        Scripted.script.append(AIMessage(content="done"))
        request = Request(task, agent="only", session_id="s")
        asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    assert asked.asked == ["s", "s"]
    assert asked.on_a_loop is False, "the plugin ran on the event loop"
    assert "Session s, turn 2." in str(Scripted.heard[-1][0].content)

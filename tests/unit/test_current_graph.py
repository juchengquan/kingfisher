"""`kingfisher.current_graph()`, read by an offered tool in each graph a turn runs."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.session import runtime
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir

WHERE = '''from kingfisher import current_graph


def where() -> str:
    """Say which graph called this tool."""
    return str(current_graph())


TOOLS = [where]
'''

#: A subagent its definition builds, handed the `where` tool kingfisher wraps for it.
COMPILED = '''SUBAGENTS = [
    {
        "name": "researcher",
        "description": "Says where it is, from a graph it built itself.",
        "tools": ["where"],
        "build": lambda model, tools: _graph(model, tools),
    }
]


def _graph(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)
'''

HELPER = """name: helper
description: Says where it is.
tools: [where]
system_prompt: |
  You say where you are.
"""


def _calls(name: str, number: int, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{number}"}])


def _said(built: Any, *, awaited: bool = False) -> dict[str, str]:
    """What each `where` call answered in one turn, subagents' included, by call id."""
    options: dict[str, Any] = {
        "config": {"configurable": {"thread_id": "where"}},
        "stream_mode": "updates",
        "subgraphs": True,
        "context": built.context,
    }
    start = {"messages": [{"role": "user", "content": "go"}]}

    async def drive() -> list[Any]:
        return [chunk async for _, chunk in built.graph.astream(start, **options)]

    chunks = (
        asyncio.run(drive())
        if awaited
        else [chunk for _, chunk in built.graph.stream(start, **options)]
    )
    said: dict[str, str] = {}
    for chunk in chunks:
        for update in chunk.values():
            for message in runtime.messages_in(update):
                if isinstance(message, ToolMessage) and message.name == "where":
                    said[message.tool_call_id] = str(message.content)
    return said


def _a_tool(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "where.py").write_text(WHERE, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)


def test_each_graph_a_turn_runs_names_itself_to_its_tools(cfg, session_dir):
    """A tool in a subagent reading the agent's name, or the agent's reading the subagent's
    after it returned. Driven through a real turn's graphs rather than the middleware alone:
    the name has to cross into the threads langgraph runs a subagent's calls on.
    """
    _a_tool(cfg)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(
            responses=[
                _calls("where", 1),
                _calls("task", 2, description="say where", subagent_type="helper"),
                _calls("where", 3),
                AIMessage(content="said"),
                _calls("task", 4, description="say where", subagent_type="general-purpose"),
                _calls("where", 5),
                AIMessage(content="said"),
                _calls("where", 6),
                AIMessage(content="done"),
            ]
        ),
        capabilities=Capabilities(subagents=("helper",)),
    )

    assert _said(built) == {
        "call-1": "None",
        "call-3": "helper",
        "call-5": "general-purpose",
        "call-6": "None",
    }


@pytest.mark.parametrize("awaited", [False, True], ids=["sync", "async"])
def test_a_compiled_subagent_names_itself_to_the_tools_it_was_handed(
    cfg, session_dir, awaited
):
    """A compiled subagent's tool reading `None`, as if the agent had called it: no
    middleware of kingfisher's runs in that graph, so the name has to travel on the tool.
    Awaited too, since the wrapper's async run is not a copy of its sync one.
    """
    _a_tool(cfg)
    (subagents_dir(cfg) / "researcher.py").write_text(COMPILED, encoding="utf-8")
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(
            responses=[
                _calls("task", 1, description="say where", subagent_type="researcher"),
                _calls("where", 2),
                AIMessage(content="said"),
                _calls("where", 3),
                AIMessage(content="done"),
            ]
        ),
        capabilities=Capabilities(subagents=("researcher",)),
    )

    assert _said(built, awaited=awaited) == {"call-2": "researcher", "call-3": "None"}

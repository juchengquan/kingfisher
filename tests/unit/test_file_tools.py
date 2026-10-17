"""deepagents' file tools as kingfisher hands them to a model, driven through real turns."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.file_tools import READ_LINES, WHOLE, FileTools
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import an_agent, start, subagents_dir
from tests.unit.scripted import Scripted

HELPER = "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n"


@pytest.fixture
def reading(scripted, monkeypatch):
    """An agent consulting a subagent, a 1,200-line file in the session, and the tools each
    model call was offered kept in order."""
    offered: list[list[Any]] = []

    def bind(self, tools, **kwargs):
        offered.append(list(tools))
        return self

    monkeypatch.setattr(Scripted, "bind_tools", bind)
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(scripted, subagents="[helper]")
    start(scripted, "s")
    long = scripted.workspace / "sessions" / "s" / "outputs" / "long.txt"
    long.write_text("".join(f"row {n:04d}\n" for n in range(1_200)), encoding="utf-8")
    return scripted, offered


def _turn(cfg: Any, *script: AIMessage, way: str = "sync") -> None:
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _read(at: str, **args: Any) -> AIMessage:
    call = {"name": "read_file", "args": {"file_path": "/outputs/long.txt", **args}, "id": at}
    return AIMessage(content="", tool_calls=[call])


def _answer(at: str) -> str:
    heard = Scripted.heard[-1]
    (said,) = [one.text for one in heard if isinstance(one, ToolMessage) and one.tool_call_id == at]
    return said


def test_a_read_naming_no_limit_reads_a_thousand_lines(reading, way):
    """At deepagents' 100 lines, paging through files was a third of every call a turn made."""
    cfg, _ = reading

    _turn(cfg, _read("r1"), AIMessage("done"), way=way)

    said = _answer("r1")
    assert "row 0999" in said
    assert "row 1000" not in said


def test_a_limit_the_model_names_is_left_as_it_is(reading):
    """A model asking for a slice it wants gets that slice, not a thousand lines."""
    cfg, _ = reading

    _turn(cfg, _read("r1", limit=50), AIMessage("done"))

    said = _answer("r1")
    assert "row 0049" in said
    assert "row 0050" not in said


@pytest.mark.parametrize("subagent", ["helper", "general-purpose"])
def test_every_graph_is_told_to_read_a_file_whole_and_shown_the_default(reading, subagent):
    """Told 100 lines and to page, most reads named a limit of 100 themselves, so a default
    the description does not mention would change almost nothing."""
    cfg, offered = reading
    asked = {"description": "look", "subagent_type": subagent}
    consulting = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    _turn(cfg, consulting, AIMessage("looked"), AIMessage("done"))

    assert len(offered) == 3
    for tools in offered:
        (read,) = [tool for tool in tools if getattr(tool, "name", None) == "read_file"]
        assert WHOLE in read.description
        assert "instead of reading them whole" not in read.description, "deepagents reworded it"
        assert read.args["limit"]["default"] == READ_LINES


def test_it_adds_no_step_to_the_agents_loop(reading):
    """langgraph's recursion limit counts graph steps, and a hook beside the model call is one."""
    cfg, _ = reading
    backend = backend_at(cfg, cfg.workspace / "sessions" / "s")

    nodes = build_agent(cfg, backend=backend).graph.get_graph().nodes

    assert "TodoListMiddleware.after_model" in nodes, "hooks are not nodes named for their class"
    assert not [one for one in nodes if FileTools.__name__ in one]

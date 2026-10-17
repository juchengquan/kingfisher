"""An agent near its step limit is told how many model calls it has left, and a subagent is
held to the same budget."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.step_budget import StepBudgetMiddleware
from kingfisher.core.layout import HARNESS, TRANSCRIPT_FILE
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import an_agent, start, subagents_dir
from tests.unit.scripted import Scripted

LEFT = re.compile(r"You have about (\d+) model calls? left in this turn\.")
LAST = "This is your last model call in this turn."


@pytest.fixture
def busy(scripted):
    an_agent(scripted)
    start(scripted, "s")
    return scripted


def _turn(cfg: Any, calls: int, way: str = "sync") -> Any:
    """A turn whose model lists the session `calls` times, then answers."""
    Scripted.script.extend(
        AIMessage(content="", tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"c{n}"}])
        for n in range(calls)
    )
    Scripted.script.append(AIMessage("done"))
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _told() -> list[int | str | None]:
    """For each model call, how many calls it was told it had after it, `"last"` where it
    was told it was the last, or `None`."""
    said: list[int | str | None] = []
    for heard in Scripted.heard:
        last = heard[-1].text if isinstance(heard[-1], HumanMessage) else ""
        found = LEFT.search(last)
        said.append(int(found[1]) if found else "last" if LAST in last else None)
    return said


def test_a_turn_past_four_fifths_of_its_steps_is_told_on_every_call_after(busy, way):
    """A turn ran to its limit and stopped mid-work, never told it was close."""
    cfg = replace(busy, recursion_limit=30)

    result = _turn(cfg, calls=20, way=way)

    told = _told()
    assert result.stop_reason == "max_steps"
    # Model calls fall on steps 2, 5, 8 ... and a 30-step turn makes its last at 29.
    assert told == [None] * 8 + [1, "last"], told


def test_the_count_follows_the_limit_a_deployment_sets(busy):
    """Read off langgraph's own limit, so a deployment's `KINGFISHER_RECURSION_LIMIT` holds."""
    cfg = replace(busy, recursion_limit=60)

    _turn(cfg, calls=30)

    told = _told()
    assert told == [None] * 16 + [3, 2, 1, "last"], told


def test_a_turn_well_inside_its_steps_is_never_told(busy):
    """A note on every call would cost every turn tokens for nothing."""
    _turn(busy, calls=5)

    assert _told() == [None] * 6


def test_the_note_is_never_kept(busy):
    """Kept, it would be sent again on every later call and every later turn, stale."""
    cfg = replace(busy, recursion_limit=30)

    _turn(cfg, calls=20)

    kept = (cfg.workspace / "sessions" / "s" / HARNESS / TRANSCRIPT_FILE).read_text()
    assert "model calls left" not in kept and "model call left" not in kept


HELPER = "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n"


@pytest.fixture
def consulting(busy):
    subagents_dir(busy).mkdir(parents=True, exist_ok=True)
    (subagents_dir(busy) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(busy, subagents="[helper]")
    return busy


def _run(cfg: Any, *script: AIMessage, way: str = "sync") -> Any:
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _ask(who: str) -> AIMessage:
    asked = {"description": "go", "subagent_type": who}
    return AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])


def _listing(n: int) -> AIMessage:
    call = {"name": "ls", "args": {"path": "/"}, "id": f"s{n}"}
    return AIMessage(content="still looking", tool_calls=[call])


def _handed_back() -> str:
    (said,) = [
        one.text
        for one in Scripted.heard[-1]
        if isinstance(one, ToolMessage) and one.tool_call_id == "t1"
    ]
    return said


@pytest.mark.parametrize("who", ["helper", "general-purpose"])
def test_a_subagent_at_the_end_of_its_budget_hands_back_what_it_has(consulting, who, way):
    """Not bound by the turn's limit, a subagent made 41 calls inside a 30-step turn."""
    cfg = replace(consulting, recursion_limit=30)

    result = _run(cfg, _ask(who), *(_listing(n) for n in range(10)), AIMessage("done"), way=way)

    # The agent's call, the subagent's ten, the agent's answer.
    assert _told() == [None] + [None] * 8 + [1, "last"] + [None]
    assert _handed_back().startswith(
        "[Stopped: this subagent reached its limit of 10 model calls, and the answer below "
        "is what it had then.]\n\nstill looking"
    )
    assert result.stop_reason == "end_turn", "a subagent's limit ended the agent's turn"


@pytest.mark.parametrize("listings", [2, 9], ids=["early", "on its last call"])
def test_a_subagent_that_answers_within_its_budget_is_handed_back_unmarked(consulting, listings):
    """Only a subagent stopped mid-work says so, even when it answers on its very last call."""
    cfg = replace(consulting, recursion_limit=30)
    found = AIMessage("found it")

    _run(cfg, _ask("helper"), *(_listing(n) for n in range(listings)), found, AIMessage("done"))

    assert _handed_back().startswith("found it")


def test_it_adds_no_step_to_the_agents_loop(busy):
    """A hook beside the model call is a graph step, which is what it counts."""
    backend = backend_at(busy, busy.workspace / "sessions" / "s")

    nodes = build_agent(busy, backend=backend).graph.get_graph().nodes

    assert "TodoListMiddleware.after_model" in nodes, "hooks are not nodes named for their class"
    assert not [one for one in nodes if StepBudgetMiddleware.__name__ in one]

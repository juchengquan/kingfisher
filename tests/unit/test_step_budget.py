"""An agent near its step limit is told how many model calls it has left."""

from __future__ import annotations

import asyncio
import re
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.step_budget import StepBudget
from kingfisher.core.layout import HARNESS, TRANSCRIPT_FILE
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import an_agent, start
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


def test_it_adds_no_step_to_the_agents_loop(busy):
    """A hook beside the model call is a graph step, which is what it counts."""
    backend = backend_at(busy, busy.workspace / "sessions" / "s")

    nodes = build_agent(busy, backend=backend).graph.get_graph().nodes

    assert "TodoListMiddleware.after_model" in nodes, "hooks are not nodes named for their class"
    assert not [one for one in nodes if StepBudget.__name__ in one]

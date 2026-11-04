"""What an offered tool's return value becomes, and why this pins somebody else's
behaviour.
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.core.agent.assembly import build_agent
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel, tools_dir

STRING = '''
def answers(x: str) -> str:
    """Return a string. Use never; this is a fixture."""
    return "three columns"


TOOLS = [answers]
'''

MAPPING = '''
def answers(x: str) -> dict:
    """Return a mapping. Use never; this is a fixture."""
    return {"rows": 3, "cols": ["a", "b"]}


TOOLS = [answers]
'''

NOTHING = '''
def answers(x: str):
    """Return nothing at all. Use never; this is a fixture."""
    return None


TOOLS = [answers]
'''

UNSERIALISABLE = '''
class Column:
    def __repr__(self):
        return "<Column>"


def answers(x: str):
    """Return something JSON will not take. Use never; this is a fixture."""
    return Column()


TOOLS = [answers]
'''

COMMAND = '''
from langgraph.types import Command


def answers(x: str):
    """Return a Command rather than a value. Use never; this is a fixture."""
    return Command(
        update={
            "messages": [
                {"role": "tool", "content": "written by the tool", "tool_call_id": "c1"}
            ]
        }
    )


TOOLS = [answers]
'''


TEXT = {"type": "text", "text": "a chart"}
IMAGE = {"type": "image", "base64": "iVBORw0KGgo=", "mime_type": "image/png"}

SEVERAL = '''
TEXT = {text!r}
IMAGE = {image!r}


def answers(x: str):
    """Return several things. Use never; this is a fixture."""
    return {value}


TOOLS = [answers]
'''

#: What a tool returns when it returns several things, and what the model is shown.
RETURNED_SEVERAL = {
    "a tuple of strings": ('("three", "columns")', '["three", "columns"]'),
    "a tuple of other things": ('("rows", 3)', '["rows", 3]'),
    "a list of blocks, an image among them": ("[TEXT, IMAGE]", [TEXT, IMAGE]),
    "a tuple of blocks": ("(TEXT, IMAGE)", [TEXT, IMAGE]),
    "a string beside a block": ('["a chart", IMAGE]', json.dumps(["a chart", IMAGE])),
    "an empty list": ("[]", []),
}


def _results(cfg, session_dir, body: str) -> list[ToolMessage]:
    """Install a tool with that body, have a model call it once, and return every tool
    result the run produced.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "answers.py").write_text(body, encoding="utf-8")

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "answers", "args": {"x": "q"}, "id": "c1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
    ).graph
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )
    return [m for m in out["messages"] if isinstance(m, ToolMessage)]


def test_what_the_model_is_shown(cfg, session_dir):
    """The table's rows for a single value, asserted as the model sees them rather
    than as langchain computes them.

    `status` is asserted with the content because the guide's claim is that *nothing
    fails*: a tool returning the wrong shape reports success and tells the model
    something unhelpful, and a row that started raising would still pass a content-only
    check on the others.
    """
    for body, shown in [
        (STRING, "three columns"),
        (MAPPING, '{"rows": 3, "cols": ["a", "b"]}'),
        (NOTHING, "null"),
        (UNSERIALISABLE, "<Column>"),
    ]:
        results = _results(cfg, session_dir, body)

        assert [m.content for m in results] == [shown]
        assert [m.status for m in results] == ["success"]


@pytest.mark.parametrize("returned", sorted(RETURNED_SEVERAL))
def test_what_the_model_is_shown_for_several_things(cfg, session_dir, returned):
    """A row of the guide's table for a list or a tuple that stopped being true.

    Driven through a built agent rather than `_format_output`, which is the first of
    two readings: it makes any list or tuple of strings and blocks into content, and
    langgraph's tool node then encodes all but a list of typed blocks. Reading the
    first alone predicts `("three", "columns")` arriving as two strings.
    """
    value, shown = RETURNED_SEVERAL[returned]
    body = SEVERAL.format(text=TEXT, image=IMAGE, value=value)

    results = _results(cfg, session_dir, body)

    assert [m.content for m in results] == [shown]
    assert [m.status for m in results] == ["success"]


def test_a_command_is_applied_rather_than_wrapped(cfg, session_dir) -> None:
    """The escape hatch the guide names, and the cost it names beside it."""
    results = _results(cfg, session_dir, COMMAND)

    assert [m.content for m in results] == ["written by the tool"]
    assert results[0].name is None

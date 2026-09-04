"""What a workspace tool's return value becomes, and why this pins somebody
else's behaviour.

`docs/guides/tools.md` tells an author to return a string and then says what
happens when they do not: `json.dumps` first, the `repr` when that fails, and a
`ToolMessage` or a langgraph `Command` handed back untouched. None of it is
kingfisher's rule -- `_format_output` in `langchain_core.tools.base` decides all
four -- and that is the argument for holding it here rather than trusting it. A
documented promise nothing tests is one that changes in an upgrade and is
discovered at some deployment's first tool call, which is the failure
`test_tool_shapes.py` was written after.

Through a graph that really dispatches, for the same reason that file gives: the
interesting part is not what langchain does to a value in isolation, it is what
the model ends up being shown after deepagents has wrapped the tool and the
middleware has passed the result through.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.infrastructure.harness.agent import build_agent
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


def _results(cfg, session_dir, body: str) -> list[ToolMessage]:
    """Install a tool with that body, have a model call it once, and return
    every tool result the run produced."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "answers.py").write_text(body, encoding="utf-8")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "answers", "args": {"x": "q"}, "id": "c1"}],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )
    return [m for m in out["messages"] if isinstance(m, ToolMessage)]


@pytest.mark.parametrize(
    ("body", "shown"),
    [
        pytest.param(STRING, "three columns", id="str"),
        pytest.param(MAPPING, '{"rows": 3, "cols": ["a", "b"]}', id="dict"),
        pytest.param(NOTHING, "null", id="none"),
        pytest.param(UNSERIALISABLE, "<Column>", id="object"),
    ],
)
def test_what_the_model_is_shown(cfg, session_dir, body: str, shown: str) -> None:
    """The four rows of the table the guide prints, asserted as the model sees
    them rather than as langchain computes them.

    `status` is asserted with the content because the guide's claim is not only
    that a dict becomes JSON -- it is that *nothing fails*, so a tool returning
    the wrong shape reports success and tells the model something unhelpful. A
    row that started raising would still pass a content-only check on the other
    three.
    """
    results = _results(cfg, session_dir, body)

    assert [m.content for m in results] == [shown]
    assert [m.status for m in results] == ["success"]


def test_a_command_is_applied_rather_than_wrapped(cfg, session_dir) -> None:
    """The escape hatch the guide names, and the cost it names beside it.

    `_format_output` returns any `ToolOutputMixin` untouched, and langgraph's
    `Command` is one -- so the tool's own message reaches the transcript instead
    of a wrapped return value, and `WorkspaceToolErrors` never sees it because
    that middleware catches exceptions and nothing else.

    The `name` assertion is the cost: a run event takes its tool name from the
    message, so a `Command` writing its own leaves the log saying a tool ran
    without saying which. Documented rather than refused -- `decisions.md` has
    the argument -- and asserted here so that "documented" means something.
    """
    results = _results(cfg, session_dir, COMMAND)

    assert [m.content for m in results] == ["written by the tool"]
    assert results[0].name is None

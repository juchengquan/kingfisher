"""The three shapes a workspace tool may be written in, and the one it may not.

A `BaseTool` -- from `@tool` or from a class of your own -- or a plain function.

`tool_name` has always said both are accepted -- "because `create_deep_agent`
accepts both, and a definition should not have to know which one deepagents
prefers this month" -- and nothing held it to that. Every fixture in this suite
used `@tool`, so the claim lived in a docstring, and a langchain upgrade that
stopped resolving a bare callable would have surfaced at some workspace's first
tool call rather than here.

The shipped `line_count` is the example of the third; this is the guarantee
behind all of them, and the refusal for the near miss: a *class* where an
instance was meant.
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.tool import tool_name
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository
from kingfisher.infrastructure.harness.agent import build_agent, registered_tools
from tests.conftest import FakeToolCallingModel, tools_dir

BARE = '''
def shout(text: str) -> str:
    """Return the text in capitals. Use when a caller asks to shout something,
    or wants a heading rendered loudly."""
    return text.upper()


TOOLS = [shout]
'''

BARE_FAILS = '''
def bare_boom(path: str) -> str:
    """Always fails, so a test does not have to hope a model calls it. Use
    never; it exists to show what a failure looks like."""
    raise FileNotFoundError(path)


TOOLS = [bare_boom]
'''


def _install(cfg, name: str, body: str) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / f"{name}.py").write_text(body, encoding="utf-8")


def _calls(name: str, args: dict):
    return [
        AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": "c1"}]),
        AIMessage(content="done"),
    ]


def test_a_plain_function_is_named_by_the_function(cfg):
    """No `.name` to read, so `__name__` is the name a request grants and an
    allowlist matches -- and everything downstream keys on that one string."""
    _install(cfg, "shout", BARE)

    (found,) = LocalToolRepository(tools_dir(cfg)).found

    assert tool_name(found.tool) == "shout"
    assert not hasattr(found.tool, "name"), "this is the case the fallback is for"


def test_a_plain_function_is_offered_to_the_model_and_dispatches(cfg, session_dir):
    """Through a graph that really dispatches, because the interesting part is
    not that it loads -- it is that deepagents wraps it and the call arrives."""
    _install(cfg, "shout", BARE)

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("shout", {"text": "hi"})),
        capabilities=Capabilities(tools=("shout",)),
    )

    assert "shout" in (registered_tools(graph) or ())
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )
    results = [m.content for m in out["messages"] if isinstance(m, ToolMessage)]
    assert results == ["HI"]
    assert out["messages"][-1].content == "done"


def test_the_docstring_and_the_annotations_are_what_the_model_gets(cfg, session_dir):
    """What you give up by leaving the decorator off, stated as behaviour: not
    the description or the schema, which come from the function as written --
    only the ability to make them differ from it."""
    _install(cfg, "shout", BARE)

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )
    node = getattr(graph, "nodes", {}).get("tools")
    wrapped = getattr(getattr(node, "bound", None), "tools_by_name", {})["shout"]

    assert "asks to shout" in wrapped.description
    assert list(wrapped.args) == ["text"]


def test_a_plain_function_fails_the_way_a_decorated_one_does(cfg, session_dir):
    """`WorkspaceToolErrors` is built from the names the workspace defined, and
    a plain function's name comes from the same `tool_name` everything else
    uses -- so the guard covers it without knowing which kind it is."""
    _install(cfg, "bare_boom", BARE_FAILS)

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("bare_boom", {"path": "/x"})),
    )
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    failed = [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert "FileNotFoundError" in failed[0].content
    assert out["messages"][-1].content == "done"


# -- a class of your own, and the near miss beside it -----------------------


SUBCLASS = """
from typing import Type

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class ShoutInput(BaseModel):
    text: str = Field(description="What to shout.")


class Shout(BaseTool):
    \"\"\"A tool written as a class, for the schema it wants to declare.\"\"\"

    name: str = "shout"
    description: str = "Return the text in capitals. Use when asked to shout."
    args_schema: Type[BaseModel] = ShoutInput

    def _run(self, text: str) -> str:
        return text.upper()


TOOLS = [{export}]
"""


def test_a_basetool_subclass_is_named_by_its_own_field(cfg, session_dir):
    """The instance form, which is what a class is for: `name` and
    `description` are declared rather than taken from the function, and
    `args_schema` is yours to write.
    """
    _install(cfg, "shout", SUBCLASS.format(export="Shout()"))

    (found,) = LocalToolRepository(tools_dir(cfg)).found

    assert tool_name(found.tool) == "shout"  # the field, not the class name

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("shout", {"text": "hi"})),
    )
    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    assert [m.content for m in out["messages"] if isinstance(m, ToolMessage)] == ["HI"]


def test_the_class_itself_is_refused_rather_than_offered(cfg):
    """`TOOLS = [Shout]` for `TOOLS = [Shout()]` -- one character, and the only
    mistake in this area that produced a *successful* wrong answer.

    Measured before the refusal: it loaded, was advertised under the class name
    `Shout` rather than its own `shout`, and calling it instantiated the class
    and handed the model `status="success"` with the repr of a
    `CallbackManager` in it. The run carried on.

    The same family as `TOOLS = add`, which the container check refuses one
    level up: both pass a duck test, and neither says anything.
    """
    import pytest

    from kingfisher.infrastructure.catalogue.tools import ToolError

    _install(cfg, "shout", SUBCLASS.format(export="Shout"))

    with pytest.raises(ToolError, match=r"write Shout\(\) to build one"):
        _ = LocalToolRepository(tools_dir(cfg)).found


def test_a_function_is_not_caught_by_that_refusal(cfg):
    """The refusal is `isinstance(tool, type)`, and the point of it is that
    nothing legitimate is one: a function is not a class, and neither is a
    `StructuredTool` or an instance of your own."""
    _install(cfg, "shout", BARE)

    assert [tool_name(f.tool) for f in LocalToolRepository(tools_dir(cfg)).found] == ["shout"]

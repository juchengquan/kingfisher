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

import functools

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.tool import named, tool_name
from kingfisher.infrastructure.catalogue.tools import LocalToolRepository, ToolError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.surface import registered_tools
from tests.conftest import FakeToolCallingModel, tools_dir

BARE = '''
def shout(text: str) -> str:
    """Return the text in capitals. Use when a caller asks to shout something,
    or wants a heading rendered loudly."""
    return text.upper()


TOOLS = [shout]
'''

DECORATED = '''
from langchain_core.tools import tool


@tool
def shout(text: str) -> str:
    """Return the text in capitals. Use when a caller asks to shout something."""
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
    _install(cfg, "shout", SUBCLASS.format(export="Shout"))

    with pytest.raises(ToolError, match=r"write Shout\(\) to build one"):
        _ = LocalToolRepository(tools_dir(cfg)).found


def test_a_function_is_not_caught_by_that_refusal(cfg):
    """The refusal is `isinstance(tool, type)`, and the point of it is that
    nothing legitimate is one: a function is not a class, and neither is a
    `StructuredTool` or an instance of your own."""
    _install(cfg, "shout", BARE)

    assert [tool_name(f.tool) for f in LocalToolRepository(tools_dir(cfg)).found] == ["shout"]


# Not a near miss like the class above -- an entry that is not a tool in any
# sense, which the loader took and named by its `repr`. `TOOLS = ["line_count"]`
# is the one a person actually writes: the *name* of the tool, where the tool
# goes, by analogy with every other format in this package, which is data and
# does name things by string.
NOT_TOOLS = {
    "a name where the tool goes": ('TOOLS = ["line_count"]', "str", "'line_count'"),
    "a number": ("TOOLS = [42]", "int", "42"),
    "a declaration, as the other formats take": (
        'TOOLS = [{"name": "line_count"}]',
        "dict",
        "{'name': 'line_count'}",
    ),
    "a gap in the list": ("TOOLS = [None]", "NoneType", "None"),
}

SHAPES = {
    "a plain function": BARE,
    "@tool": DECORATED,
    "an instance": SUBCLASS.format(export="Shout()"),
}


@pytest.mark.parametrize(("body", "kind", "shown"), NOT_TOOLS.values(), ids=NOT_TOOLS)
def test_an_entry_that_is_not_a_tool_is_refused(cfg, body, kind, shown):
    """Measured before this: every one of them loaded. The string was offered to
    the model as a tool named `'line_count'`, quotes and all; the dict as
    `{'name': 'line_count'}`. `tool_name` fell through to its `repr` fallback,
    which exists so that naming never raises, and so gave junk a name.

    The build then died at `AttributeError: 'function' object has no attribute
    'name'`, from inside deepagents, naming neither the file nor the entry.
    """
    _install(cfg, "probe", body)

    with pytest.raises(ToolError) as caught:
        _ = LocalToolRepository(tools_dir(cfg)).found

    said = str(caught.value)
    assert "probe.py" in said, "the refusal has to name the file to beat the crash it replaces"
    assert kind in said, f"and say what it found rather than only that it was wrong: {said}"
    assert shown in said, f"and show the entry, which is what makes it findable: {said}"


@pytest.mark.parametrize("body", SHAPES.values(), ids=SHAPES)
def test_the_three_documented_shapes_are_not_caught_by_that_refusal(cfg, body):
    """The refusal asks `named`, not `callable`, and this is the half that pins
    the difference: a `BaseTool` is not callable at all -- measured, `@tool`
    returns a `StructuredTool` whose `callable()` is False -- so a callable-only
    check would refuse two of the three shapes this file exists to guarantee.
    """
    _install(cfg, "probe", body)

    assert [tool_name(f.tool) for f in LocalToolRepository(tools_dir(cfg)).found] == ["shout"]


def test_the_rule_is_the_one_langchain_itself_applies():
    """Why `named` is the right rule and not merely the one this layer can reach.

    A catalogue may import `yaml` and nothing else, so `isinstance(tool,
    BaseTool)` is not available there -- but it would not have been better.
    Measured against `convert_to_openai_tool`: langchain names a bare callable
    by `__name__` and raises on anything carrying neither that nor `.name`.
    Everything refused above is something langchain would have died on anyway;
    the check only moves the failure to where the file can still be named.

    A `functools.partial` is the case that separates the two rules -- callable,
    unusable, and accepted by any check that asks only whether it can be called.
    """
    from langchain_core.utils.function_calling import convert_to_openai_tool

    def line_count(text: str) -> int:
        """Count the lines. Use when asked."""
        return len(text.splitlines())

    assert named(line_count)
    assert convert_to_openai_tool(line_count)["function"]["name"] == "line_count"

    wrapped = functools.partial(line_count)
    assert callable(wrapped), "the shape a callable-only check would let through"
    assert not named(wrapped)
    with pytest.raises(AttributeError, match="__name__"):
        convert_to_openai_tool(wrapped)

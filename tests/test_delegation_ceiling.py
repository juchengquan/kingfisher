"""A subagent may never do more than the request that reached it.

`ToolAllowlist` refuses at `wrap_tool_call`, and that held -- for the parent.
A subagent has its own middleware stack and inherits none of the parent's, so
a request that withheld `execute` handed it straight to any delegate. The
restriction looked like a wall with a door beside it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities, narrowed
from kingfisher.infrastructure.agent import build_agent
from kingfisher.infrastructure.definitions import read_subagent
from kingfisher.infrastructure.delegation import as_subagent
from kingfisher.infrastructure.scoping import ToolAllowlist
from tests.conftest import FakeToolCallingModel

HELPER = """---
name: helper
description: Declares no tools, so it inherits whatever it is given.
---
You help.
"""


def _with_helper(cfg, definition: str = HELPER, name: str = "helper.md"):
    directory = cfg.subagents_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(definition, encoding="utf-8")
    return cfg


def _subagent_graphs(graph):
    """The compiled subagents, which live in the `task` tool's closure."""
    node = getattr(graph, "nodes", {}).get("tools")
    task_tool = getattr(getattr(node, "bound", None), "tools_by_name", {}).get("task")
    found: dict = {}

    def walk(obj, depth=0):
        if depth > 4 or found:
            return
        for attribute in ("func", "__closure__"):
            value = getattr(obj, attribute, None)
            if attribute == "__closure__":
                for cell in value or ():
                    try:
                        contents = cell.cell_contents
                    except ValueError:
                        continue
                    if isinstance(contents, dict) and any(
                        hasattr(v, "nodes") for v in contents.values()
                    ):
                        found.update(contents)
                        return
                    walk(contents, depth + 1)
            elif value is not None:
                walk(value, depth + 1)

    if task_tool is not None:
        walk(task_tool)
    return found


def _model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def test_a_delegate_may_not_use_what_its_caller_was_denied(cfg, session_dir):
    """The escape, driven rather than inspected.

    The delegate inherits the parent's model, so a scripted one reaches it: it
    calls `execute`, and the question is whether anything stops it. Asserting
    on what the delegate's ToolNode *registers* would prove nothing -- that is
    identical either way, which is how this went unnoticed.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo escaped"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(tools=("read_file", "task"), subagents=("helper",)),
    )

    delegate = _subagent_graphs(graph).get("helper")
    assert delegate is not None, "the declared subagent was not compiled"

    out = delegate.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 6},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "escaped" not in transcript, "the delegate ran a command its caller could not"
    assert "not available for this request" in transcript


def test_the_builtin_delegate_arrives_with_the_ceiling_on(cfg, session_dir):
    """deepagents supplies a `general-purpose` delegate with "the same
    capabilities as the main agent" and none of our middleware, present
    whenever `task` is -- including for a request that declared none.

    It is not withheld. Supplying one by the same name *replaces* it, since
    the specs are keyed by name, so it keeps working and arrives under the
    caller's ceiling. Withholding it would have cost delegation to every
    narrowed request that had not named a delegate, for no extra safety.
    """
    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "execute", "args": {"command": "echo escaped"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(tools=("read_file", "task"), subagents=("helper",)),
    )

    delegate = _subagent_graphs(graph).get("general-purpose")
    assert delegate is not None, "the built-in delegate should still be reachable"

    out = delegate.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 8},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "escaped" not in transcript, "the built-in ran what its caller could not"
    assert "not available for this request" in transcript


def test_the_builtin_survives_when_no_delegates_are_named(cfg, session_dir):
    """`subagents=None` means "no opinion about delegates", and a narrowed
    request that never named one still gets the built-in -- limited, not gone.
    """
    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(tools=("read_file", "task")),
    )

    delegate = _subagent_graphs(graph).get("general-purpose")
    assert delegate is not None

    out = delegate.invoke(
        {
            "messages": [{"role": "user", "content": "go"}],
        },
        config={"recursion_limit": 8},
    )
    assert out["messages"][-1].content == "ok"


def test_an_unnamed_delegate_is_still_refused(cfg, session_dir):
    """The backstop. Only the names we supplied are reachable, so a delegate
    deepagents adds in some future version cannot arrive unrestricted."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "escape", "subagent_type": "something-else"},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
        capabilities=Capabilities(tools=("read_file", "task"), subagents=("helper",)),
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is not a delegate this request may use" in transcript
    assert "general-purpose" in transcript  # and it names what may be reached


def test_an_unrestricted_request_delegates_as_before(cfg, session_dir):
    """The rule attaches to *tool* narrowing. A caller that restricted nothing
    already has everything, so its delegates having everything is not an
    escalation, and deepagents' own spec is left untouched."""
    responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "task",
                    "args": {"description": "help", "subagent_type": "general-purpose"},
                    "id": "c1",
                }
            ],
        ),
        AIMessage(content="done"),
    ]

    graph = build_agent(
        _with_helper(cfg),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=responses),
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"recursion_limit": 12},
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])
    assert "is not a delegate this request may use" not in transcript


# -- one rule, applied at two levels --------------------------------------
#
# `delegation` carried its own copy of the narrowing rule -- identical to
# `capabilities._narrow` across every input pair, with the arguments in the
# other order, and nothing comparing them. It is `capabilities.narrowed` now,
# and this table is checked against both levels from one source: a request
# clamped by what the deployment granted, and a definition clamped by what its
# caller was granted. A second copy that drifted would fail one and not the
# other.
#
# What this cannot catch at the delegate level is a copy that changes only the
# *order*, because the ceiling reaches nothing but `ToolAllowlist`, which keeps
# a set. Verified rather than assumed: reintroducing the old swapped-argument
# copy fails nothing, and there is nothing for it to fail -- same names, same
# set, same behaviour. Two copies differing in which names survive do fail.

NARROWING = [
    (None, None, None),          # neither has an opinion
    (None, ("a",), ("a",)),      # only the cap does
    (("a",), None, ("a",)),      # only the selection does
    (("a", "b"), ("b",), ("b",)),  # both do, and the overlap survives
    (("a",), ("b",), ()),        # both do, and nothing overlaps
    (("b", "a"), ("a", "b"), ("b", "a")),  # the selection's order is kept
]
CASES = pytest.mark.parametrize(("selection", "cap", "expected"), NARROWING)


@CASES
def test_the_rule_itself(selection, cap, expected):
    assert narrowed(selection, by=cap) == expected


@CASES
def test_a_request_is_narrowed_by_it(selection, cap, expected):
    granted = Capabilities(tools=cap)

    assert granted.intersect(Capabilities(tools=selection)).tools == expected


@CASES
def test_a_delegate_is_narrowed_by_it(cfg, selection, cap, expected):
    """The level that carried the copy.

    `None` means no allowlist at all rather than an empty one, because an empty
    tuple would say the opposite -- nothing permitted instead of no opinion.
    """
    spec = replace(read_subagent(HELPER, Path("helper.md")), tools=selection)

    built = as_subagent(spec, cfg, tools=cap)

    allowlists = [m for m in built.get("middleware", []) if isinstance(m, ToolAllowlist)]
    if expected is None:
        assert allowlists == []
    else:
        # It keeps a set, so order is not observable here; the rule test above
        # is where that case is pinned.
        assert allowlists[0]._allowed == set(expected)

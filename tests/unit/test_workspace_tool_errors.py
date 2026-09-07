"""A workspace tool's exception is a failed tool result, not a dead run."""

from __future__ import annotations

import asyncio

import pytest
from langchain_core.messages import ToolMessage

from kingfisher.infrastructure.harness.backend import WorkspaceToolErrors


class _Request:
    """The shape the middleware reads: a tool call with a name and an id."""

    def __init__(self, name: str) -> None:
        self.tool_call = {"name": name, "id": "call-1"}


def _raises(exc: BaseException):
    def handler(_request):
        raise exc

    return handler


def test_a_workspace_tools_exception_becomes_a_failed_tool_result():
    """The failure this was written for."""
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    answer = guard.wrap_tool_call(
        _Request("csv_profile"), _raises(FileNotFoundError("/data/orders.csv"))
    )

    assert isinstance(answer, ToolMessage)
    assert answer.status == "error"
    assert "/data/orders.csv" in answer.content
    assert answer.tool_call_id == "call-1"


def test_the_message_names_the_exception_type():
    """A workspace tool is somebody else's code and its exceptions were not written to
    be read by a model.
    """
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    answer = guard.wrap_tool_call(_Request("probe"), _raises(FileNotFoundError("/data/x")))

    assert "FileNotFoundError" in answer.content


def test_a_built_in_tool_is_left_exactly_as_it_was():
    """The half that keeps this narrow."""
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    with pytest.raises(FileNotFoundError):
        guard.wrap_tool_call(_Request("read_file"), _raises(FileNotFoundError("/data/x")))


def test_a_tool_that_works_is_untouched():
    """The negative control."""
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    assert guard.wrap_tool_call(_Request("probe"), lambda _r: "the answer") == "the answer"


def test_an_interrupt_is_not_a_tool_telling_the_model_something():
    """`BaseException` is deliberately outside the catch."""
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    with pytest.raises(KeyboardInterrupt):
        guard.wrap_tool_call(_Request("probe"), _raises(KeyboardInterrupt()))


#: What the failing handler raises, named so the literal is not inline.
MISSING = "/data/orders.csv"


async def _araises(_request):
    raise FileNotFoundError(MISSING)


def test_the_async_path_behaves_the_same():
    """Both halves exist because the harness uses both, and a guard that held on one
    would be absent exactly when a service is serving.
    """
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    answer = asyncio.run(guard.awrap_tool_call(_Request("csv_profile"), _araises))

    assert isinstance(answer, ToolMessage)
    assert answer.status == "error"


def test_the_async_path_leaves_a_built_in_alone_too():
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    with pytest.raises(FileNotFoundError):
        asyncio.run(guard.awrap_tool_call(_Request("read_file"), _araises))


# -- and that the build actually installs it -------------------------------

A_TOOL = '''
from langchain_core.tools import tool


@tool
def probe_one(text: str) -> str:
    """A tool that exists so the build has a workspace name to guard."""
    return text


TOOLS = [probe_one]
'''


def _guard_in(captured) -> WorkspaceToolErrors | None:
    """The guard the build handed to `create_deep_agent`, if it added one."""
    for entry in captured.get("middleware") or ():
        if isinstance(entry, WorkspaceToolErrors):
            return entry
    return None


def test_the_build_guards_the_names_the_workspace_defined(cfg, session_dir, monkeypatch):
    """A middleware nobody installs guards nothing."""
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import capture_build, tools_dir

    captured = capture_build(monkeypatch)
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "probe.py").write_text(A_TOOL, encoding="utf-8")

    build_agent(cfg, session_dir=session_dir, model=_a_model())

    guard = _guard_in(captured)
    assert guard is not None, "the build installed no WorkspaceToolErrors"
    assert "probe_one" in guard.names


def test_a_workspace_with_no_tools_installs_no_guard(cfg, session_dir, monkeypatch):
    """Nothing to guard, so nothing added."""
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import capture_build

    captured = capture_build(monkeypatch)

    build_agent(cfg, session_dir=session_dir, model=_a_model())

    assert _guard_in(captured) is None


def _a_model():
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


# -- and end to end, through a graph that really dispatches ----------------

ALWAYS_FAILS = '''
from langchain_core.tools import tool


@tool
def always_fails(anything: str) -> str:
    """Raises every time, so a test does not have to hope a model calls it."""
    raise FileNotFoundError("/data/nothing-here.csv")


TOOLS = [always_fails]
'''


def _calls(name: str, call_id: str = "call-1"):
    """A scripted turn that calls one tool, then one that answers."""
    from langchain_core.messages import AIMessage

    return [
        AIMessage(
            content="",
            tool_calls=[{"name": name, "args": {"anything": "x"}, "id": call_id}],
        ),
        AIMessage(content="done"),
    ]


def _graph_with_a_failing_tool(cfg, session_dir):
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    return build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("always_fails")),
    )


def test_a_failing_workspace_tool_does_not_stop_a_run(cfg, session_dir):
    """The claim, through an assembled graph rather than the middleware alone."""
    out = _graph_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}
    )

    failures = [
        m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert failures, "the tool's exception never reached the model"
    assert "FileNotFoundError" in failures[0].content
    assert "/data/nothing-here.csv" in failures[0].content


def test_the_run_carries_on_to_an_answer(cfg, session_dir):
    """Not merely surviving: the turn finishes."""
    out = _graph_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}
    )

    assert out["messages"][-1].content == "done"


# -- and one level down, where delegation put the same tools ----------------

CALLS_IT = """name: helper
description: Calls the tool that fails.
tools: [always_fails]
system_prompt: |
  You call always_fails.
"""


def _delegate_with_a_failing_tool(cfg, session_dir):
    """The same graph, one level down: a delegate holding the failing tool."""
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(CALLS_IT, encoding="utf-8")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("always_fails")),
        capabilities=Capabilities(subagents=("helper",)),
    )
    return _subagent_graphs(graph)["helper"]


def test_a_failing_workspace_tool_does_not_stop_a_delegate_either(cfg, session_dir):
    """The gap the parent's guard left, and the reason it matters more now."""
    out = _delegate_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    failures = [
        m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert failures, "the tool's exception never reached the delegate's model"
    assert "FileNotFoundError" in failures[0].content


def test_the_delegate_carries_on_to_an_answer(cfg, session_dir):
    """Surviving is not enough: the delegate has to finish, or its caller gets nothing
    back and the run is dead a level higher instead.
    """
    out = _delegate_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    assert out["messages"][-1].content == "done"


NESTS = """name: helper
description: Consults another, which calls the tool that fails.
subagents: [deeper]
system_prompt: |
  You ask deeper.
"""

DEEPER = """name: deeper
description: Calls the tool that fails.
tools: [always_fails]
system_prompt: |
  You call always_fails.
"""


def test_a_helper_below_a_delegate_is_guarded_too(cfg, session_dir):
    """Worth its own test rather than assumed from the one above."""
    from kingfisher.domain.capabilities import Capabilities
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(NESTS, encoding="utf-8")
    (subagents_dir(cfg) / "deeper.yaml").write_text(DEEPER, encoding="utf-8")

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=_calls("always_fails")),
        capabilities=Capabilities(subagents=("helper", "deeper")),
    )
    nested = _subagent_graphs(_subagent_graphs(graph)["helper"])["deeper"]

    out = nested.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    assert [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert out["messages"][-1].content == "done"


def test_a_converted_failure_still_reaches_the_run_log(cfg, session_dir, tmp_path):
    """Catching it for the model does not hide it from whoever reads afterwards."""
    from kingfisher.infrastructure.harness.runlog import JsonlRunLogger

    log = tmp_path / "run.jsonl"
    out = _graph_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={
            "callbacks": [
                JsonlRunLogger(log, model="m", endpoint="e", session_id="s")
            ]
        },
    )

    assert [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"], (
        "the model stopped being told, which is what this guard is for"
    )
    assert log.exists(), "the run produced no log at all"
    assert '"tool_error"' in log.read_text(encoding="utf-8"), (
        "the run log lost its only record of a tool that failed"
    )

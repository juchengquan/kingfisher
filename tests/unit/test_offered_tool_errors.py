"""An offered tool's exception is a failed tool result, not a dead run.

Tested against the middleware rather than through a run, deliberately. A run proves
nothing here: re-running the smoke after the fix passed, and the transcript showed the
model had not called the failing tool at all that time.
"""

from __future__ import annotations

import asyncio

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import ToolException

from kingfisher.core.agent.host_paths import HostPathError
from kingfisher.core.agent.middlewares.guarded_calls import (
    GuardedCallsMiddleware,
    OfferedToolFailureMiddleware,
)
from kingfisher.plugins import backend_at
from tests.conftest import STANDING_UNWIRED


class _Request:
    """The shape the middleware reads: a tool call with a name and an id."""

    def __init__(self, name: str) -> None:
        self.tool_call = {"name": name, "id": "call-1"}


def _raises(exc: BaseException):
    def handler(_request):
        raise exc

    return handler


def test_an_offered_tools_exception_becomes_a_failed_tool_result():
    """The failure this was written for."""
    guard = OfferedToolFailureMiddleware(frozenset({"csv_profile"}))

    answer = guard.wrap_tool_call(
        _Request("csv_profile"), _raises(FileNotFoundError("/inputs/orders.csv"))
    )

    assert isinstance(answer, ToolMessage)
    assert answer.status == "error"
    assert "/inputs/orders.csv" in answer.content
    assert answer.tool_call_id == "call-1"


def test_an_offered_tool_refusing_a_host_path_still_names_the_type():
    """The host-path case checked first, so an offered tool's refusal lost the type
    that says it was the tool's own."""
    from kingfisher.core.agent.host_paths import HostPathError

    guard = OfferedToolFailureMiddleware(frozenset({"probe"}))

    answer = guard.wrap_tool_call(_Request("probe"), _raises(HostPathError("/etc/hosts")))

    assert "HostPathError: /etc/hosts" in answer.content


def test_a_built_in_refusing_a_host_path_is_handed_back_as_said():
    """The correction `reject_host_path` writes is for the model, so it goes back as
    written."""
    from kingfisher.core.agent.host_paths import HostPathError

    guard = GuardedCallsMiddleware()

    answer = guard.wrap_tool_call(_Request("read_file"), _raises(HostPathError("use /inputs")))

    assert answer.content == "Error: use /inputs"
    assert answer.status == "error"


def test_a_built_in_tool_is_left_exactly_as_it_was():
    """The half that keeps this narrow."""
    guard = OfferedToolFailureMiddleware(frozenset({"csv_profile"}))

    with pytest.raises(FileNotFoundError):
        guard.wrap_tool_call(_Request("read_file"), _raises(FileNotFoundError("/inputs/x")))


def test_a_tool_that_works_is_untouched():
    """The negative control."""
    guard = OfferedToolFailureMiddleware(frozenset({"probe"}))

    assert guard.wrap_tool_call(_Request("probe"), lambda _r: "the answer") == "the answer"


def test_an_interrupt_is_not_a_tool_telling_the_model_something():
    """`BaseException` is deliberately outside the catch."""
    guard = OfferedToolFailureMiddleware(frozenset({"probe"}))

    with pytest.raises(KeyboardInterrupt):
        guard.wrap_tool_call(_Request("probe"), _raises(KeyboardInterrupt()))


#: What the failing handler raises, named so the literal is not inline.
MISSING = "/inputs/orders.csv"


async def _araises(_request):
    raise FileNotFoundError(MISSING)


def test_the_async_path_behaves_the_same():
    """Both halves exist because the harness uses both, and a guard that held on one
    would be absent exactly when a service is serving.
    """
    guard = OfferedToolFailureMiddleware(frozenset({"csv_profile"}))

    answer = asyncio.run(guard.awrap_tool_call(_Request("csv_profile"), _araises))

    assert isinstance(answer, ToolMessage)
    assert answer.status == "error"


def test_the_async_path_leaves_a_built_in_alone_too():
    guard = OfferedToolFailureMiddleware(frozenset({"csv_profile"}))

    with pytest.raises(FileNotFoundError):
        asyncio.run(guard.awrap_tool_call(_Request("read_file"), _araises))


# -- and that the build actually installs it -------------------------------

A_TOOL = '''
from langchain_core.tools import tool


@tool
def probe_one(text: str) -> str:
    """A tool that exists so the build has an offered name to guard."""
    return text


TOOLS = [probe_one]
'''


def _guard_in(built) -> GuardedCallsMiddleware | None:
    """The guard the build handed to `create_deep_agent`, if it added one."""
    for entry in built.middleware or ():
        if isinstance(entry, GuardedCallsMiddleware):
            return entry
    return None


def test_the_build_guards_the_names_the_workspace_defined(cfg, session_dir):
    """A middleware nobody installs guards nothing."""
    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "probe.py").write_text(A_TOOL, encoding="utf-8")

    built = build_agent(cfg, backend=backend_at(cfg, session_dir), model=_a_model())

    guard = _guard_in(built)
    assert guard is not None, "the build installed no GuardedCallsMiddleware"
    assert "probe_one" in guard.names


def test_a_workspace_with_no_tools_hands_back_no_exception_of_its_own(cfg, session_dir):
    """The guard handed names when the definitions offered no tools, so some other tool's
    exception became a failed result rather than its own report."""
    from kingfisher.core.agent.assembly import build_agent

    built = build_agent(cfg, backend=backend_at(cfg, session_dir), model=_a_model())

    guard = _guard_in(built)
    assert guard is not None
    assert guard.names == frozenset()
    # And the layer that answers for an offered tool's own failure is not installed at
    # all: there is no name for it to answer for, and a middleware that cannot act reads
    # as one that does.
    assert not [m for m in built.middleware or () if isinstance(m, OfferedToolFailureMiddleware)]


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
    raise FileNotFoundError("/inputs/nothing-here.csv")


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
    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import FakeToolCallingModel, tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=_calls("always_fails")),
    ).graph


def test_a_failing_offered_tool_does_not_stop_a_run(cfg, session_dir):
    """The claim, through an assembled graph rather than the middleware alone."""
    out = _graph_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}
    )

    failures = [
        m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert failures, "the tool's exception never reached the model"
    assert "FileNotFoundError" in failures[0].content
    assert "/inputs/nothing-here.csv" in failures[0].content


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


def _subagent_with_a_failing_tool(cfg, session_dir):
    """The same graph, one level down: a subagent holding the failing tool."""
    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(CALLS_IT, encoding="utf-8")

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=_calls("always_fails")),
        capabilities=Capabilities(subagents=("helper",)),
    ).graph
    return _subagent_graphs(graph)["helper"]


def test_a_failing_offered_tool_does_not_stop_a_subagent_either(cfg, session_dir):
    """The gap the parent's guard left, and the reason it matters more now."""
    out = _subagent_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    failures = [
        m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"
    ]
    assert failures, "the tool's exception never reached the subagent's model"
    assert "FileNotFoundError" in failures[0].content


def test_the_subagent_carries_on_to_an_answer(cfg, session_dir):
    """Surviving is not enough: the subagent has to finish, or its caller gets nothing
    back and the run is dead a level higher instead.
    """
    out = _subagent_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    assert out["messages"][-1].content == "done"


NESTS = """name: helper
description: Consults another, which calls the tool that fails.
builtin_tools: ["*"]
tools: ["*"]
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


def test_a_helper_below_a_subagent_is_guarded_too(cfg, session_dir):
    """Worth its own test rather than assumed from the one above.

    A helper is built by a different call than a subagent the request activated, so
    reaching for the top-level instance instead is an easy mistake that passes -- a
    standalone subagent has neither the helper nor the bug.
    """
    from kingfisher.core.access.capabilities import Capabilities
    from kingfisher.core.agent.assembly import build_agent
    from tests.conftest import FakeToolCallingModel, subagents_dir, tools_dir
    from tests.unit.test_delegation_ceiling import _subagent_graphs

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "always_fails.py").write_text(ALWAYS_FAILS, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(NESTS, encoding="utf-8")
    (subagents_dir(cfg) / "deeper.yaml").write_text(DEEPER, encoding="utf-8")

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=_calls("always_fails")),
        capabilities=Capabilities(subagents=("helper", "deeper")),
    ).graph
    nested = _subagent_graphs(_subagent_graphs(graph)["helper"])["deeper"]

    out = nested.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 8}
    )

    assert [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"]
    assert out["messages"][-1].content == "done"


def test_a_converted_failure_still_reaches_the_run_log(cfg, session_dir):
    """Catching it for the model does not hide it from whoever reads afterwards."""
    from kingfisher.core.session.runlog import RunLogger
    from tests.conftest import RecordedEvents

    sink = RecordedEvents()
    out = _graph_with_a_failing_tool(cfg, session_dir).invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={
            "callbacks": [
                RunLogger(sink, model="m", endpoint="e", session_id="s", turn_id="t")
            ]
        },
    )

    assert [m for m in out["messages"] if isinstance(m, ToolMessage) and m.status == "error"], (
        "the model stopped being told, which is what this guard is for"
    )
    assert sink.named("tool_error"), "the run log lost its only record of a tool that failed"


# -- through the stack, where the guards meet -------------------------------
#
# Driven through what `standing_middleware` builds, not one guard alone: the translator
# caught every error raised below it, and no test of a single class could see that.


def _through(stack, call: dict, tool, way: str = "sync"):
    """What the model reads back for `call`, through each tool hook of `stack` in the
    order langchain composes them, the first outermost."""
    from functools import partial

    from langchain.agents.middleware import AgentMiddleware, ToolCallRequest
    from langchain.tools import ToolRuntime
    from langchain_core.messages.tool import tool_call

    state = {"messages": []}
    runtime = ToolRuntime(
        state=state,
        context=None,
        config={},
        stream_writer=lambda _chunk: None,
        tool_call_id=call["id"],
        store=None,
    )
    request = ToolCallRequest(
        tool_call=tool_call(name=call["name"], args=call["args"], id=call["id"]),
        tool=tool,
        state=state,
        runtime=runtime,
    )
    hooked = [
        one for one in stack if type(one).wrap_tool_call is not AgentMiddleware.wrap_tool_call
    ]
    if way == "sync":
        handler = partial(_ran, tool)
        for one in reversed(hooked):
            handler = partial(one.wrap_tool_call, handler=handler)
        return handler(request)
    ahandler = partial(_aran, tool)
    for one in reversed(hooked):
        ahandler = partial(one.awrap_tool_call, handler=ahandler)
    return asyncio.run(ahandler(request))


def _ran(tool, request):
    return tool.invoke({"type": "tool_call", **request.tool_call})


async def _aran(tool, request):
    return await tool.ainvoke({"type": "tool_call", **request.tool_call})


@pytest.fixture
def paths(cfg, session_dir):
    from tests.conftest import paths_in

    return paths_in(cfg, session_dir)


@pytest.fixture
def stack(cfg, session_dir, paths):
    """The agent's standing middleware on a graph holding one offered tool, `probe`."""
    from kingfisher.core.agent.middlewares.standing import standing_middleware
    from kingfisher.core.agent.middlewares.step_budget import StepBudget

    return standing_middleware(
        "agent",
        name="",
        names=frozenset({"probe"}),
        paths=paths,
        layout=cfg.layout,
        backend=backend_at(cfg, session_dir),
        budget=StepBudget(cfg.recursion_limit),
        execution_timeout_s=cfg.execution_timeout_s,
        models=cfg.models,
        trigger=170_000,
        **STANDING_UNWIRED,
    )


def _raising(exc: BaseException):
    """An offered tool that raises `exc` whatever it is asked."""
    from langchain_core.tools import tool

    @tool
    def probe(note: str) -> str:
        """Raise what the test hands it."""
        raise exc

    return probe


def test_a_built_in_refusing_a_host_path_reads_as_written_beside_an_offered_tool(stack):
    """On a graph holding an offered tool, the translator caught `read_file`'s refusal
    first and appended its own rule, after a doubled full stop."""
    refused = _raising(HostPathError("'/Users/me/x.csv' is a host path. Use '/inputs/x.csv'."))
    call = {"name": "read_file", "args": {"note": "x"}, "id": "c1"}

    answer = _through(stack, call, refused)

    assert answer.content == "Error: '/Users/me/x.csv' is a host path. Use '/inputs/x.csv'."
    assert answer.status == "error"


class _FallsOver(AgentMiddleware):
    """A deployment's middleware that raises on every tool call, as a broken one does."""

    name = "FallsOver"

    def wrap_tool_call(self, request, handler):
        msg = "the deployment's middleware fell over"
        raise RuntimeError(msg)

    async def awrap_tool_call(self, request, handler):
        msg = "the deployment's middleware fell over"
        raise RuntimeError(msg)


def _stack_around(cfg, session_dir, paths, inner):
    """The agent's standing middleware with `inner` where a deployment's goes."""
    from kingfisher.core.agent.middlewares.standing import standing_middleware
    from kingfisher.core.agent.middlewares.step_budget import StepBudget

    return standing_middleware(
        "agent",
        name="",
        names=frozenset({"probe"}),
        paths=paths,
        layout=cfg.layout,
        backend=backend_at(cfg, session_dir),
        budget=StepBudget(cfg.recursion_limit),
        execution_timeout_s=cfg.execution_timeout_s,
        models=cfg.models,
        trigger=170_000,
        inner=inner,
        **STANDING_UNWIRED,
    )


def test_a_deployments_middleware_that_falls_over_ends_the_turn_on_either_kind_of_tool(
    cfg, session_dir, paths
):
    """It ended the turn on a built-in and came back as a failed result on an offered
    tool -- naming the tool, so a deployment's own fault read as the tool's and the turn
    carried on with the deployment's rule not applied. One answer for both, and it is the
    loud one: a middleware a deployment registered has no way to report a fault of its own
    to the model, and a rule that did not run is not a rule.
    """
    stack = _stack_around(cfg, session_dir, paths, [_FallsOver()])
    probe = _raising(FileNotFoundError("never reached"))

    for name in ("probe", "read_file"):
        with pytest.raises(RuntimeError, match="fell over"):
            _through(stack, {"name": name, "args": {"note": "x"}, "id": "c1"}, probe)


def test_the_tools_own_failure_is_still_answered_for_the_tool_under_that_middleware(
    cfg, session_dir, paths
):
    """The control for the rule above: with a deployment's middleware installed and the
    tool itself failing, the model still reads a failed result rather than losing the turn
    -- otherwise that rule would pass against a stack that had stopped converting
    anything.
    """

    class _Quiet(AgentMiddleware):
        name = "Quiet"

    stack = _stack_around(cfg, session_dir, paths, [_Quiet()])
    probe = _raising(FileNotFoundError(MISSING))

    answer = _through(stack, {"name": "probe", "args": {"note": "x"}, "id": "c1"}, probe)

    assert answer.status == "error"
    assert MISSING in answer.content


#: What the offered tool raises in each case, and the argument it is called with.
FAILURES = {
    "its-own-error": (FileNotFoundError("/inputs/orders.csv"), "x"),
    "its-own-host-path": (HostPathError("/etc/hosts"), "x"),
    "its-own-tool-exception": (ToolException("name a file under /inputs/, not the folder"), "x"),
    "a-refused-path": (RuntimeError("never reached"), "/proc/self/environ"),
}


@pytest.mark.parametrize("case", list(FAILURES))
def test_an_offered_tool_fails_alike_through_the_stack_and_on_a_compiled_subagent(
    stack, paths, way, case
):
    """`SessionPaths` says the two must answer identically, and through the stack a tool's
    own host-path refusal came back as the translator's rule and its `ToolException` with
    a type in front."""
    from kingfisher.core.agent.tool_guards import guarded_tools

    raised, note = FAILURES[case]
    probe = _raising(raised)
    call = {"name": "probe", "args": {"note": note}, "id": "c1"}

    middleware = _through(stack, call, probe, way)
    guarded = guarded_tools([probe], paths)[0]
    called = {"type": "tool_call", **call}
    compiled = guarded.invoke(called) if way == "sync" else asyncio.run(guarded.ainvoke(called))

    assert (middleware.content, middleware.status) == (compiled.content, compiled.status)
    assert middleware.status == "error"


def test_a_split_grep_whose_path_is_refused_fails_once(stack):
    """Moved inside `BuiltinToolsMiddleware`, the guard would answer each part of `alpha|beta`
    apart, and the model would read one refusal under "matches for `alpha`" and another
    under "matches for `beta`"."""
    from langchain_core.tools import tool

    said = "'/Users/me/notes' is a host path. Use '/inputs/notes'."

    @tool
    def grep(pattern: str, path: str) -> str:
        """Stands in for deepagents' own, refusing the path as the backend does."""
        raise HostPathError(said)

    searched = {"pattern": "alpha|beta", "path": "/Users/me/notes"}
    call = {"name": "grep", "args": searched, "id": "c1"}

    answer = _through(stack, call, grep)

    assert answer.content == f"Error: {said}"
    assert answer.status == "error"

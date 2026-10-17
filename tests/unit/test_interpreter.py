"""The JavaScript sandbox: a surface `execute` can never be."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import Kingfisher
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.interpreter import release_interpreter
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    dispatched,
    start,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted
from tests.unit.test_subagent_helpers import _helper, _tools_of


def _model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def test_it_is_off_unless_a_deployment_wires_it(cfg, session_dir):
    """A second execution surface, and a beta dependency, should not arrive because
    someone upgraded.
    """
    graph = build_agent(cfg, backend=backend_at(cfg, session_dir), model=_model()).graph
    assert "eval" not in dispatched(graph)


def test_wiring_it_adds_one_tool(cfg, session_dir):
    wired = replace(cfg, interpreter_enabled=True)
    graph = build_agent(
        wired, backend=backend_at(wired, session_dir), model=_model()
    ).graph
    assert "eval" in dispatched(graph)


def test_eval_is_an_ordinary_tool_a_request_may_withhold(cfg, session_dir):
    """No new axis."""
    wired = replace(cfg, interpreter_enabled=True)

    without = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(builtin_tools=("read_file",)),
    ).graph
    # Registered either way -- the allowlist refuses at call time, it does not
    # unregister. What matters is that the name is known to the validator.
    assert "eval" in dispatched(without)


def test_a_request_that_withheld_the_shell_cannot_reach_it_from_code(cfg, session_dir):
    """The whole reason for adopting this."""
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(builtin_tools=("read_file", "eval")),
    )

    interpreter = _interpreter_in(built)
    assert interpreter is not None, "the interpreter was not wired"
    assert "execute" not in _ptc(interpreter)
    assert "read_file" in _ptc(interpreter)


def test_an_unrestricted_request_gets_no_tool_namespace(cfg, session_dir):
    """`None` is the library's *off*, not an allowlist of everything: an unrestricted
    request reaches no tool from code, and a list here would widen that unannounced.
    """
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired, backend=backend_at(wired, session_dir), model=_model()
    )

    assert _ptc(_interpreter_in(built)) is None


def _interpreter_in(built):
    for middleware in built.middleware or ():
        if type(middleware).__name__ == "CodeInterpreterMiddleware":
            return middleware
    return None


def _ptc(interpreter):
    for attribute in ("_ptc", "ptc"):
        if hasattr(interpreter, attribute):
            return getattr(interpreter, attribute)
    pytest.fail("could not read the interpreter's tool allowlist")
    return None


def test_withholding_task_also_stops_dispatch_from_code(cfg, session_dir):
    """`task()` is a top-level global in the REPL, not a `tools.*` entry, so the tool
    allowlist does not reach it.
    """
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "read_file")),  # no task
    )

    interpreter = _interpreter_in(built)
    assert _dispatch_enabled(interpreter) is False


def test_granting_task_allows_dispatch_from_code(cfg, session_dir):
    """The negative control: without it the test above would pass just as well if
    dispatch were disabled for everyone.
    """
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "task")),
    )

    assert _dispatch_enabled(_interpreter_in(built)) is True


def test_task_is_never_offered_through_the_tool_namespace(cfg, session_dir, monkeypatch):
    """The library refuses it: `task()` is the global, and routing it through `tools.*`
    as well would give two dispatch paths, the second losing `responseSchema`.
    """
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "task", "read_file")),
    )

    assert "task" not in _ptc(_interpreter_in(built))
    assert "read_file" in _ptc(_interpreter_in(built))


def _dispatch_enabled(interpreter):
    for attribute in ("_subagents", "subagents"):
        if hasattr(interpreter, attribute):
            return getattr(interpreter, attribute)
    pytest.fail("could not read whether code-side dispatch is enabled")
    return None


def test_the_vm_image_is_dropped_rather_than_checkpointed(cfg, session_dir):
    """The library serialises the whole QuickJS heap into the checkpoint at the end of
    every turn -- measured at a constant 1,280KB, written whether or not `eval` was
    ever called.
    """
    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired, backend=backend_at(wired, session_dir), model=_model()
    )

    interpreter = _interpreter_in(built)
    cap = next(
        (getattr(interpreter, a) for a in ("_max_snapshot_bytes", "max_snapshot_bytes")
         if hasattr(interpreter, a)),
        None,
    )
    assert cap is not None, "could not read the interpreter's snapshot cap"
    assert cap < 1_280_000, (
        f"the cap is {cap}, at or above the 1,280KB image, so every turn stores one"
    )


def test_the_cap_is_not_the_librarys_default(cfg, session_dir):
    """Left unset the cap becomes `memory_limit` -- 64MB, far above any real image, so
    nothing is ever dropped.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    wired = replace(cfg, interpreter_enabled=True)
    built = build_agent(
        wired, backend=backend_at(wired, session_dir), model=_model()
    )

    ours = _interpreter_in(built)._max_snapshot_bytes
    theirs = CodeInterpreterMiddleware()._max_snapshot_bytes

    assert ours < theirs, f"cap {ours} is no tighter than the library default {theirs}"


# -- a call from code keeps the guards a direct call has ----------------------
#
# Driven rather than inspected: the bridge dispatches past `ToolNode`, so what decides
# these is what the call meets when it runs, and an assertion on `ptc` would pass
# against a wrapper that guards nothing.

#: The shapes a deployment's tools come in: one taking `path`, one taking a file under
#: another name, one that raises, and one reading the turn's backend.
CODE_TOOLS = '''
from pathlib import Path

from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from kingfisher import ToolContext


@tool
def peek(path: str) -> str:
    """Read a file. `path` is the same virtual path the file tools take."""
    return Path(path).read_text()


@tool
def peek_any(input_file: str) -> str:
    """Read whatever file it is handed."""
    return Path(input_file).read_text()


@tool
def always_fails(anything: str) -> str:
    """Raise, every time."""
    raise FileNotFoundError("/inputs/nothing-here.csv")


def backend_read(file_path: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Read a file through the session's backend."""
    (got,) = runtime.context.backend.download_files([file_path])
    return got.content.decode()


TOOLS = [peek, peek_any, always_fails, backend_read]
'''

THEIRS = "IN-ANOTHER-SESSION"
MINE = "MINE-IN-THIS-SESSION"

NARROWED = Capabilities(
    builtin_tools=("eval", "read_file", "write_file"),
    tools=("peek", "peek_any", "always_fails", "backend_read"),
)


def _with_code_tools(cfg, session_dir) -> str:
    """A workspace holding `CODE_TOOLS`, a file in this session and one in another."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "code_tools.py").write_text(CODE_TOOLS, encoding="utf-8")
    other = cfg.workspace / "sessions" / "other-session" / "inputs"
    other.mkdir(parents=True, exist_ok=True)
    (other / "theirs.txt").write_text(THEIRS, encoding="utf-8")
    (session_dir / "inputs" / "mine.txt").write_text(MINE, encoding="utf-8")
    return str(other / "theirs.txt")


def _from_code(cfg, session_dir, code, *, capabilities=NARROWED, agent=None):
    """What one `eval` of `code` answered, and the turn's last word."""
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "eval", "args": {"code": code}, "id": "ev"}],
                ),
                AIMessage(content="done"),
            ]
        ),
        capabilities=capabilities,
        agent=agent,
        # A gate needs somewhere to pause into, and the agents here that carry one
        # are refused without it.
        checkpointer=InMemorySaver() if agent is not None else None,
    )
    try:
        out = built.graph.invoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"configurable": {"thread_id": "from-code"}},
            context=built.context,
        )
    finally:
        # A turn that raised skipped `after_agent`, and a runtime left open hangs the
        # process at exit instead of failing the test that opened it.
        release_interpreter(cfg, built.graph)
    (answered,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    return str(answered.content), str(out["messages"][-1].content)


@pytest.mark.parametrize(
    ("call", "refused_with"),
    [
        ("tools.peekAny({input_file: OTHER})", "is a host path"),
        ("tools.peekAny({input_file: '/etc/hosts'})", "is a host path"),
        # Under `path` it is translated rather than refused, so it lands in this
        # session, where no such file is.
        ("tools.peek({path: OTHER})", "FileNotFoundError"),
    ],
)
def test_a_workspace_tool_called_from_code_cannot_read_outside_the_session(
    cfg, session_dir, call, refused_with
):
    """From `eval` a tool reading its own argument returned another session's file and
    the host's `/etc/hosts`, since `WorkspaceToolPathsMiddleware` never saw the call.
    """
    wired = replace(cfg, interpreter_enabled=True)
    other = _with_code_tools(wired, session_dir)
    program = call.replace("OTHER", json.dumps(other))

    answered, _ = _from_code(wired, session_dir, f"(async () => await {program})()")

    assert THEIRS not in answered
    assert refused_with in answered


def test_a_path_handed_to_a_tool_from_code_is_this_sessions(cfg, session_dir):
    """`/inputs/mine.txt` reached the tool from `eval` as written, so the tool looked
    for it at the root of the host and raised.
    """
    wired = replace(cfg, interpreter_enabled=True)
    _with_code_tools(wired, session_dir)

    answered, _ = _from_code(
        wired, session_dir, "(async () => await tools.peek({path: '/inputs/mine.txt'}))()"
    )

    assert MINE in answered


def test_a_tool_that_raises_under_code_fails_the_call_and_not_the_turn(cfg, session_dir):
    """Uncaught in the program, a workspace tool's `FileNotFoundError` ended the turn:
    `ToolFailureMiddleware` wraps a tool call, and a call from code is not one.
    """
    wired = replace(cfg, interpreter_enabled=True)
    _with_code_tools(wired, session_dir)

    answered, last = _from_code(
        wired, session_dir, "(async () => await tools.alwaysFails({anything: 'x'}))()"
    )

    assert "FileNotFoundError" in answered
    assert last == "done"


def test_a_tool_taking_the_turns_backend_still_gets_it_from_code(cfg, session_dir):
    """The bridge injects `runtime` only where the tool's schema declares it, so a
    wrapper that hid the declaration would call the tool without its backend.
    """
    wired = replace(cfg, interpreter_enabled=True)
    _with_code_tools(wired, session_dir)

    answered, _ = _from_code(
        wired,
        session_dir,
        "(async () => await tools.backendRead({file_path: '/inputs/mine.txt'}))()",
    )

    assert MINE in answered


@pytest.mark.parametrize(
    ("gated", "spelled"),
    # A built-in, which reaches the sandbox by name, and a workspace tool, which
    # reaches it as an object: two lists, and a gate has to empty both.
    [("write_file", "writeFile"), ("peek", "peek")],
)
def test_a_gated_tool_is_out_of_reach_from_code(cfg, session_dir, gated, spelled):
    """Under `interrupt_on: [write_file]` a `tools.writeFile` from `eval` wrote with no
    pause: the gate reads the model's tool calls, and a call from code is never one.
    """
    wired = replace(cfg, interpreter_enabled=True)
    _with_code_tools(wired, session_dir)
    an_agent(wired, "gated", interrupt_on=f"[{gated}]", **EVERY_TOOL)
    an_agent(wired, "ungated", interrupt_on="[execute]", **EVERY_TOOL)
    agents = LocalAgentRepository(wired.catalogue_roots["agents"]).specs
    program = f"(async () => typeof tools.{spelled})()"

    gated_answer, _ = _from_code(wired, session_dir, program, agent=agents["gated"])
    ungated_answer, _ = _from_code(wired, session_dir, program, agent=agents["ungated"])

    assert "undefined" in gated_answer
    # The control: without it this passes for a tool the request never granted.
    assert "function" in ungated_answer


def test_a_gate_on_task_takes_task_out_of_code_on_an_unrestricted_request(
    cfg, session_dir
):
    """`task()` from `eval` ran the subagent with no pause under `interrupt_on: [task]`:
    an unrestricted request never re-wired the sandbox, so it never heard of the gate.
    """
    wired = replace(cfg, interpreter_enabled=True)
    subagents_dir(wired).mkdir(parents=True, exist_ok=True)
    (subagents_dir(wired) / "kept.yaml").write_text(
        "name: kept\ndescription: Answers.\nsystem_prompt: |\n  You answer.\n",
        encoding="utf-8",
    )
    an_agent(wired, "gated", interrupt_on="[task]", subagents="[kept]", **EVERY_TOOL)
    an_agent(wired, "ungated", interrupt_on="[execute]", subagents="[kept]", **EVERY_TOOL)
    agents = LocalAgentRepository(wired.catalogue_roots["agents"]).specs
    program = "(async () => typeof task)()"

    gated_answer, _ = _from_code(
        wired, session_dir, program, capabilities=Capabilities(), agent=agents["gated"]
    )
    ungated_answer, _ = _from_code(
        wired, session_dir, program, capabilities=Capabilities(), agent=agents["ungated"]
    )

    assert "undefined" in gated_answer
    assert "function" in ungated_answer


def test_a_nested_helper_is_handed_no_sandbox(cfg, session_dir):
    """A helper was handed the probe graph's `eval`, built before the grant and never
    narrowed: its `task()` reached a subagent its own allowlist refused.
    """
    wired = replace(cfg, interpreter_enabled=True)
    subagents_dir(wired).mkdir(parents=True, exist_ok=True)
    (subagents_dir(wired) / "outer.yaml").write_text(
        'name: outer\ndescription: Consults.\nbuiltin_tools: ["*"]\nsubagents: [helper]\n'
        "system_prompt: |\n  You ask.\n",
        encoding="utf-8",
    )
    # `["*"]`, which is every built-in and `eval` among them, so the helper's own grant
    # is not what keeps it away. Listing it is refused at load.
    (subagents_dir(wired) / "helper.yaml").write_text(
        'name: helper\ndescription: Computes.\nbuiltin_tools: ["*"]\n'
        "system_prompt: |\n  You compute.\n",
        encoding="utf-8",
    )

    graph = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=_model(),
        capabilities=Capabilities(
            builtin_tools=("eval", "task", "read_file"), tools=(), subagents=("outer", "helper")
        ),
    ).graph

    assert "eval" in dispatched(graph), "the sandbox was not wired -- this proves nothing"
    assert "eval" not in _tools_of(_helper(graph, "outer", "helper"))


# -- giving the runtime back ----------------------------------------------


def _quickjs_workers() -> set[str]:
    """The sandbox's own OS threads, by the name `quickjs_rs` gives them."""
    import threading

    return {t.name for t in threading.enumerate() if t.name.startswith("quickjs-worker")}


def _force_close(graph) -> None:
    """Close the runtime whatever `release_interpreter` did, as a safety net.

    Not a second implementation to keep in step -- a net under the test below, and it
    exists because of how that test fails. A runtime left open does not fail the
    process, it hangs it at exit, so without this a regression arrives as a CI job
    that times out with no output rather than as a red assertion. Measured by
    removing the fix: the assertion went red and pytest then sat there until it was
    killed.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    for node in graph.nodes.values():
        owner = getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        if isinstance(owner, CodeInterpreterMiddleware):
            owner._registry.close()
            return


def test_the_runtime_is_given_back_when_a_turn_ends_by_exception(cfg, session_dir):
    """The interpreter's teardown is `after_agent`, and langgraph does not run
    `after_agent` when the graph raises.

    The `eval` is why this is driven rather than inspected: a turn that never evaluated
    has no runtime to leave behind, so a version of this test without it passes against
    the bug.

    That is not a leaked handle. `quickjs_rs` pins its Runtime to one worker thread
    because it is `!Send`, and closing it means collecting on *that* thread; a sweep
    from anywhere else hits the drop check. The only sweep left is `Py_FinalizeEx`,
    on the main thread, and there the finalizer deadlocks rather than panicking -- so
    the process does not crash, it stops. Measured on a real run that hit
    `recursion_limit`: traceback printed, report already written to disk, and the
    process still sitting there minutes later.
    """
    from langgraph.errors import GraphRecursionError

    from kingfisher.core.agent.interpreter import release_interpreter

    wired = replace(cfg, interpreter_enabled=True)
    model = FakeToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"name": "eval", "args": {"code": "1+1"}, "id": "e"}],
            ),
            *[
                AIMessage(
                    content="",
                    tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"c{i}"}],
                )
                for i in range(20)
            ],
        ]
    )
    graph = build_agent(wired, backend=backend_at(wired, session_dir), model=model).graph

    with pytest.raises(GraphRecursionError):
        graph.invoke(
            {"messages": [{"role": "user", "content": "go"}]},
            config={"configurable": {"thread_id": "release-probe"}, "recursion_limit": 8},
        )

    assert _quickjs_workers(), "the turn started no runtime -- this proves nothing"

    try:
        release_interpreter(wired, graph)
        remaining = _quickjs_workers()
    finally:
        _force_close(graph)

    assert not remaining, "the sandbox outlived the turn that opened it"


def test_a_real_build_is_releasable(cfg, session_dir):
    """`release_interpreter` finds the middleware by walking the compiled graph's nodes,
    which is not a published shape -- the same unpublished shape `registered_tools`
    reads, pinned the same way.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    wired = replace(cfg, interpreter_enabled=True)
    graph = build_agent(
        wired, backend=backend_at(wired, session_dir), model=_model()
    ).graph

    owners = [
        getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        for node in graph.nodes.values()
    ]

    assert any(isinstance(o, CodeInterpreterMiddleware) for o in owners), (
        "the interpreter is no longer reachable from the graph, so nothing closes it"
    )


def test_releasing_costs_nothing_when_the_interpreter_is_off(cfg, session_dir):
    """It runs in the teardown of every turn, including the ones on a deployment that
    never wired a sandbox.
    """
    graph = build_agent(cfg, backend=backend_at(cfg, session_dir), model=_model()).graph

    release_interpreter(cfg, graph)

    assert not _quickjs_workers()


def _close_every_interpreter() -> None:
    """Close whatever runtime is still open, wherever its middleware is held.

    The net under the test below, for the reason `_force_close` is one: a runtime left
    open hangs the process at exit. That test is about a runtime held by no graph the
    turn returns, so there is no graph to walk and the heap is searched instead.
    """
    import gc
    from contextlib import suppress

    from langchain_quickjs import CodeInterpreterMiddleware

    for held in gc.get_objects():
        if isinstance(held, CodeInterpreterMiddleware):
            with suppress(Exception):
                held._registry.close()


def test_no_sandbox_outlives_a_turn_in_which_a_helper_reached_for_it(scripted):
    """A nested helper's `eval` came off the probe graph, whose runtime
    `release_interpreter` never sees, so a `quickjs-worker` outlived every such turn.

    Driven through the service, because the teardown under test is the turn's own.
    The agent calls `eval` as well, so the turn opens a runtime whatever the helper
    holds -- without it this passes against a sandbox that never started.
    """
    wired = replace(scripted, interpreter_enabled=True)
    subagents_dir(wired).mkdir(parents=True, exist_ok=True)
    (subagents_dir(wired) / "outer.yaml").write_text(
        'name: outer\ndescription: Consults.\nbuiltin_tools: ["*"]\nsubagents: [inner]\n'
        "system_prompt: |\n  You ask.\n",
        encoding="utf-8",
    )
    (subagents_dir(wired) / "inner.yaml").write_text(
        'name: inner\ndescription: Computes.\nbuiltin_tools: ["*"]\n'
        "system_prompt: |\n  You compute.\n",
        encoding="utf-8",
    )
    an_agent(wired, "only", subagents="[outer, inner]", **EVERY_TOOL)
    start(wired, "s")

    def calls(name, args, call_id):
        return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])

    Scripted.script.extend(
        [
            calls("eval", {"code": "1 + 1"}, "agent-eval"),
            calls("task", {"description": "go", "subagent_type": "outer"}, "to-outer"),
            calls("task", {"description": "go", "subagent_type": "inner"}, "to-inner"),
            calls("eval", {"code": "1 + 1"}, "helper-eval"),
            AIMessage(content="INNER-DONE"),
            AIMessage(content="OUTER-DONE"),
            AIMessage(content="done"),
        ]
    )
    try:
        events = list(
            Kingfisher(wired, sessions=LocalSessions()).stream(
                Request(
                    "go",
                    agent="only",
                    session_id="s",
                    capabilities=Capabilities(
                        builtin_tools=("eval", "task", "read_file"), tools=()
                    ),
                )
            )
        )
        remaining = _quickjs_workers()
    finally:
        _close_every_interpreter()

    evaluated = [e.text for e in events if e.kind == "tool_result" and e.tool == "eval"]
    assert "<result>2</result>\n[A-0001]" in evaluated, "the agent's own eval never ran"
    assert not remaining, "a sandbox outlived the turn that opened it"

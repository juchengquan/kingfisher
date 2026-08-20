"""The JavaScript sandbox: a surface `execute` can never be.

`execute` is a real shell with the whole host behind it -- every safety note in
this codebase ends by saying so. The interpreter is the opposite: capped
memory, capped time, no filesystem, no network, and reachable tools limited to
what the request granted. It is wired for that property, so that is what these
check.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.harness.agent import build_agent, release_interpreter
from tests.conftest import FakeToolCallingModel, capture_build, dispatched


def _model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def test_it_is_off_unless_a_deployment_wires_it(cfg, session_dir):
    """A second execution surface, and a beta dependency, should not arrive
    because someone upgraded."""
    graph = build_agent(cfg, session_dir=session_dir, model=_model())
    assert "eval" not in dispatched(graph)


def test_wiring_it_adds_one_tool(cfg, session_dir):
    graph = build_agent(
        replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model()
    )
    assert "eval" in dispatched(graph)


def test_eval_is_an_ordinary_tool_a_request_may_withhold(cfg, session_dir):
    """No new axis. It is granted and withheld through `Capabilities.tools`
    like anything else, which also means the tool-name validator covers it."""
    wired = replace(cfg, interpreter_enabled=True)

    without = build_agent(
        wired,
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(builtin_tools=("read_file",)),
    )
    # Registered either way -- the allowlist refuses at call time, it does not
    # unregister. What matters is that the name is known to the validator.
    assert "eval" in dispatched(without)


def test_a_request_that_withheld_the_shell_cannot_reach_it_from_code(cfg, session_dir, monkeypatch):
    """The whole reason for adopting this. `ptc` is the request's own grant,
    so a caller that withheld `execute` cannot call it from inside the
    sandbox either."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(builtin_tools=("read_file", "eval")),
    )

    interpreter = _interpreter_in(captured)
    assert interpreter is not None, "the interpreter was not wired"
    assert "execute" not in _ptc(interpreter)
    assert "read_file" in _ptc(interpreter)


def test_an_unrestricted_request_gets_no_allowlist(cfg, session_dir, monkeypatch):
    """`None`, not an empty tuple. The library reads `None` as "no allowlist";
    an empty tuple would mean the opposite of what the caller asked for.

    Consistent with the rest: restrictions attach to narrowing, and a caller
    that narrowed nothing has nothing to escape from.
    """
    captured = capture_build(monkeypatch)
    build_agent(replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model())

    assert _ptc(_interpreter_in(captured)) is None


def _interpreter_in(captured):
    for middleware in captured.get("middleware", ()):
        if type(middleware).__name__ == "CodeInterpreterMiddleware":
            return middleware
    return None


def _ptc(interpreter):
    for attribute in ("_ptc", "ptc"):
        if hasattr(interpreter, attribute):
            return getattr(interpreter, attribute)
    pytest.fail("could not read the interpreter's tool allowlist")
    return None


def test_withholding_task_also_stops_dispatch_from_code(cfg, session_dir, monkeypatch):
    """`task()` is a top-level global in the REPL, not a `tools.*` entry, so
    the tool allowlist does not reach it.

    Left at the library's default this would let a request that withheld `task`
    delegate anyway from inside the sandbox -- a hole of exactly the shape the
    delegate ceiling exists to close, arrived at by a different door.
    """
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "read_file")),  # no task
    )

    interpreter = _interpreter_in(captured)
    assert _dispatch_enabled(interpreter) is False


def test_granting_task_allows_dispatch_from_code(cfg, session_dir, monkeypatch):
    """The negative control: without it the test above would pass just as well
    if dispatch were disabled for everyone."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "task")),
    )

    assert _dispatch_enabled(_interpreter_in(captured)) is True


def test_task_is_never_offered_through_the_tool_namespace(cfg, session_dir, monkeypatch):
    """The library refuses it: `task()` is the global, and routing it through
    `tools.*` as well would give two dispatch paths, the second losing
    `responseSchema`. Undocumented, and only a live run found it."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(builtin_tools=("eval", "task", "read_file")),
    )

    assert "task" not in _ptc(_interpreter_in(captured))
    assert "read_file" in _ptc(_interpreter_in(captured))


def _dispatch_enabled(interpreter):
    for attribute in ("_subagents", "subagents"):
        if hasattr(interpreter, attribute):
            return getattr(interpreter, attribute)
    pytest.fail("could not read whether code-side dispatch is enabled")
    return None


def test_the_vm_image_is_dropped_rather_than_checkpointed(cfg, session_dir, monkeypatch):
    """The library serialises the whole QuickJS heap into the checkpoint at the
    end of every turn -- measured at a constant 1,280KB, written whether or not
    `eval` was ever called. One observed run called it zero times out of
    forty-five tool calls and still paid it. Capping took a workspace's thread
    database from 2.94MB to 0.31MB across two turns.

    Asserted against the cap the middleware is holding rather than the argument
    passed, so the check survives the library renaming its keyword.
    """
    captured = capture_build(monkeypatch)
    build_agent(replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model())

    interpreter = _interpreter_in(captured)
    cap = next(
        (getattr(interpreter, a) for a in ("_max_snapshot_bytes", "max_snapshot_bytes")
         if hasattr(interpreter, a)),
        None,
    )
    assert cap is not None, "could not read the interpreter's snapshot cap"
    assert cap < 1_280_000, (
        f"the cap is {cap}, at or above the 1,280KB image, so every turn stores one"
    )


def test_the_cap_is_not_the_librarys_default(cfg, session_dir, monkeypatch):
    """Left unset the cap becomes `memory_limit` -- 64MB, far above any real
    image, so nothing is ever dropped. The point of setting it is that the
    default keeps everything.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    captured = capture_build(monkeypatch)
    build_agent(replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model())

    ours = _interpreter_in(captured)._max_snapshot_bytes
    theirs = CodeInterpreterMiddleware()._max_snapshot_bytes

    assert ours < theirs, f"cap {ours} is no tighter than the library default {theirs}"


# -- giving the runtime back ----------------------------------------------


def _quickjs_workers() -> set[str]:
    """The sandbox's own OS threads, by the name `quickjs_rs` gives them."""
    import threading

    return {t.name for t in threading.enumerate() if t.name.startswith("quickjs-worker")}


def _force_close(graph) -> None:
    """Close the runtime whatever `release_interpreter` did, as a safety net.

    Not a second implementation to keep in step -- a net under the test below,
    and it exists because of how that test fails. A runtime left open does not
    fail the process, it hangs it at exit, so without this a regression arrives
    as a CI job that times out with no output rather than as a red assertion.
    Measured by removing the fix: the assertion went red and pytest then sat
    there until it was killed.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    for node in graph.nodes.values():
        owner = getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        if isinstance(owner, CodeInterpreterMiddleware):
            owner._registry.close()
            return


def test_the_runtime_is_given_back_when_a_turn_ends_by_exception(cfg, session_dir):
    """The interpreter's teardown is `after_agent`, and langgraph does not run
    `after_agent` when the graph raises. So the runtime outlived the turn.

    That is not a leaked handle. `quickjs_rs` pins its Runtime to one worker
    thread because it is `!Send`, and closing it means collecting on *that*
    thread; a sweep from anywhere else hits the drop check. The only sweep left
    is `Py_FinalizeEx`, on the main thread, and there the finalizer deadlocks
    rather than panicking -- so the process does not crash, it stops. Measured
    on a real run that hit `recursion_limit`: traceback printed, report already
    written to disk, and the process still sitting there minutes later.

    Driven rather than inspected, and the `eval` is why: a turn that never
    evaluated has no runtime to leave behind, so a version of this test without
    it passes against the bug.
    """
    from langgraph.errors import GraphRecursionError

    from kingfisher.infrastructure.harness.agent import release_interpreter

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
    graph = build_agent(wired, session_dir=session_dir, model=model)

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
    """`release_interpreter` finds the middleware by walking the compiled
    graph's nodes, which is not a published shape -- the same unpublished shape
    `registered_tools` reads, pinned the same way.

    Best-effort at runtime, because taking down a finished turn over an
    introspection detail is the worse trade. This is what notices instead when
    langgraph stops naming a node after the middleware that declared it.
    """
    from langchain_quickjs import CodeInterpreterMiddleware

    graph = build_agent(
        replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model()
    )

    owners = [
        getattr(getattr(getattr(node, "bound", None), "func", None), "__self__", None)
        for node in graph.nodes.values()
    ]

    assert any(isinstance(o, CodeInterpreterMiddleware) for o in owners), (
        "the interpreter is no longer reachable from the graph, so nothing closes it"
    )


def test_releasing_costs_nothing_when_the_interpreter_is_off(cfg, session_dir):
    """It runs in the teardown of every turn, including the ones on a
    deployment that never wired a sandbox. Those must not pay an import for
    it -- `langchain_quickjs` is deferred precisely so they do not."""
    graph = build_agent(cfg, session_dir=session_dir, model=_model())

    release_interpreter(cfg, graph)

    assert not _quickjs_workers()

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
from kingfisher.infrastructure.agent import build_agent, registered_tools
from tests.conftest import FakeToolCallingModel, capture_build


def _model():
    return FakeToolCallingModel(responses=[AIMessage(content="ok")])


def test_it_is_off_unless_a_deployment_wires_it(cfg, session_dir):
    """A second execution surface, and a beta dependency, should not arrive
    because someone upgraded."""
    graph = build_agent(cfg, session_dir=session_dir, model=_model())
    assert "eval" not in registered_tools(graph)


def test_wiring_it_adds_one_tool(cfg, session_dir):
    graph = build_agent(
        replace(cfg, interpreter_enabled=True), session_dir=session_dir, model=_model()
    )
    assert "eval" in registered_tools(graph)


def test_eval_is_an_ordinary_tool_a_request_may_withhold(cfg, session_dir):
    """No new axis. It is granted and withheld through `Capabilities.tools`
    like anything else, which also means the tool-name validator covers it."""
    wired = replace(cfg, interpreter_enabled=True)

    without = build_agent(
        wired,
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(tools=("read_file",)),
    )
    # Registered either way -- the allowlist refuses at call time, it does not
    # unregister. What matters is that the name is known to the validator.
    assert "eval" in registered_tools(without)


def test_a_request_that_withheld_the_shell_cannot_reach_it_from_code(cfg, session_dir, monkeypatch):
    """The whole reason for adopting this. `ptc` is the request's own grant,
    so a caller that withheld `execute` cannot call it from inside the
    sandbox either."""
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=_model(),
        capabilities=Capabilities(tools=("read_file", "eval")),
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
        capabilities=Capabilities(tools=("eval", "read_file")),  # no task
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
        capabilities=Capabilities(tools=("eval", "task")),
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
        capabilities=Capabilities(tools=("eval", "task", "read_file")),
    )

    assert "task" not in _ptc(_interpreter_in(captured))
    assert "read_file" in _ptc(_interpreter_in(captured))


def _dispatch_enabled(interpreter):
    for attribute in ("_subagents", "subagents"):
        if hasattr(interpreter, attribute):
            return getattr(interpreter, attribute)
    pytest.fail("could not read whether code-side dispatch is enabled")
    return None

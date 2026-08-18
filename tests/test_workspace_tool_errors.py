"""A workspace tool's exception is a failed tool result, not a dead run.

Found by the smoke: the model handed `csv_profile` the agent's routed path, the
tool raised `FileNotFoundError`, and a sixteen-call run died. The same mistake
through `read_file` costs nothing -- a built-in reports failures as tool results
and the model carries on.

Tested against the middleware rather than through a run, deliberately. A run
proves nothing here: re-running the smoke after the fix passed, and the
transcript showed the model had not called the failing tool at all that time.
Whether the guard works cannot depend on what a model chooses.
"""

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
    """The failure this was written for. `FileNotFoundError` from a tool the
    workspace defined used to escape the graph and take the run with it."""
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    answer = guard.wrap_tool_call(
        _Request("csv_profile"), _raises(FileNotFoundError("/data/orders.csv"))
    )

    assert isinstance(answer, ToolMessage)
    assert answer.status == "error"
    assert "/data/orders.csv" in answer.content
    assert answer.tool_call_id == "call-1"


def test_the_message_names_the_exception_type():
    """A workspace tool is somebody else's code and its exceptions were not
    written to be read by a model. `FileNotFoundError: /data/x` says what kind
    of wrong it was; the path alone does not."""
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    answer = guard.wrap_tool_call(_Request("probe"), _raises(FileNotFoundError("/data/x")))

    assert "FileNotFoundError" in answer.content


def test_a_built_in_tool_is_left_exactly_as_it_was():
    """The half that keeps this narrow.

    Built-ins already report their failures as tool results, and `HostPathGuard`
    covers the one thing they do not. Catching theirs too would put a second
    opinion between deepagents and its own error handling -- so anything not
    named here raises as it always did.
    """
    guard = WorkspaceToolErrors(frozenset({"csv_profile"}))

    with pytest.raises(FileNotFoundError):
        guard.wrap_tool_call(_Request("read_file"), _raises(FileNotFoundError("/data/x")))


def test_a_tool_that_works_is_untouched():
    """The negative control. Without it every assertion above would pass on a
    middleware that returned an error unconditionally."""
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    assert guard.wrap_tool_call(_Request("probe"), lambda _r: "the answer") == "the answer"


def test_an_interrupt_is_not_a_tool_telling_the_model_something():
    """`BaseException` is deliberately outside the catch. A `KeyboardInterrupt`
    or a `MemoryError` is not a refusal the model can act on, and converting one
    into a tool result would leave the run trying to recover from the process
    being stopped."""
    guard = WorkspaceToolErrors(frozenset({"probe"}))

    with pytest.raises(KeyboardInterrupt):
        guard.wrap_tool_call(_Request("probe"), _raises(KeyboardInterrupt()))


#: What the failing handler raises, named so the literal is not inline.
MISSING = "/data/orders.csv"


async def _araises(_request):
    raise FileNotFoundError(MISSING)


def test_the_async_path_behaves_the_same():
    """Both halves exist because the harness uses both, and a guard that held on
    one would be absent exactly when a service is serving.

    Driven with `asyncio.run` rather than a plugin, which is how `test_async`
    does it -- one convention for the repository beats two.
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
    """The guard the build handed to `create_deep_agent`, if it added one.

    Read off the construction call rather than the compiled graph: a compiled
    graph does not carry its middleware anywhere a test can reach, and the claim
    here is about what was installed.
    """
    for entry in captured.get("middleware") or ():
        if isinstance(entry, WorkspaceToolErrors):
            return entry
    return None


def test_the_build_guards_the_names_the_workspace_defined(cfg, session_dir, monkeypatch):
    """A middleware nobody installs guards nothing.

    Asserted against the *names* rather than its presence: built from the wrong
    set it would install and protect nothing, which is the same as absent and
    harder to notice.
    """
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
    """Nothing to guard, so nothing added. A middleware that wrapped every call
    to answer for an empty set is cost with no claim behind it."""
    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import capture_build

    captured = capture_build(monkeypatch)

    build_agent(cfg, session_dir=session_dir, model=_a_model())

    assert _guard_in(captured) is None


def _a_model():
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    return FakeToolCallingModel(responses=[AIMessage(content="ok")])

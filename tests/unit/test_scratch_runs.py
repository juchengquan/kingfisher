"""An offered tool running a command over files, under its caller's grant to the shell.

Driven through graphs kingfisher builds and the backend a turn gets, rather than stand-ins:
what is bound, and where, is decided by the middleware's place on a real graph and by a
real folder that a real shell can swap.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
import threading
import time
import warnings
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, cast

import pytest
from deepagents.backends import CompositeBackend, StateBackend
from langchain.agents import create_agent
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher import ScratchRun, ToolContext
from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.scratch_runs import ScratchRunsMiddleware
from kingfisher.core.agent.permitted_backend import PermittedBackend, scratch_runs_of
from kingfisher.core.agent.scratch import ScratchRunner
from kingfisher.core.agent.tool_context import NOT_GRANTED
from kingfisher.core.agent.tool_guards import guarded_tools
from kingfisher.core.definitions.agents.catalogue import LocalAgentRepository
from kingfisher.core.definitions.tools.catalogue import LocalToolRepository
from kingfisher.core.interfaces import MAX_SCRATCH_BYTES, MAX_SCRATCH_OUTPUTS, CommandResult
from kingfisher.core.session import runtime
from kingfisher.plugins import backend_at
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    paths_in,
    subagents_dir,
    tools_dir,
)
from tests.unit.test_interpreter import _from_code

if TYPE_CHECKING:
    from deepagents.backends.protocol import BackendProtocol

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell and folders")

#: A tool written the way `tools.md` shows one, which the driven tests below run.
SHOUT = '''from langchain.tools import ToolRuntime

from kingfisher import ToolContext

KEPT = []


def shout(text: str, runtime: ToolRuntime[ToolContext]) -> str:
    """Upper-case `text` by running `tr` over it in a scratch folder."""
    KEPT.append(runtime.context)
    ran = runtime.context.run(
        ["/bin/sh", "-c", "tr a-z A-Z < in.txt > out.txt"],
        {"in.txt": text.encode()},
        outputs=["out.txt"],
    )
    return ran.error or ran.files["out.txt"].decode()


def shout_again(text: str, runtime: ToolRuntime[ToolContext]) -> str:
    """`shout`, through the context an earlier call of it was handed."""
    ran = KEPT[-1].run(["/bin/echo", text])
    return ran.error or ran.output


async def ashout(text: str, runtime: ToolRuntime[ToolContext]) -> str:
    """`shout`, for an async graph."""
    ran = await runtime.context.arun(
        ["/bin/sh", "-c", "tr a-z A-Z < in.txt > out.txt"],
        {"in.txt": text.encode()},
        outputs=["out.txt"],
    )
    return ran.error or ran.files["out.txt"].decode()


TOOLS = [shout, shout_again, ashout]
'''

HELPER = """name: helper
description: Shouts.
tools: [shout]
builtin_tools: {builtins}
system_prompt: |
  You shout.
"""


def _a_tool(cfg) -> None:
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shout.py").write_text(SHOUT, encoding="utf-8")


def _calls(name: str, **args: Any) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": f"call-{name}"}])


def _results(cfg, session_dir, *responses: AIMessage, **keywords: Any) -> dict[str, str]:
    """Each tool's last result in one turn, subagents' included, by tool name."""
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=list(responses)),
        **keywords,
    )
    found: dict[str, str] = {}
    for _, chunk in built.graph.stream(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "scratch"}},
        stream_mode="updates",
        subgraphs=True,
        context=built.context,
    ):
        for update in chunk.values():
            for message in runtime.messages_in(update):
                if isinstance(message, ToolMessage) and message.name:
                    found[message.name] = str(message.content)
    return found


# -- who is handed a run ------------------------------------------------------


def test_a_tool_runs_a_command_when_its_agent_may_use_the_shell(cfg, session_dir):
    """The whole change, through the override `ToolCallRequest` does not declare: if
    langgraph stopped carrying a replaced runtime to the tool, this is what would fail."""
    _a_tool(cfg)

    found = _results(cfg, session_dir, _calls("shout", text="hi"), AIMessage(content="done"))

    assert found["shout"] == "HI"


def test_a_tool_is_refused_when_its_agent_may_not_use_the_shell(cfg, session_dir):
    """A request withheld the shell: a tool running commands for it would be the way round."""
    _a_tool(cfg)

    found = _results(
        cfg,
        session_dir,
        _calls("shout", text="hi"),
        AIMessage(content="done"),
        capabilities=Capabilities(builtin_tools=("read_file",), tools=("shout",)),
    )

    assert found["shout"] == NOT_GRANTED


def test_a_tool_is_refused_when_its_agent_must_ask_before_using_the_shell(cfg, session_dir):
    """Nothing can pause for a person inside a tool, so a gated shell is no shell here."""
    _a_tool(cfg)
    an_agent(cfg, "gated", interrupt_on="[execute]", **EVERY_TOOL)
    (agent,) = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs.values()

    found = _results(
        cfg,
        session_dir,
        _calls("shout", text="hi"),
        AIMessage(content="done"),
        agent=agent,
        checkpointer=InMemorySaver(),
    )

    assert found["shout"] == NOT_GRANTED


@pytest.mark.parametrize(
    ("builtins", "answer"), [("[read_file]", NOT_GRANTED), ("[execute]", "HI")]
)
def test_a_subagents_tool_runs_under_the_subagents_own_grant(cfg, session_dir, builtins, answer):
    """The subagent's grant, not the agent's: the agent holds the shell either way, and
    the context a subagent's tool is handed is the agent's, inherited."""
    _a_tool(cfg)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        HELPER.format(builtins=builtins), encoding="utf-8"
    )

    found = _results(
        cfg,
        session_dir,
        _calls("task", description="shout it", subagent_type="helper"),
        _calls("shout", text="hi"),
        AIMessage(content="shouted"),
        AIMessage(content="done"),
        capabilities=Capabilities(subagents=("helper",)),
    )

    assert found["shout"] == answer


def test_a_tool_called_from_code_is_refused(cfg, session_dir):
    """`eval` passes its own context to every tool its code calls, so a run bound on
    `eval`'s call would reach them all, with no gate and no grant looked at."""
    wired = replace(cfg, interpreter_enabled=True)
    _a_tool(wired)

    answered, _ = _from_code(
        wired,
        session_dir,
        "(async () => await tools.shout({text: 'hi'}))()",
        capabilities=Capabilities(builtin_tools=("eval", "execute"), tools=("shout",)),
    )

    assert NOT_GRANTED in answered


def test_a_compiled_subagents_tool_is_refused(cfg, session_dir):
    """A compiled subagent runs with none of kingfisher's middleware, so nothing binds a
    run for its tools; they are refused rather than handed one some other way."""
    _a_tool(cfg)
    loaded = {one.name: one.tool for one in LocalToolRepository(tools_dir(cfg)).found}
    (wrapped,) = guarded_tools([loaded["shout"]], paths_in(cfg, session_dir))
    graph = create_agent(
        FakeToolCallingModel(responses=[_calls("shout", text="hi"), AIMessage(content="ok")]),
        tools=[wrapped],
        context_schema=ToolContext,
    )

    out = graph.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        context=ToolContext(backend=PermittedBackend(backend_at(cfg, session_dir), [])),
    )

    (result,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert result.content == NOT_GRANTED


def test_a_run_kept_past_its_call_runs_nothing(cfg, session_dir):
    """A tool that keeps what it was handed would otherwise hold the shell for every call
    after, under another agent's grant or in another session's turn."""
    _a_tool(cfg)

    found = _results(
        cfg,
        session_dir,
        _calls("shout", text="hi"),
        _calls("shout_again", text="later"),
        AIMessage(content="done"),
    )

    assert found["shout"] == "HI"
    assert "has ended" in found["shout_again"]


def test_an_async_tool_runs_on_an_async_graph(cfg, session_dir):
    """The hook an async turn runs is `awrap_tool_call`; without it the bind raises."""
    _a_tool(cfg)
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(
            responses=[_calls("ashout", text="hi"), AIMessage(content="done")]
        ),
    )

    out = asyncio.run(
        built.graph.ainvoke(
            {"messages": [{"role": "user", "content": "go"}]}, context=built.context
        )
    )

    (result,) = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert result.content == "HI"


# -- the bound runner ---------------------------------------------------------


class _Recording:
    """A backend whose run records what it was asked, and answers that it ran."""

    def __init__(self) -> None:
        self.asked: list[dict[str, Any]] = []

    def run_in_scratch(self, argv, files, *, outputs, timeout=None, stop=None) -> ScratchRun:
        self.asked.append({"argv": argv, "files": files, "outputs": outputs, "timeout": timeout})
        return ScratchRun(output="ran", exit_code=0)


@pytest.mark.parametrize(
    ("argv", "files", "outputs", "refused"),
    [
        ("tr a-z A-Z", {}, (), "list of strings"),
        ([], {}, (), "list of strings"),
        (["/bin/true"], {"../up": b""}, (), "one plain name"),
        (["/bin/true"], {}, ("sub/out",), "one plain name"),
        (["/bin/true"], {".hidden": b""}, (), "one plain name"),
        (["/bin/true"], {"big": b"x" * (MAX_SCRATCH_BYTES + 1)}, (), "one run may carry"),
        (["/bin/true"], {}, tuple(f"f{n}" for n in range(MAX_SCRATCH_OUTPUTS + 1)), "bring back"),
    ],
)
def test_a_run_the_rules_refuse_never_reaches_the_backend(argv, files, outputs, refused):
    """Refused before any backend sees it, so a remote sandbox is held to them too."""
    backend = _Recording()

    ran = ScratchRunner(backend, seconds=30).run(argv, files, outputs=outputs, timeout=None)

    assert ran.error is not None
    assert refused in ran.error
    assert backend.asked == []


def test_each_run_is_given_what_is_left_of_the_call(monkeypatch):
    """One clock per tool call: ten runs of the longest command would hold the session
    past the claim that counts on none outliving `execution_timeout_s`."""
    now = [100.0]
    monkeypatch.setattr("kingfisher.core.agent.scratch.time.monotonic", lambda: now[0])
    backend = _Recording()
    runner = ScratchRunner(backend, seconds=30)

    runner.run(["/bin/true"], {}, outputs=(), timeout=None)
    now[0] += 25
    runner.run(["/bin/true"], {}, outputs=(), timeout=60)
    now[0] += 5
    late = runner.run(["/bin/true"], {}, outputs=(), timeout=None)

    assert [one["timeout"] for one in backend.asked] == [30, 5]
    assert late.error is not None
    assert "no time left" in late.error


def test_the_run_is_found_behind_the_composite_a_session_is():
    """A composite forwards no method of its own, so asked alone it refuses kingfisher's
    backend, whose shell is its default."""
    shell = _Recording()
    composite = CompositeBackend(
        default=cast("BackendProtocol", shell), routes={"/memories/": StateBackend()}
    )

    assert scratch_runs_of(composite) == shell.run_in_scratch
    assert scratch_runs_of(StateBackend()) is None


def test_a_backend_without_the_method_is_refused_by_name():
    ran = ScratchRunner(StateBackend(), seconds=30).run(["/bin/true"], {}, outputs=(), timeout=None)

    assert ran.error is not None
    assert "run_in_scratch" in ran.error


def test_a_context_with_nothing_bound_refuses():
    assert ToolContext(backend=None).run(["/bin/true"]).error == NOT_GRANTED


# -- the middleware ------------------------------------------------------------


@dataclass
class _Runtime:
    """`ToolRuntime`, as far as the middleware reads and replaces it."""

    context: Any


@dataclass
class _Request:
    """`ToolCallRequest`, likewise."""

    tool_call: dict[str, Any]
    runtime: _Runtime


def _handed(middleware: ScratchRunsMiddleware, name: str, context: Any) -> Any:
    """The context the tool behind `middleware` is handed for one call of `name`."""
    seen: list[Any] = []

    def handler(request: Any) -> str:
        seen.append(request.runtime.context)
        return "ok"

    middleware.wrap_tool_call(_Request({"name": name, "id": "c"}, _Runtime(context)), handler)
    return seen[0]


@pytest.mark.parametrize(
    ("tools", "gated", "name", "bound"),
    [
        (None, (), "shout", True),
        (("execute", "shout"), (), "shout", True),
        (("read_file", "shout"), (), "shout", False),
        (("execute", "shout"), ("execute",), "shout", False),
        (("execute", "eval"), (), "eval", False),
    ],
)
def test_a_run_is_bound_only_by_name_and_only_under_the_shell(tools, gated, name, bound):
    middleware = ScratchRunsMiddleware(
        frozenset({"shout"}), backend=_Recording(), tools=tools, gated=gated, seconds=30
    )

    handed = _handed(middleware, name, ToolContext(backend=None))

    assert (handed.scratch is not None) is bound


def test_a_context_kingfisher_did_not_make_is_passed_on_untouched():
    """`graph=` drives a graph with its builder's context, or none."""
    middleware = ScratchRunsMiddleware(
        frozenset({"shout"}), backend=_Recording(), tools=None, gated=(), seconds=30
    )

    assert _handed(middleware, "shout", None) is None
    assert _handed(middleware, "shout", {"theirs": 1}) == {"theirs": 1}


# -- kingfisher's own backend ----------------------------------------------------


def _scratchpad_runs(session_dir) -> list[str]:
    """The run folders left in this session's scratchpad."""
    return sorted(p.name for p in (session_dir / "scratchpad").glob(".run-*"))


def test_outputs_come_back_and_the_folder_goes(cfg, session_dir):
    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/sh", "-c", "tr a-z A-Z < in.txt > out.txt; printf done"],
        {"in.txt": b"hi"},
        outputs=["out.txt", "never.txt"],
    )

    assert (ran.exit_code, ran.output, dict(ran.files)) == (0, "done", {"out.txt": b"HI"})
    assert "never.txt" in ran.missing
    assert _scratchpad_runs(session_dir) == []


def test_the_command_sees_only_its_own_environment(cfg, session_dir, monkeypatch):
    """Not even the shell's own: `KINGFISHER_SKILLS` and anything this process holds."""
    monkeypatch.setenv("A_SERVICE_CREDENTIAL", "sk-do-not-leak")

    ran = backend_at(cfg, session_dir).default.run_in_scratch(["/usr/bin/env"], {}, outputs=[])
    named = {line.split("=", 1)[0] for line in ran.output.splitlines() if "=" in line}

    assert "sk-do-not-leak" not in ran.output
    assert named <= {"PATH", "LANG", "LC_ALL", "HOME", "TMPDIR", "PWD", "SHLVL", "_"}
    assert "HOME" in named


def test_a_fifo_left_as_an_output_is_refused_without_waiting(cfg, session_dir):
    """Opened blocking, a FIFO holds the read, and the turn, until something writes it."""
    started = time.monotonic()

    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/usr/bin/mkfifo", "out.txt"], {}, outputs=["out.txt"]
    )

    assert ran.exit_code == 0, f"the FIFO was never made: {ran.output}"
    assert time.monotonic() - started < 10
    assert ran.files == {}
    assert ran.missing["out.txt"] == "not a regular file"


def test_a_link_left_as_an_output_is_not_followed(cfg, session_dir):
    """Read in kingfisher's own process, outside the fence that stops the shell reading
    `.harness`, an output that is a link would hand the pinned agent to the tool."""
    (session_dir / ".harness" / "agent.yaml").write_text("PINNED-AGENT", encoding="utf-8")
    target = session_dir / ".harness" / "agent.yaml"

    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/ln", "-s", str(target), "out.txt"], {}, outputs=["out.txt"]
    )

    assert ran.exit_code == 0, f"the link was never made: {ran.output}"
    assert ran.files == {}
    assert "out.txt" in ran.missing


def test_a_folder_swapped_for_a_link_mid_run_hands_back_nothing_from_where_it_points(
    cfg, session_dir, caplog
):
    """Measured: a folder renamed aside and replaced by a link had kingfisher read and
    write where the link pointed. Held by descriptor, the swap costs only the outputs."""
    harness = session_dir / ".harness"
    (harness / "agent.yaml").write_text("PINNED-AGENT", encoding="utf-8")
    swap = f'here="$PWD"; cd ..; mv "$here" "$here.aside"; ln -s {harness} "$here"'

    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/sh", "-c", swap], {}, outputs=["agent.yaml"]
    )

    left = list((session_dir / "scratchpad").glob(".run-*"))
    # Without the swap there is nothing to prove: the shell made it, and it was logged.
    assert ran.exit_code == 0, f"the swap never happened: {ran.output}"
    assert any(one.is_symlink() for one in left)
    assert "left behind" in caplog.text
    assert b"PINNED-AGENT" not in b"".join(ran.files.values())
    assert "agent.yaml" in ran.missing
    assert (harness / "agent.yaml").read_text(encoding="utf-8") == "PINNED-AGENT"
    for one in left:
        if one.is_symlink():
            one.unlink()
        else:
            shutil.rmtree(one)


def test_a_command_past_its_time_is_stopped_and_its_folder_still_goes(cfg, session_dir):
    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/sleep", "30"], {}, outputs=[], timeout=1
    )

    assert ran.exit_code == 124
    assert _scratchpad_runs(session_dir) == []


def test_a_run_its_caller_stops_ends_at_once(cfg, session_dir):
    """What a cancelled async tool sets: until the command is dead the session is in use
    by a turn its caller has let go of."""
    stop = threading.Event()
    threading.Timer(0.5, stop.set).start()
    started = time.monotonic()

    backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/sleep", "30"], {}, outputs=[], stop=stop
    )

    assert time.monotonic() - started < 10
    assert _scratchpad_runs(session_dir) == []


def test_a_deployments_own_runner_is_not_gone_round(cfg, session_dir):
    """A runner a deployment supplied may run as another user or under limits; a scratch
    run launching its own process would skip all of it."""

    class Theirs:
        local = True

        def run(self, command: str, *, timeout: int | None = None) -> CommandResult:
            return CommandResult(output="", exit_code=0)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        backend = backend_at(cfg, session_dir, runner=Theirs())

    ran = backend.default.run_in_scratch(["/bin/true"], {}, outputs=[])

    assert ran.error is not None
    assert "runner of its own" in ran.error


@pytest.mark.parametrize("argv", [["-u", "x"], ["A=b"]])
def test_a_program_env_would_read_as_its_own_is_refused(cfg, session_dir, argv):
    ran = backend_at(cfg, session_dir).default.run_in_scratch(argv, {}, outputs=[])

    assert ran.error is not None
    assert _scratchpad_runs(session_dir) == []

"""An offered tool running a command over files, under its caller's grant to the shell.

Driven through graphs kingfisher builds and the backend a turn gets, rather than stand-ins:
what is bound, and where, is decided by the middleware's place on a real graph and by a
real folder that a real shell can swap.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import stat
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

from kingfisher import Kingfisher, Request, ScratchRun, ToolContext
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
from kingfisher.plugins import LocalSessions, NoScratchRunLimit, Plugins, backend_at
from kingfisher.plugins.sessions.scratch import ScratchFolder
from tests.conftest import (
    EVERY_TOOL,
    FakeToolCallingModel,
    an_agent,
    paths_in,
    subagents_dir,
    tools_dir,
)
from tests.unit.scripted import Scripted
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


def _results(
    cfg, session_dir, *responses: AIMessage, way: str = "sync", **keywords: Any
) -> dict[str, str]:
    """Each tool's last result in one turn, subagents' included, by tool name."""
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=list(responses)),
        **keywords,
    )
    found: dict[str, str] = {}

    def keep(chunk: Any) -> None:
        for update in chunk.values():
            for message in runtime.messages_in(update):
                if isinstance(message, ToolMessage) and message.name:
                    found[message.name] = str(message.content)

    start = {"messages": [{"role": "user", "content": "go"}]}
    options: dict[str, Any] = {
        "config": {"configurable": {"thread_id": "scratch"}},
        "stream_mode": "updates",
        "subgraphs": True,
        "context": built.context,
    }
    if way == "sync":
        for _, chunk in built.graph.stream(start, **options):
            keep(chunk)
        return found

    async def drained() -> None:
        async for _, chunk in built.graph.astream(start, **options):
            keep(chunk)

    asyncio.run(drained())
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


def test_a_run_kept_past_its_call_runs_nothing(cfg, session_dir, way):
    """A tool that keeps what it was handed would otherwise hold the shell for every call
    after, under another agent's grant or in another session's turn. Both ways, since an
    awaited turn closes the runner in `awrap_tool_call`, which nothing ran."""
    _a_tool(cfg)

    found = _results(
        cfg,
        session_dir,
        _calls("shout", text="hi"),
        _calls("shout_again", text="later"),
        AIMessage(content="done"),
        way=way,
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


# -- a deployment's limit -----------------------------------------------------


class _Limit:
    """A `ScratchRunLimit` refusing the graphs named in `refused`, keeping what it is asked."""

    def __init__(self, *refused: str) -> None:
        self.refused = refused
        self.asked: list[tuple[str, str]] = []

    def allows(self, name: str, kind: str) -> bool:
        self.asked.append((name, kind))
        return name not in self.refused


def _answers(cfg, session_dir, *responses: AIMessage, **keywords: Any) -> list[str]:
    """Every `shout` result in one turn, subagents' included, in the order they came."""
    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=list(responses)),
        **keywords,
    )
    found: list[str] = []
    for _, chunk in built.graph.stream(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": "scratch"}},
        stream_mode="updates",
        subgraphs=True,
        context=built.context,
    ):
        for update in chunk.values():
            for message in runtime.messages_in(update):
                if isinstance(message, ToolMessage) and message.name == "shout":
                    found.append(str(message.content))
    return found


def _shouted_then_delegated(to: str) -> list[AIMessage]:
    """The agent shouts, then hands `to` a task in which it shouts too."""
    return [
        _calls("shout", text="hi"),
        _calls("task", description="shout it", subagent_type=to),
        _calls("shout", text="hi"),
        AIMessage(content="shouted"),
        AIMessage(content="done"),
    ]


def _a_helper(cfg, builtins: str = "[execute]") -> None:
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        HELPER.format(builtins=builtins), encoding="utf-8"
    )


def test_a_limit_refusing_a_subagent_leaves_the_agents_run_bound(cfg, session_dir):
    """A limit asked about the wrong graph would refuse the agent with the subagent, or
    neither; the agent's run in the same build is the control."""
    _a_tool(cfg)
    _a_helper(cfg)
    limit = _Limit("helper")

    found = _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("helper"),
        capabilities=Capabilities(subagents=("helper",)),
        scratch_run_limit=limit,
    )

    assert found == ["HI", NOT_GRANTED]
    # Each graph by its own name and kind, the agent, named by no definition, as empty --
    # and once: the agent was asked again for a stack built only to be thrown away.
    assert sorted(limit.asked) == sorted(
        [("", "agent"), ("helper", "subagent"), ("general-purpose", "general-purpose")]
    )


def test_general_purpose_is_asked_by_that_name(cfg, session_dir):
    """deepagents' own subagent is handed the agent's tools: asked as the agent, a
    deployment refusing it would have refused the agent, and the other way round."""
    _a_tool(cfg)

    found = _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("general-purpose"),
        scratch_run_limit=_Limit("general-purpose"),
    )

    assert found == ["HI", NOT_GRANTED]


class _Raising(_Limit):
    """A `ScratchRunLimit` raising for the graphs named in `refused`, allowing the rest."""

    def allows(self, name: str, kind: str) -> bool:
        self.asked.append((name, kind))
        if name in self.refused:
            raise KeyError(name)
        return True


#: What a tool on a graph whose limit raised `KeyError('helper')` is told.
RAISED = "refused: this deployment's ScratchRunLimit raised KeyError: 'helper'"


@pytest.mark.parametrize(
    ("limit", "answer"), [(_Raising, RAISED), (_Limit, NOT_GRANTED)], ids=["raised", "false"]
)
def test_a_limit_raising_for_a_subagent_refuses_only_its_tools_and_says_why(
    cfg, session_dir, limit, answer
):
    """A raise that propagated failed every turn over a graph it never needed, one taken as
    allowed handed a run out, and one refused generically left the agent nothing to say
    why. A plain `False` keeps the generic refusal; the agent's run is the control."""
    _a_tool(cfg)
    _a_helper(cfg)

    found = _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("helper"),
        capabilities=Capabilities(subagents=("helper",)),
        scratch_run_limit=limit("helper"),
    )

    assert found == ["HI", answer]


def test_a_limit_that_raises_is_logged_once_for_the_graph_it_raised_on(
    cfg, session_dir, caplog
):
    """Refused without a word in the log, a deployment's bug reads as its policy."""
    _a_tool(cfg)
    _a_helper(cfg)

    _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("helper"),
        capabilities=Capabilities(subagents=("helper",)),
        scratch_run_limit=_Raising("helper"),
    )

    raised = [r for r in caplog.records if "the scratch run limit raised" in r.getMessage()]
    assert [r.getMessage() for r in raised] == [
        "the scratch run limit raised on subagent 'helper'; its tools are refused a run"
    ]
    assert raised[0].exc_info is not None
    assert raised[0].exc_info[0] is KeyError


@pytest.mark.parametrize(
    ("builtins", "answer"), [("[read_file]", NOT_GRANTED), ("[execute]", "HI")]
)
def test_a_limit_allowing_a_graph_without_the_shell_binds_nothing(
    cfg, session_dir, builtins, answer
):
    """The limit can only take away: joined to kingfisher's rule with `or`, it would hand a
    subagent refused the shell a way round that, through its tools."""
    _a_tool(cfg)
    _a_helper(cfg, builtins)

    found = _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("helper"),
        capabilities=Capabilities(subagents=("helper",)),
        scratch_run_limit=_Limit(),
    )

    assert found == ["HI", answer]


#: Where the shout runs, how the agent's model gets it there, and what the limit is asked.
_SHOUTED_IN = {
    "agent": ([_calls("shout", text="hi"), AIMessage(content="done")], ("gated", "agent")),
    "subagent": (
        [
            _calls("task", description="shout it", subagent_type="helper"),
            _calls("shout", text="hi"),
            AIMessage(content="shouted"),
            AIMessage(content="done"),
        ],
        ("helper", "subagent"),
    ),
    "general-purpose": (
        [
            _calls("task", description="shout it", subagent_type="general-purpose"),
            _calls("shout", text="hi"),
            AIMessage(content="shouted"),
            AIMessage(content="done"),
        ],
        ("general-purpose", "general-purpose"),
    ),
}


@pytest.mark.parametrize("graph", list(_SHOUTED_IN))
@pytest.mark.parametrize(
    "case",
    [
        ("[execute]", _Limit, NOT_GRANTED),
        ("[write_file]", _Limit, "HI"),
        ("[execute]", _Raising, NOT_GRANTED),
        ("[write_file]", _Raising, RAISED),
    ],
    ids=["gated-allowing", "ungated-allowing", "gated-raising", "ungated-raising"],
)
def test_a_gated_shell_is_refused_whatever_the_limit_answers(cfg, session_dir, case, graph):
    """Nothing can pause for a person inside a tool, so no limit may hand a gated shell on,
    on any graph the gate reaches: one not handed the agent's gate ran the command unasked.
    A limit raising there is not what refused it, so the refusal does not blame it."""
    gates, limit, answer = case
    _a_tool(cfg)
    _a_helper(cfg)
    an_agent(cfg, "gated", interrupt_on=gates, subagents="[helper]", **EVERY_TOOL)
    (agent,) = LocalAgentRepository(cfg.catalogue_roots["agents"]).specs.values()
    responses, asked = _SHOUTED_IN[graph]
    name = asked[0]
    limit = limit(name) if limit is _Raising else limit()

    found = _answers(
        cfg,
        session_dir,
        *responses,
        agent=agent,
        checkpointer=InMemorySaver(),
        scratch_run_limit=limit,
    )

    # `RAISED` names the helper's graph; the limit here raised for this graph's.
    assert found == [answer.replace("'helper'", repr(name))]
    assert asked in limit.asked


@pytest.mark.parametrize(
    ("limit", "direct_answer"),
    [(_Limit(), "HI"), (_Raising(""), RAISED.replace("'helper'", "''"))],
    ids=["allowing", "raising"],
)
def test_a_tool_called_from_code_is_refused_whatever_the_limit_answers(
    cfg, session_dir, limit, direct_answer
):
    """`eval` hands its context to every tool its code calls, so neither a limit allowing
    the agent nor the refusal bound for one raising may reach them through it. The direct
    call under the same grant is the control."""
    wired = replace(cfg, interpreter_enabled=True)
    _a_tool(wired)
    granted = Capabilities(builtin_tools=("eval", "execute"), tools=("shout",))

    answered, _ = _from_code(
        wired,
        session_dir,
        "(async () => await tools.shout({text: 'hi'}))()",
        capabilities=granted,
        scratch_run_limit=limit,
    )
    direct = _answers(
        wired,
        session_dir,
        _calls("shout", text="hi"),
        AIMessage(content="done"),
        capabilities=granted,
        scratch_run_limit=limit,
    )

    assert NOT_GRANTED in answered
    assert direct == [direct_answer]


@pytest.mark.parametrize("limit", [None, NoScratchRunLimit()], ids=["left-out", "default"])
def test_left_out_every_graph_binds_what_kingfisher_allows(cfg, session_dir, limit):
    """A deployment that never heard of the plugin would otherwise lose, or gain, a run."""
    _a_tool(cfg)
    _a_helper(cfg)
    wired = {} if limit is None else {"scratch_run_limit": limit}

    found = _answers(
        cfg,
        session_dir,
        *_shouted_then_delegated("helper"),
        capabilities=Capabilities(subagents=("helper",)),
        **wired,
    )
    withheld = _answers(
        cfg,
        session_dir,
        _calls("shout", text="hi"),
        AIMessage(content="done"),
        capabilities=Capabilities(builtin_tools=("read_file",), tools=("shout",)),
        **wired,
    )

    assert found == ["HI", "HI"]
    assert withheld == [NOT_GRANTED]


@pytest.mark.parametrize(("tool", "asked"), [(False, False), (True, True)])
def test_a_graph_holding_no_offered_tool_is_not_asked(cfg, session_dir, tool, asked):
    """There is nothing there to bind a run for, so a limit asked about it is told of a
    graph its answer cannot change."""
    if tool:
        _a_tool(cfg)
    limit = _Limit()

    build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="done")]),
        scratch_run_limit=limit,
    )

    # Counted rather than `bool`, which passes with the agent asked twice.
    expected = [("", "agent"), ("general-purpose", "general-purpose")] if asked else []
    assert sorted(limit.asked) == sorted(expected)


@pytest.mark.parametrize(
    ("refused", "answer"), [("only", NOT_GRANTED), ("other", "HI")], ids=["it", "another"]
)
def test_the_command_hands_the_limit_to_every_turn(scripted, refused, answer):
    """A limit the service took and never passed to the build refused nothing. Through
    `Plugins`, as the command hands it, with the agent asked by its own name."""
    _a_tool(scripted)
    an_agent(scripted, **EVERY_TOOL)
    Scripted.script.extend([_calls("shout", text="hi"), AIMessage(content="done")])
    wiring = Plugins(scratch_run_limit=_Limit(refused)).keywords()

    Kingfisher(scripted, sessions=LocalSessions(), **wiring).run(
        Request("go", agent="only", session_id="s")
    )

    (result,) = [m for m in Scripted.heard[-1] if isinstance(m, ToolMessage)]
    # The recorded result's id follows on a line of its own.
    assert str(result.content).split("\n")[0] == answer


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
    ("tools", "gated", "limit", "name", "bound"),
    [
        (None, (), True, "shout", True),
        (("execute", "shout"), (), True, "shout", True),
        (("read_file", "shout"), (), True, "shout", False),
        (("execute", "shout"), ("execute",), True, "shout", False),
        (("execute", "eval"), (), True, "eval", False),
        (None, (), False, "shout", False),
    ],
)
def test_a_run_is_bound_only_by_name_and_only_under_the_shell(tools, gated, limit, name, bound):
    middleware = ScratchRunsMiddleware(
        frozenset({"shout"}),
        backend=_Recording(),
        tools=tools,
        gated=gated,
        seconds=30,
        limit_allows=limit,
    )

    handed = _handed(middleware, name, ToolContext(backend=None))

    assert (handed.scratch is not None) is bound


def test_a_context_kingfisher_did_not_make_is_passed_on_untouched():
    """`graph=` drives a graph with its builder's context, or none."""
    middleware = ScratchRunsMiddleware(
        frozenset({"shout"}),
        backend=_Recording(),
        tools=None,
        gated=(),
        seconds=30,
        limit_allows=True,
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


def test_a_tools_run_is_not_handed_the_session_root(cfg, session_dir):
    """A documented root would make reaching session files by path, around the rules the
    tool's backend keeps, look like the way in. The shell's own command beside it is the
    control, or a variable nobody exported would pass.
    """
    backend = backend_at(cfg, session_dir)

    ran = backend.default.run_in_scratch(["/usr/bin/env"], {}, outputs=[])
    shell = backend.execute("/usr/bin/env")

    assert "KINGFISHER_SESSION_ROOT=" not in ran.output
    assert f"KINGFISHER_SESSION_ROOT={session_dir}" in shell.output.splitlines()


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


def test_a_scratchpad_swapped_for_a_link_gets_no_run_where_it_points(
    cfg, session_dir, tmp_path
):
    """The session is checked for links when it opens, not between runs: followed, a
    scratchpad the shell swapped for a link since had the run's folder made, and its
    inputs written, wherever the link pointed."""
    backend = backend_at(cfg, session_dir)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    scratchpad = session_dir / cfg.layout.scratchpad
    scratchpad.rename(scratchpad.with_name("scratchpad.aside"))
    scratchpad.symlink_to(elsewhere)

    with pytest.raises(OSError):
        backend.default.run_in_scratch(["/bin/true"], {"in.txt": b"x"}, outputs=[])

    assert list(elsewhere.iterdir()) == []


def test_an_input_is_never_written_through_a_file_already_under_its_name(
    cfg, session_dir, tmp_path
):
    """Opened without `O_EXCL`, an input written over a hard link planted under its name
    went into the file the link shares, wherever that is."""
    shared = tmp_path / "shared.txt"
    shared.write_bytes(b"KEPT")

    with ScratchFolder(session_dir, cfg.layout.scratchpad) as folder:
        os.link(shared, folder.path / "in.txt")
        with pytest.raises(FileExistsError):
            folder.write({"in.txt": b"WRITTEN THROUGH"})

    assert shared.read_bytes() == b"KEPT"


def test_a_runs_folder_is_its_owners_alone(cfg, session_dir):
    """Made by the umask's leave, the folder holding a run's inputs could be read, and an
    output planted in it, by anyone else on the machine. The umask is opened up so it
    cannot be what keeps the folder private."""
    opened = os.umask(0)
    try:
        with ScratchFolder(session_dir, cfg.layout.scratchpad) as folder:
            mode = stat.S_IMODE(folder.path.stat().st_mode)
    finally:
        os.umask(opened)

    assert mode == 0o700


def test_outputs_past_what_one_run_may_bring_back_are_left_where_they_are(
    cfg, session_dir, monkeypatch
):
    """Read into kingfisher's own process: past the cap an output came back cut short as
    though whole, and each one after it came back empty. The cap is lowered so a run of
    a few bytes can cross it; the whole of it is what one run may bring back."""
    monkeypatch.setattr("kingfisher.plugins.sessions.scratch.MAX_BROUGHT_BACK", 1000)

    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        [
            "/bin/sh",
            "-c",
            "printf '%600s' '' > a; printf '%600s' '' > b; printf '%10s' '' > c",
        ],
        {},
        outputs=["a", "b", "c"],
    )

    assert ran.exit_code == 0, ran.output
    # `c` still fits in what `b` would have used: only the one past the cap stays behind.
    assert ran.files == {"a": b" " * 600, "c": b" " * 10}
    assert set(ran.missing) == {"b"}
    assert "past the 1000 bytes" in ran.missing["b"]


def test_an_output_the_agent_planted_mid_run_comes_back_as_the_commands_own(
    cfg, session_dir
):
    """What `tools.md` tells a tool author to distrust: the run's folder is in the
    scratchpad the agent writes, so a file it plants under an output's name is brought
    back as if the command had written it.

    Planted while the command is still running, and through the backend the file tools
    are given rather than by reaching for the host path: a test that wrote the file
    afterwards, or around the backend, would pass with the folder beyond the agent's
    reach, which is the claim being made.
    """
    backend = backend_at(cfg, session_dir)
    waiting = (
        "n=0; while [ ! -f ../release ] && [ $n -lt 200 ]; do sleep 0.05; n=$((n+1)); done; "
        "printf wrote-nothing"
    )
    ran: list[ScratchRun] = []

    def run() -> None:
        ran.append(
            backend.default.run_in_scratch(["/bin/sh", "-c", waiting], {}, outputs=["out.txt"])
        )

    thread = threading.Thread(target=run)
    thread.start()
    try:
        until = time.monotonic() + 10
        while not _scratchpad_runs(session_dir) and time.monotonic() < until:
            time.sleep(0.005)
        live = _scratchpad_runs(session_dir)
        planted = backend.write(f"/scratchpad/{live[0]}/out.txt", "PLANTED") if live else None
    finally:
        backend.write("/scratchpad/release", "go")
        thread.join(30)

    assert live, "the run never made its folder, so nothing was measured"
    assert planted is not None and planted.error is None, f"not planted: {planted}"
    # The control: with an output of its own the command would be the one answering.
    assert ran[0].output == "wrote-nothing"
    assert ran[0].files == {"out.txt": b"PLANTED"}
    assert _scratchpad_runs(session_dir) == []


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


def test_a_cancelled_async_run_stops_its_command_before_it_lets_go(cfg, session_dir):
    """Every async run on kingfisher's own backend goes through the thread fallback: not
    told to stop, its command ran to its end after the cancel, and let go of first, its
    folder outlived the call that made it."""
    runner = ScratchRunner(backend_at(cfg, session_dir), seconds=30)

    async def cancelled() -> float:
        call = asyncio.ensure_future(
            runner.arun(["/bin/sleep", "10"], {}, outputs=[], timeout=None)
        )
        until = time.monotonic() + 10
        while not _scratchpad_runs(session_dir) and time.monotonic() < until:
            await asyncio.sleep(0.01)
        assert _scratchpad_runs(session_dir), "the run never made its folder"
        started = time.monotonic()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call
        return time.monotonic() - started

    took = asyncio.run(cancelled())

    assert _scratchpad_runs(session_dir) == []
    assert took < 5


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


@pytest.mark.parametrize(
    ("files", "outputs"),
    [({"../escaped.txt": b"written"}, []), ({}, ["../kept.txt"])],
    ids=["file", "output"],
)
def test_a_caller_of_the_method_itself_cannot_name_a_file_beside_the_run(
    cfg, session_dir, files, outputs
):
    """`ScratchRunner` refuses such a name first, but the method is the backend's, found by
    `scratch_runs_of`, and the folder opens a name relative to itself with no check of its
    own: `../` wrote into the scratchpad, and read a file there back as an output."""
    scratchpad = session_dir / "scratchpad"
    (scratchpad / "kept.txt").write_bytes(b"the session's")

    ran = backend_at(cfg, session_dir).default.run_in_scratch(
        ["/bin/true"], files, outputs=outputs
    )

    assert ran.error is not None
    assert "plain name" in ran.error
    assert ran.files == {}
    assert not (scratchpad / "escaped.txt").exists()


def test_a_caller_of_the_method_itself_is_held_to_the_shells_time(cfg, session_dir):
    """`ScratchRunner` hands a run only what its call has left, but a caller of the method
    asking for more ran past `execution_timeout_s`, which the claim's grace counts on no
    command outliving."""
    backend = backend_at(replace(cfg, execution_timeout_s=1), session_dir)

    ran = backend.default.run_in_scratch(["/bin/sleep", "5"], {}, outputs=[], timeout=60)

    assert ran.exit_code == 124

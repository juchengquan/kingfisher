"""The standing middleware each graph kingfisher builds is wrapped in, and where it sits."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.standing import standing_middleware
from kingfisher.core.definitions.agents.spec import AgentSpec
from kingfisher.core.session.ledger import Ledger
from kingfisher.core.session.store import KeptLedger
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel, subagent_of, subagents_dir, tools_dir
from tests.unit.test_subagent_skills import offer_skills


class _Offered(AgentMiddleware):
    """Stands in for one a definition names."""

    name = "_Offered"


A_TOOL = '''
def lookup(key: str) -> str:
    """Look a key up."""
    return key


TOOLS = [lookup]
'''


def _everything(cfg, session_dir):
    """A build with every branch on: a ledger, memory, the interpreter, a narrowed
    request, skills, a workspace tool, a declared subagent and a middleware named."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "lookup.py").write_text(A_TOOL, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nsystem_prompt: |\n  You help.\n", encoding="utf-8"
    )
    offer_skills(cfg, "tabular-qa")
    wired = replace(cfg, memory_enabled=True, interpreter_enabled=True, skills_enabled=True)
    return build_agent(
        wired,
        agent=AgentSpec(
            name="probed",
            description="an agent with everything",
            system_prompt="You work.",
            builtin_tools=("read_file", "task", "eval"),
            middlewares=("offered",),
            skills=("tabular-qa",),
            subagents=("helper",),
        ),
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        middleware_registry={"offered": _Offered},
        capabilities=Capabilities(
            builtin_tools=("read_file", "task", "eval"),
            tools=("lookup",),
            skills=("tabular-qa",),
            subagents=("helper",),
        ),
        ledger=Ledger("t", KeptLedger()),
    )


def test_the_agent_is_wrapped_in_standing_then_switched_then_offered(cfg, session_dir):
    """A setting's middleware or a definition's landing among the standing ones, where it
    would see a call before a guard had judged it or rewritten its paths -- and, under
    both of them, the one layer that answers for what the tool itself raised, which read
    a deployment's own fault as the tool's failure while it sat outside them."""
    built = _everything(cfg, session_dir)

    assert [type(m).__name__ for m in built.middleware] == [
        "ResultClearingMiddleware",
        "TodoListMiddleware",
        "RecorderMiddleware",
        "StepBudgetMiddleware",
        "ModelReplyMiddleware",
        "GrantedOnlyMiddleware",
        "GuardedCallsMiddleware",
        "AttachmentsMiddleware",
        "BuiltinToolsMiddleware",
        "NarrowedSkillsMiddleware",
        "MemoryMiddleware",
        "CodeInterpreterMiddleware",
        "_Offered",
        "WorkspaceToolFailureMiddleware",
    ]


def test_general_purpose_is_wrapped_in_standing_then_offered(cfg, session_dir):
    """`general-purpose` built apart from the agent and missing a guard the agent has,
    which is how it once reached a workspace tool with the paths the model wrote."""
    built = _everything(cfg, session_dir)

    assert [type(m).__name__ for m in subagent_of(built, "general-purpose")["middleware"]] == [
        "RecorderMiddleware",
        "StepBudgetMiddleware",
        "ModelReplyMiddleware",
        "GrantedOnlyMiddleware",
        "GuardedCallsMiddleware",
        "AttachmentsMiddleware",
        "BuiltinToolsMiddleware",
        "_Offered",
        "WorkspaceToolFailureMiddleware",
    ]


@pytest.mark.parametrize(
    ("kind", "given"),
    [
        ("subagent", {"reachable": ("helper",)}),
        ("subagent", {"clearing": lambda _: None}),
        ("general-purpose", {"reachable": ("helper",)}),
        ("general-purpose", {"skills": ("tabular-qa",)}),
    ],
)
def test_what_is_the_agents_alone_is_refused_elsewhere(cfg, kind, given):
    """A subagent handed the declared-subagent check meant for the agent's own `task` or
    the result clearing sized to the agent's grant, or `general-purpose` a skills index,
    which it is never handed."""
    with pytest.raises(ValueError, match="agent"):
        standing_middleware(
            kind,
            names=frozenset(),
            paths=None,
            layout=cfg.layout,
            backend=None,
            step_limit=cfg.recursion_limit,
            trigger=170_000,
            **given,
        )


def test_an_ungranted_call_is_refused_once_before_anything_splits_it(cfg, session_dir, way):
    """The grant filter sat innermost, so an ungranted `grep` of `alpha|beta` was split in
    two first and the model read a refusal under a heading for each part."""
    from langchain_core.tools import tool

    from tests.unit.test_workspace_tool_errors import _through

    stack = standing_middleware(
        "agent",
        names=frozenset(),
        paths=None,
        layout=cfg.layout,
        backend=backend_at(cfg, session_dir),
        step_limit=cfg.recursion_limit,
        trigger=170_000,
        tools=("read_file",),
    )
    searched: list[str] = []

    @tool
    def grep(pattern: str) -> str:
        """Stands in for deepagents' own, which the filter must not reach."""
        searched.append(pattern)
        return "found"

    call = {"name": "grep", "args": {"pattern": "alpha|beta"}, "id": "c1"}
    answer = _through(stack, call, grep, way)

    assert answer.content == (
        "Error: grep is not available for this request. Available tools: read_file."
    )
    assert searched == []

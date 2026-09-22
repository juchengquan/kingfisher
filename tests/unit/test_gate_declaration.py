"""An agent file that says which tools need a person, and what the wiring does with it.

The other half of the approval gate. `test_approval_gates.py` drives the pause and
the answer through an injected graph; this one starts from the YAML and goes through
`build_agent`, which is the path a real deployment takes and the only one that can
show a declared gate reaching deepagents at all.
"""

from __future__ import annotations

from dataclasses import replace as replace_cfg
from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import InMemorySaver

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.kinds.agents.reading import read
from kingfisher.kinds.agents.spec import AgentError
from tests.conftest import FakeToolCallingModel, tools_dir

WRITES = [
    AIMessage(
        content="",
        tool_calls=[
            {
                "name": "write_file",
                "args": {"file_path": "/derived/a.txt", "content": "x"},
                "id": "c1",
            }
        ],
    ),
    AIMessage(content="done"),
]

A_TOOL = '''
def line_count(path: str) -> str:
    """Count the lines in a text file."""
    return "0"


TOOLS = [line_count]
'''

#: "Nothing was passed", so a test can ask for *no* checkpointer without `None`
#: meaning "give me the usual one".
_DEFAULT: Any = object()


def _agent(**fields: str) -> Any:
    written = "".join(f"{key}: {value}\n" for key, value in fields.items())
    return read(
        f"name: gatekeeper\ndescription: An agent.\n{written}system_prompt: |\n  Go.\n",
        Path("gatekeeper.yaml"),
    )


def _run(cfg, session_dir, spec, *, checkpointer: Any = _DEFAULT, **kwargs: Any) -> Any:
    agent = build_agent(
        cfg,
        session_dir=session_dir,
        agent=spec,
        model=FakeToolCallingModel(responses=list(WRITES)),
        checkpointer=InMemorySaver() if checkpointer is _DEFAULT else checkpointer,
        **kwargs,
    )
    return agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]},
        config={"configurable": {"thread_id": session_dir.name}, "recursion_limit": 12},
    )


def test_a_declared_gate_stops_the_call(cfg, session_dir):
    """The whole slice: a line in a YAML file reaches deepagents as an interrupt."""
    out = _run(cfg, session_dir, _agent(interrupt_on="[write_file]"))

    assert "__interrupt__" in out, "the gated call ran without stopping for anyone"
    assert not (session_dir / "derived" / "a.txt").exists()


def test_the_same_agent_without_the_line_does_not_stop(cfg, session_dir):
    """The control beside it. Without this, the assertion above passes against an
    agent that was never going to reach `write_file` in the first place.
    """
    out = _run(cfg, session_dir, _agent())

    assert "__interrupt__" not in out
    assert (session_dir / "derived" / "a.txt").read_text() == "x"


def test_only_the_named_tool_is_gated(cfg, session_dir):
    """A gate on one tool that stopped another would be a gate nobody could reason
    about, and `write_file` running here is what shows the list is read as a list.
    """
    out = _run(cfg, session_dir, _agent(interrupt_on="[execute]"))

    assert "__interrupt__" not in out
    assert (session_dir / "derived" / "a.txt").read_text() == "x"


def test_a_gate_on_a_name_no_tool_answers_to_is_refused(cfg, session_dir):
    """A typo'd gate is a gate that never fires, and an author who believes a call is
    gated while it runs is worse off than one whose workspace refused to start.
    """
    with pytest.raises(AgentError, match="no tool here answers to"):
        _run(cfg, session_dir, _agent(interrupt_on="[wrtie_file]"))


def test_a_gate_with_no_checkpointer_is_refused(cfg, session_dir):
    """Without a saver the pause cannot be kept, and langgraph suppresses the
    interrupt rather than raising it -- so the gated call is skipped in silence.
    That is the outcome `interrupt_on` was written to prevent, so it is refused.
    """
    stateless = replace_cfg(cfg, conversation_enabled=False)

    with pytest.raises(AgentError, match="no checkpointer"):
        _run(stateless, session_dir, _agent(interrupt_on="[write_file]"), checkpointer=None)


def test_an_agent_with_no_gates_needs_no_checkpointer(cfg, session_dir):
    """The refusal above must not reach every stateless deployment, which is most of
    the reason it is asked of the gate list rather than of the flag.
    """
    stateless = replace_cfg(cfg, conversation_enabled=False)

    out = _run(stateless, session_dir, _agent(), checkpointer=None)

    assert "__interrupt__" not in out


def test_the_gates_reach_deepagents_as_the_argument_a_delegate_inherits(
    cfg, session_dir, monkeypatch
):
    """A delegate cannot declare `interrupt_on` -- the field is `REFUSED` in the
    subagent format -- so what it stops on has to arrive from its parent, or a
    workspace could gate the shell and lose the gate the moment work was delegated.

    What this checks is the half kingfisher owns: the top-level argument is set, and
    nothing per-delegate is, so deepagents' own inheritance is the only route there
    is. It does not drive a delegate to a pause -- doing that through `build_agent`
    means scripting a model the delegate resolves for itself -- so a change in how
    deepagents propagates the argument would pass here.
    """
    from tests.conftest import capture_build

    directory = cfg.workspace / "subagents"
    directory.mkdir(exist_ok=True)
    (directory / "scribe.yaml").write_text(
        "name: scribe\ndescription: Writes.\nsystem_prompt: |\n  Write.\n", encoding="utf-8"
    )
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        agent=_agent(interrupt_on="[write_file]", subagents="[scribe]"),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        checkpointer=InMemorySaver(),
    )

    assert captured.get("interrupt_on") == {"write_file": True}


def test_an_agent_that_gates_nothing_passes_no_gate_argument(cfg, session_dir, monkeypatch):
    """Passing an empty mapping would make deepagents install the middleware for every
    agent in every workspace, for gates that are not there.
    """
    from tests.conftest import capture_build

    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        agent=_agent(),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        checkpointer=InMemorySaver(),
    )

    assert "interrupt_on" not in captured


def test_a_gate_on_a_tool_this_caller_was_not_granted_is_allowed(cfg, session_dir):
    """A grant narrows per request, so an agent gating a workspace tool that this
    caller did not ask for has a gate with nothing to fire on -- which is fine.
    Refusing it would break a legitimately narrow run for a danger that is not there.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "line_count.py").write_text(A_TOOL, encoding="utf-8")

    out = _run(
        cfg,
        session_dir,
        _agent(interrupt_on="[line_count]"),
        capabilities=Capabilities(tools=None),
    )

    assert "__interrupt__" not in out, "a tool nobody was granted still gated something"
    assert (session_dir / "derived" / "a.txt").read_text() == "x"

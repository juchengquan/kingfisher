"""A deployment's instructions: the file beside its definitions, or a plugin of its own."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.prompting import INSTRUCTIONS_FILE, InstructionsFile
from kingfisher.core.config import ConfigError
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import an_agent, subagent_of, subagents_dir
from tests.unit.scripted import Scripted

HELPER = """name: helper
description: Checks a figure.
system_prompt: |
  You check figures.
"""


def _system_said(cfg, *requests: Request, **wiring) -> list[str]:
    """What the model was told as its system message, once per turn, on real turns."""
    told: list[str] = []
    kf = Kingfisher(cfg, sessions=LocalSessions(), **wiring)
    for request in requests:
        Scripted.script.append(AIMessage(content="done"))
        kf.run(request)
        told.append(str(Scripted.heard[-1][0].content))
    return told


def test_the_file_beside_the_definitions_reaches_the_model(scripted):
    """The default: written once, beside `models.yaml`, and said to every agent."""
    an_agent(scripted)
    scripted.authored_files[INSTRUCTIONS_FILE].write_text(
        "House rule: cite every figure.\n", encoding="utf-8"
    )

    (said,) = _system_said(scripted, Request("go", agent="only"))

    assert "House rule: cite every figure." in said


def test_no_file_is_no_instructions(cfg):
    """A definitions folder without one is the ordinary case, not an error."""
    assert InstructionsFile().text(cfg) == ""


def test_a_deployments_own_are_asked_on_every_turn(scripted):
    """Read once at startup, a change would wait for a restart that nothing announces,
    where the file it replaces took effect on the next turn."""
    an_agent(scripted)

    class Counting:
        asked = 0

        def text(self, cfg) -> str:
            self.asked += 1
            return f"Rule number {self.asked}."

    first, second = _system_said(
        scripted,
        Request("go", agent="only", session_id="s"),
        Request("again", agent="only", session_id="s"),
        instructions=Counting(),
    )

    assert "Rule number 1." in first
    assert "Rule number 2." in second


def test_a_build_hands_the_instructions_to_every_subagent(cfg, session_dir):
    """A house rule that reached the main agent and nothing it delegated to is one
    delegated work quietly escapes."""
    an_agent(cfg, "lead", subagents="[helper]")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    built = build_agent(
        cfg,
        agent=kf.agent_named("lead"),
        backend=backend_at(cfg, session_dir),
        checkpointer=None,
        instructions="House rule: cite every figure.",
    )

    told = subagent_of(built, "helper")["system_prompt"]
    assert told.index("House rule: cite every figure.") < told.index("You check figures.")


def test_a_prompt_left_in_the_workspace_is_refused(cfg):
    """Ignored, the house rules it held would stop reaching every agent with nothing
    said, and the turns would run differently."""
    (cfg.workspace / "PROMPT.md").write_text("House rule: be terse.", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"INSTRUCTIONS\.md"):
        Kingfisher(cfg, sessions=LocalSessions())

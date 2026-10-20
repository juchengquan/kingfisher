"""A deployment's instructions: the file beside its definitions, or a plugin of its own."""

from __future__ import annotations

import pytest
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.prompting import INSTRUCTIONS_FILE, InstructionsFile
from kingfisher.core.config import ConfigError
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import EVERY_TOOL, agent_named, an_agent, subagent_of, subagents_dir
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
    an_agent(scripted, **EVERY_TOOL)
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
    an_agent(scripted, **EVERY_TOOL)

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
    an_agent(cfg, "lead", subagents="[helper]", **EVERY_TOOL)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    kf = Kingfisher(cfg, sessions=LocalSessions())

    built = build_agent(
        cfg,
        agent=agent_named(kf, "lead"),
        backend=backend_at(cfg, session_dir),
        checkpointer=None,
        instructions="House rule: cite every figure.",
    )

    told = subagent_of(built, "helper")["system_prompt"]
    assert told.index("House rule: cite every figure.") < told.index("You check figures.")


@pytest.mark.parametrize("rule", ["House rule: cite every figure.", ""], ids=["rule", "none"])
def test_general_purpose_is_told_the_house_rules_as_a_declared_subagent_is(scripted, rule):
    """kingfisher supplied `general-purpose` with deepagents' bare prompt, so a turn
    delegated to it escaped every house rule a declared subagent was held to.

    Driven, and the subagent's own model call read: the spec kingfisher hands over is only
    what deepagents is asked to run.
    """
    an_agent(scripted, builtin_tools="[read_file, task]")
    scripted.authored_files[INSTRUCTIONS_FILE].write_text(rule, encoding="utf-8")
    Scripted.script.extend(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "task",
                        "args": {"description": "check", "subagent_type": "general-purpose"},
                        "id": "t",
                    }
                ],
            ),
            AIMessage("checked"),
            AIMessage("done"),
        ]
    )
    Kingfisher(scripted, sessions=LocalSessions()).run(Request("go", agent="only"))
    systems = [heard[0].text for heard in Scripted.heard]

    assert len(systems) == 3, "the subagent was never called, so the rest is vacuous"
    told = systems[1]
    deepagents_own = GENERAL_PURPOSE_SUBAGENT["system_prompt"]
    assert told.startswith(f"{rule}\n\n---\n\n{deepagents_own}" if rule else deepagents_own)
    assert "You are kingfisher" not in told


def test_a_prompt_left_in_the_workspace_is_refused(cfg):
    """Ignored, the house rules it held would stop reaching every agent with nothing
    said, and the turns would run differently."""
    (cfg.workspace / "PROMPT.md").write_text("House rule: be terse.", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"INSTRUCTIONS\.md"):
        Kingfisher(cfg, sessions=LocalSessions())

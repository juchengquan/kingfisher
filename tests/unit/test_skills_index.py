"""The skills index every graph holding skills is sent, written from one template."""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.core.agent.prompting import ShellPrompt, skills_index
from kingfisher.core.layout import DEFAULT_LAYOUT
from tests.conftest import subagents_dir
from tests.unit.test_prompt import _told

#: What kingfisher's template says and deepagents' did not, and what deepagents' said that
#: is not true here: it read 100 lines by default, and ran a script by absolute path.
OURS = "what it says to do is in its `SKILL.md`"
THEIRS = ("## Skills System", "progressive disclosure", "limit=1000", "use absolute paths")


def _skill(cfg) -> None:
    skill = cfg.catalogue_roots["skills"] / "csv"
    skill.mkdir(parents=True, exist_ok=True)
    (skill / "SKILL.md").write_text(
        "---\nname: csv\ndescription: Reading CSV files.\n---\nRead it.\n", encoding="utf-8"
    )


def _consulting(cfg, helper_tools: str, *, skills: str, agent_tools: str) -> list[str]:
    """The agent's first system message and its subagent's, from one real turn."""
    _skill(cfg)
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "helper.yaml").write_text(
        f"name: helper\ndescription: Helps.\nbuiltin_tools: {helper_tools}\nskills: [csv]\n"
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    asks = AIMessage(
        content="",
        tool_calls=[
            {"name": "task", "args": {"description": "x", "subagent_type": "helper"}, "id": "t"}
        ],
    )
    agent, helper, _ = _told(
        replace(cfg, skills_enabled=True),
        asks,
        AIMessage("helped"),
        AIMessage("done"),
        builtin_tools=agent_tools,
        skills=skills,
        subagents="[helper]",
    )
    return [agent, helper]


@pytest.mark.parametrize("skills", ['["*"]', "[csv]"], ids=["every skill", "named"])
def test_every_index_is_written_from_kingfishers_template_once(scripted, skills):
    """The agent granted every skill was sent deepagents' own index, a narrowed agent and
    a subagent deepagents' wording in kingfisher's list, and the agent kingfisher's section
    besides: two accounts of one catalogue, the library's saying to read 1,000 lines where
    the default already is, and to run scripts by a path the shell cannot use."""
    told = _consulting(scripted, "[read_file, execute]", skills=skills, agent_tools='["*"]')

    for system in told:
        assert system.count("## Skills\n") == 1
        assert OURS in " ".join(system.split())
        for phrase in THEIRS:
            assert phrase not in system, phrase
        # Both hold the shell, and kingfisher's says how it reaches the catalogue.
        assert "$KINGFISHER_SKILLS" in system


@pytest.mark.parametrize("skills", ['["*"]', "[csv]"], ids=["every skill", "named"])
def test_a_graph_without_the_shell_is_not_told_how_the_shell_reaches_skills(scripted, skills):
    """Told of `$KINGFISHER_SKILLS` without the shell, a model calls the tool it was told
    to reach it with, and is refused."""
    told = _consulting(scripted, "[read_file]", skills=skills, agent_tools="[read_file, task]")

    for system in told:
        assert "$KINGFISHER_SKILLS" not in system
        # The control: each was handed an index, without the shell's sentence.
        assert OURS in " ".join(system.split())


def test_an_index_offering_nothing_says_nothing_of_creating_a_skill(scripted):
    """deepagents' empty index said skills could be created in `/skills/`, where every
    write is refused."""
    wired = replace(scripted, skills_enabled=True)
    (system,) = _told(wired, builtin_tools="[read_file]", skills='["*"]')

    assert "(No skills are offered here.)" in system
    assert "create skills" not in system


def test_a_brace_the_shell_writes_reaches_the_index_as_written():
    """deepagents fills the template with `str.format`, so a deployment's sentence holding
    braces would fail every model call, or lose them."""
    shell = ShellPrompt(skills="Run `{script}` with `${KINGFISHER_SKILLS}`.")

    template = skills_index(DEFAULT_LAYOUT, shell, shelled=True)
    filled = template.format(skills_locations="", skills_load_warnings="", skills_list="")

    assert "Run `{script}` with `${KINGFISHER_SKILLS}`." in filled

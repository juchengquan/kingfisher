"""A skill is opened with `read_file`: a definition naming one must hold it, and a request
withholding it is offered none."""

from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher
from kingfisher.core.access.capabilities import ALL, Capabilities
from kingfisher.core.agent.assembly import build_agent, builtin_tool_names
from kingfisher.core.agent.middlewares.narrowing import NarrowedSkillsMiddleware
from kingfisher.core.definitions import seeding
from kingfisher.core.definitions.agents import reading as agents
from kingfisher.core.definitions.agents.spec import AgentError, AgentSpec
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.documents import DefinitionText
from kingfisher.core.definitions.skills import READ_FILE
from kingfisher.core.definitions.subagents.spec import SubagentError, declared
from kingfisher.core.request import Request
from kingfisher.core.session.steps import drive
from kingfisher.plugins import LocalSessions, backend_at
from tests.conftest import (
    FakeToolCallingModel,
    a_subagent,
    an_agent,
    middleware_of,
    subagents_dir,
    withheld_told,
)

HEAD = "name: n\ndescription: d\nsystem_prompt: |\n  Go.\n"

#: The two ways a file leaves `read_file` out, each with the words that tell it which.
WITHOUT = {
    "a list without it": ("builtin_tools: [ls, grep]\n", "builtin_tools leaves out 'read_file'"),
    "no line at all": ("", "builtin_tools is left out, which grants no built-in"),
}

#: The two ways a file holds it.
WITH = {"listed": "builtin_tools: [read_file]\n", "every built-in": 'builtin_tools: ["*"]\n'}


def read_agent(text: str) -> AgentSpec:
    return agents.read(DefinitionText(text, Path("n.yaml")))


def carrying(builtin: dict[str, object], skills: Path) -> dict[str, object]:
    """A portable `SUBAGENTS` entry carrying the skills in `skills`."""
    return {
        "name": "p",
        "description": "d",
        "system_prompt": "Go.",
        "skills": str(skills),
        **builtin,
    }


# -- refused when the file is read -------------------------------------------


@pytest.mark.parametrize("written", sorted(WITHOUT))
def test_an_agent_naming_skills_it_cannot_open_is_refused(written):
    """An agent told of skills without `read_file` spent a refused call on each it opened."""
    line, said = WITHOUT[written]

    with pytest.raises(AgentError, match=re.escape(said)) as refused:
        read_agent(HEAD + line + "skills: [audit]\n")

    assert "skills names audit" in str(refused.value)
    assert "Add read_file to builtin_tools" in str(refused.value)


def test_an_agent_naming_every_skill_it_cannot_open_is_refused():
    """`["*"]` names no skill, and a rule reading names alone would let it through."""
    with pytest.raises(AgentError, match="skills names every skill"):
        read_agent(HEAD + "builtin_tools: [ls]\nskills: ['*']\n")


@pytest.mark.parametrize("written", sorted(WITHOUT))
def test_a_subagent_naming_skills_it_cannot_open_is_refused(written):
    """A subagent told of skills without `read_file` spent a refused call on each it opened."""
    line, said = WITHOUT[written]

    with pytest.raises(SubagentError, match=re.escape(said)):
        a_subagent(HEAD + line + "skills: [audit]\n", "n.yaml")


def test_a_subagent_naming_only_its_own_skill_without_read_file_is_refused():
    """The shipped `redactor`'s shape: a bundled entry is kept apart from `skills`, so a
    rule reading that field alone passed the one definition that had the defect.
    """
    with pytest.raises(SubagentError, match="skills names redaction"):
        a_subagent(
            HEAD + "builtin_tools: [ls]\nskills:\n  - name: redaction\n    source: bundled\n",
            "n.yaml",
        )


@pytest.mark.parametrize("written", ["a list without it", "no line at all"])
def test_a_python_subagent_carrying_skills_it_cannot_open_is_refused(written, tmp_path):
    """A portable entry carrying skills without `read_file`, including a `SubAgent` taken
    bare, which has no word for built-ins at all.
    """
    builtin = {"builtin_tools": ["ls", "grep"]} if written == "a list without it" else {}

    with pytest.raises(SubagentError, match=re.escape(WITHOUT[written][1])) as refused:
        declared(carrying(builtin, tmp_path), "pack.py")

    assert str(tmp_path) in str(refused.value), "it names the directory it carried"


@pytest.mark.parametrize("written", sorted(WITH))
def test_a_definition_holding_read_file_keeps_its_skills(written, tmp_path):
    """The control: a rule refusing every `skills:` line would pass each refusal above."""
    line = WITH[written]
    builtin = yaml.safe_load(line)

    assert read_agent(HEAD + line + "skills: [audit]\n").skills == ("audit",)
    assert a_subagent(HEAD + line + "skills: [audit]\n", "n.yaml").skills == ("audit",)
    assert declared(carrying(builtin, tmp_path), "pack.py").carried["skills"] == (tmp_path,)


def test_the_refusal_does_not_wait_for_skills_to_be_switched_on(cfg):
    """Read only with the switch on, a deployment would carry the broken file until the day
    it turned skills on, and learn of it then.
    """
    an_agent(cfg, builtin_tools="[ls]", skills="[audit]")

    assert not cfg.skills_enabled
    with pytest.raises(AgentError, match="the tool a skill is opened with"):
        Definitions.from_config(cfg).warm()


def test_the_tool_a_skill_is_opened_with_is_a_built_in(cfg):
    """deepagents renaming `read_file` would leave every refusal demanding a tool nothing
    offers, and every grant of it refused as unknown.
    """
    assert READ_FILE in (builtin_tool_names(cfg, Definitions.from_config(cfg)) or ())


# -- dropped for a request that withholds it ---------------------------------


def offer(cfg, *names: str) -> None:
    for name in names:
        (cfg.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A procedure.\n---\nBody.\n", encoding="utf-8"
        )


def build(cfg, session_dir, *, agent: AgentSpec | None = None, **caps):
    wired = replace(cfg, skills_enabled=True)
    return build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        agent=agent,
        capabilities=Capabilities(**caps),
    )


#: An agent holding every built-in and every skill, so only the request narrows either.
EVERYTHING = AgentSpec(
    name="a", description="d", system_prompt="Go.", builtin_tools=ALL, tools=ALL, skills=ALL
)


def told_of_skills(built) -> dict[str, bool]:
    """Each place an agent hears of skills: the index, and the prompt's two paragraphs."""
    return {
        "index": built.skills is not None
        or any(isinstance(m, NarrowedSkillsMiddleware) for m in built.middleware),
        "section": "## Skills" in built.system_prompt,
        # The shell's paragraph under it, which only kingfisher's own shell writes.
        "shell": "KINGFISHER_SKILLS" in built.system_prompt,
    }


@pytest.mark.parametrize("skills", [ALL, ("alpha",)], ids=["every skill", "named"])
def test_a_request_withholding_read_file_is_told_of_no_skills(cfg, session_dir, skills):
    """Narrowing `read_file` away left the index and the prompt's skills text in place, so
    the model opened a skill and was refused, once per skill it tried.
    """
    offer(cfg, "alpha")

    built = build(
        cfg, session_dir, agent=EVERYTHING, builtin_tools=("ls", "execute"), skills=skills
    )

    assert told_of_skills(built) == {"index": False, "section": False, "shell": False}


@pytest.mark.parametrize("skills", [ALL, ("alpha",)], ids=["every skill", "named"])
def test_a_request_keeping_read_file_is_still_told_of_its_skills(cfg, session_dir, skills):
    """The control: dropping skills for every narrowed request would pass the rule above."""
    offer(cfg, "alpha")

    built = build(
        cfg,
        session_dir,
        agent=EVERYTHING,
        builtin_tools=("ls", "execute", READ_FILE),
        skills=skills,
    )

    assert told_of_skills(built) == {"index": True, "section": True, "shell": True}


def bundled_index(cfg, session_dir, *builtin: str) -> list[object]:
    """`surveyor`'s skills index, for a request granting `builtin`: its skill is its own."""
    folder = subagents_dir(cfg) / "surveyor"
    (folder / "skills" / "sampling").mkdir(parents=True)
    (folder / "skills" / "sampling" / "SKILL.md").write_text(
        "---\nname: sampling\ndescription: Samples rows.\n---\nBody.\n", encoding="utf-8"
    )
    (folder / "surveyor.yaml").write_text(
        "name: surveyor\ndescription: Surveys.\nbuiltin_tools: [read_file, ls]\n"
        "skills: [{name: sampling, source: bundled}]\nsystem_prompt: |\n  Go.\n",
        encoding="utf-8",
    )
    built = build(cfg, session_dir, subagents=("surveyor",), builtin_tools=builtin)
    return [
        m for m in middleware_of(built, "surveyor") if isinstance(m, NarrowedSkillsMiddleware)
    ]


def test_a_subagent_withholding_read_file_is_told_of_none_of_its_own_skills(cfg, session_dir):
    """A subagent's own skills skip the deployment's switch, and so skipped this too: its
    index outlived the `read_file` a request took away.
    """
    assert bundled_index(cfg, session_dir, "ls") == []


def test_a_subagent_keeping_read_file_is_still_told_of_its_own_skills(cfg, session_dir):
    """The control for the rule above."""
    assert len(bundled_index(cfg, session_dir, "ls", READ_FILE)) == 1


@pytest.mark.parametrize(
    ("builtin", "reported"), [(("ls",), False), (("ls", READ_FILE), True)]
)
def test_the_run_reports_no_skill_withheld_where_none_could_be_opened(
    cfg, shipped, builtin, reported
):
    """`1 skill(s) not granted` says the others were, to a request that withheld
    `read_file` and so was offered none.
    """
    seeding.seed(cfg, shipped)
    an_agent(cfg, builtin_tools='["*"]', tools='["*"]', skills='["*"]')
    on = replace(cfg, skills_enabled=True)
    asked = Request(
        "go",
        agent="only",
        capabilities=Capabilities(builtin_tools=builtin, skills=("code-review",)),
    )

    prepared = drive(Kingfisher(on, sessions=LocalSessions())._prepare_steps(asked))

    assert ("skill" in dict(withheld_told(prepared.events))) is reported

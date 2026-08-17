"""What a delegate is told it knows.

A subagent inherits none of its parent's middleware, so an index it is not
given is an index it has no idea exists. That is why `skills:` had to be built
rather than inherited, and why omitting it means none.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from tests.conftest import FakeToolCallingModel, capture_build

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.definitions import read_subagent
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.scoping import ScopedSkills, ToolAllowlist


def define(cfg, body: str, name: str = "reviewer") -> None:
    (cfg.workspace / "subagents").mkdir(parents=True, exist_ok=True)
    (cfg.workspace / "subagents" / f"{name}.yaml").write_text(body, encoding="utf-8")


def offer_skills(cfg, *names: str) -> None:
    for name in names:
        (cfg.skills_dir / name).mkdir(parents=True, exist_ok=True)
        (cfg.skills_dir / name / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: A procedure.\n---\nBody.\n", encoding="utf-8"
        )


def build(cfg, session_dir, monkeypatch, **caps):
    captured = capture_build(monkeypatch)
    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(**caps),
    )
    return captured


def middleware_of(captured, name: str) -> list:
    (spec,) = [s for s in captured["subagents"] if s["name"] == name]
    return spec.get("middleware", [])


# -- the field ------------------------------------------------------------


def test_a_definition_can_name_the_skills_its_delegate_gets(cfg, session_dir, monkeypatch):
    offer_skills(cfg, "tabular-qa", "code-review")
    define(cfg, "name: reviewer\ndescription: d\nskills: [tabular-qa]\n"
        "system_prompt: |\n  You review.\n")

    captured = build(cfg, session_dir, monkeypatch, subagents=("reviewer",))

    (scoped,) = [m for m in middleware_of(captured, "reviewer") if isinstance(m, ScopedSkills)]
    assert set(scoped._allowed) == {"tabular-qa"}


def test_omitting_skills_grants_none(cfg, session_dir, monkeypatch):
    """Not what omitting `tools` means, and the asymmetry is the point: a
    delegate's body is already its procedure."""
    offer_skills(cfg, "tabular-qa")
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n")

    captured = build(cfg, session_dir, monkeypatch, subagents=("reviewer",))

    assert not [m for m in middleware_of(captured, "reviewer") if isinstance(m, ScopedSkills)]


def test_omitting_tools_still_inherits(cfg, session_dir, monkeypatch):
    """The other half of the asymmetry, so a change to one is not read as
    licence to change the other."""
    define(cfg, "name: reviewer\ndescription: d\nsystem_prompt: |\n  You review.\n")

    captured = build(cfg, session_dir, monkeypatch, subagents=("reviewer",))

    assert not [m for m in middleware_of(captured, "reviewer") if isinstance(m, ToolAllowlist)]


def test_both_can_be_named_together(cfg, session_dir, monkeypatch):
    offer_skills(cfg, "tabular-qa")
    define(
        cfg,
        "name: reviewer\ndescription: d\n"
        "builtin_tools: [read_file]\nskills: [tabular-qa]\n"
        "system_prompt: |\n  You review.\n",
    )

    captured = build(cfg, session_dir, monkeypatch, subagents=("reviewer",))
    middleware = middleware_of(captured, "reviewer")

    assert [type(m).__name__ for m in middleware] == ["ToolAllowlist", "ScopedSkills"]


# -- the two refusals -----------------------------------------------------


def test_a_definition_naming_an_unknown_skill_fails_loudly(cfg, session_dir, monkeypatch):
    """A mistake in the definition, so it raises -- the same way `build_agent`
    already refuses a request naming a skill nothing defines."""
    define(cfg, "name: reviewer\ndescription: d\nskills: [nonesuch]\n"
        "system_prompt: |\n  You review.\n")

    with pytest.raises(CapabilityError, match="names unknown skill"):
        build(cfg, session_dir, monkeypatch, subagents=("reviewer",))


def test_a_delegate_cannot_reach_past_the_request(cfg, session_dir, monkeypatch):
    """Not a mistake -- a caller narrower than the definition -- so the skill
    is dropped rather than raised, exactly as intersect drops it for the parent."""
    offer_skills(cfg, "tabular-qa", "code-review")
    define(
        cfg,
        "name: reviewer\ndescription: d\n"
        "skills: [tabular-qa, code-review]\n"
        "system_prompt: |\n  You review.\n",
    )

    captured = build(
        cfg, session_dir, monkeypatch, subagents=("reviewer",), skills=("tabular-qa",)
    )

    (scoped,) = [m for m in middleware_of(captured, "reviewer") if isinstance(m, ScopedSkills)]
    assert set(scoped._allowed) == {"tabular-qa"}, "the delegate kept a skill its caller lacked"


# -- the format -----------------------------------------------------------


def test_the_field_parses_in_both_yaml_forms(tmp_path):
    """A block list is the skill spec's own form, and both reach the domain
    already parsed now that a definition is read as YAML."""
    inline = read_subagent(
        "name: r\ndescription: d\nskills: [a, b]\nsystem_prompt: |\n  Body.\n", tmp_path / "r.md"
    )
    block = read_subagent(
        "name: r\ndescription: d\nskills:\n  - a\n  - b\n"
        "system_prompt: |\n  Body.\n", tmp_path / "r.md"
    )

    assert inline.skills == block.skills == ("a", "b")

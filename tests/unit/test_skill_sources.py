"""Two skills called `lookup`, from two parties who never met."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import build_backend, skills_sources
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills
from kingfisher.skills import registry as skill_registry
from kingfisher.skills.catalogue import LocalSkillRepository
from tests.conftest import FakeToolCallingModel, capture_build

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nBody of the skill.\n"


def _skill(root, folder, name, desc):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")


def _two_parties(cfg):
    for party in ("research", "legal"):
        _skill(cfg.skills_dir, f"{party}/lookup", "lookup", f"The {party} way.")
    return skill_registry.read(LocalSkillRepository(cfg.skills_dir), root=cfg.skills_dir)


# -- both survive ----------------------------------------------------------


def test_a_folder_becomes_its_own_source(cfg):
    """Nested skills are invisible to a single root source -- deepagents lists one level
    deep and no further -- so a folder is a source or it is nothing.
    """
    _skill(cfg.skills_dir, "research/lookup", "lookup", "The research way.")

    assert skill_registry.sources(cfg.skills_dir) == (
        ("catalogue", "/"),
        ("research", "/research/"),
    )


def test_a_folder_that_is_itself_a_skill_is_not_a_source(cfg):
    """Both look like a directory under the root."""
    _skill(cfg.skills_dir, "flat", "flat", "At the root.")

    assert skill_registry.sources(cfg.skills_dir) == (("catalogue", "/"),)


def test_two_parties_can_both_ship_one_name(cfg):
    """The whole point."""
    registry = _two_parties(cfg)

    assert set(registry.offered) == {"research::lookup", "legal::lookup"}
    assert registry.description("research::lookup") == "The research way."
    assert registry.description("legal::lookup") == "The legal way."


def test_a_unique_name_stays_bare(cfg):
    """Every catalogue that exists today has no collisions, and none of them should have
    to learn a new spelling for that.
    """
    _skill(cfg.skills_dir, "alone", "alone", "Only one of me.")

    assert _read(cfg).names == ("alone",)


def _read(cfg):
    return skill_registry.read(LocalSkillRepository(cfg.skills_dir), root=cfg.skills_dir)


# -- naming one ------------------------------------------------------------


def test_a_bare_name_that_two_sources_offer_is_refused(cfg):
    """The safety property."""
    registry = _two_parties(cfg)

    with pytest.raises(CapabilityError, match="more than one source"):
        registry.resolve("lookup")


def test_the_refusal_names_both_spellings(cfg):
    """A refusal that does not say what to write instead is a refusal someone has to go
    and research.
    """
    registry = _two_parties(cfg)

    with pytest.raises(CapabilityError) as raised:
        registry.resolve("lookup")

    assert "legal::lookup" in str(raised.value)
    assert "research::lookup" in str(raised.value)


def test_a_qualified_name_resolves(cfg):
    assert _two_parties(cfg).resolve("research::lookup") == "research::lookup"


def test_a_qualified_name_from_the_wrong_source_is_refused(cfg):
    """Told apart from an unknown skill, because they send a reader to different places:
    one is a typo, the other is the wrong party.
    """
    registry = _two_parties(cfg)

    with pytest.raises(CapabilityError, match="no skill 'lookup' in 'sales'"):
        registry.resolve("sales::lookup")


def test_a_request_may_activate_one_of_them(cfg, session_dir):
    _two_parties(cfg)

    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("research::lookup",)),
    )


def test_a_request_naming_it_bare_is_refused_at_build(cfg, session_dir):
    _two_parties(cfg)

    with pytest.raises(CapabilityError, match="more than one source"):
        build_agent(
            replace(cfg, skills_enabled=True),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(skills=("lookup",)),
        )


def test_a_broken_skill_inside_a_folder_is_still_reported(cfg):
    """`unloadable` read `repository.names`, which lists the root and stops."""
    _skill(cfg.skills_dir, "research/fine", "fine", "Loads.")
    (cfg.skills_dir / "research" / "broken").mkdir(parents=True)
    (cfg.skills_dir / "research" / "broken" / "SKILL.md").write_text(
        "---\nname: broken\n---\nNo description.\n", encoding="utf-8"
    )

    registry = _read(cfg)

    assert registry.names == ("fine",)
    assert registry.unloadable == ("research/broken",)


# -- the two loaders, which do not delegate --------------------------------


def test_the_async_loader_agrees_with_the_sync_one(cfg, session_dir):
    """`before_agent` and `abefore_agent` each build their own dictionary -- neither
    calls the other -- so overriding one leaves the other collapsing.

    It fails *open* -- a synchronous run offers both skills and an `astream` run
    silently offers one -- and "it delegates" is the assumption that let the shell
    sandbox nest itself twice while thirteen tests passed.
    """
    _two_parties(cfg)
    backend = build_backend(replace(cfg, skills_enabled=True), session_dir)
    middleware = NarrowedSkills(
        allowed=("research::lookup",),
        backend=backend,
        sources=skills_sources(("research", "legal")),
    )

    from_sync = middleware.before_agent({}, None, {})["skills_metadata"]
    from_async = asyncio.run(middleware.abefore_agent({}, None, {}))["skills_metadata"]

    sync_ids = [s[skill_registry.KEY] for s in from_sync]

    assert sync_ids == [s[skill_registry.KEY] for s in from_async]
    assert "research::lookup" in sync_ids
    assert "legal::lookup" in sync_ids


def test_only_the_activated_one_reaches_the_model(cfg, session_dir):
    """Both load; one is shown."""
    _two_parties(cfg)
    backend = build_backend(replace(cfg, skills_enabled=True), session_dir)
    middleware = NarrowedSkills(
        allowed=("research::lookup",),
        backend=backend,
        sources=skills_sources(("research", "legal")),
    )

    loaded = middleware.before_agent({}, None, {})["skills_metadata"]
    shown = middleware._format_skills_list(loaded)

    assert "The research way." in shown
    assert "The legal way." not in shown


# -- the boundary that was failing open ------------------------------------


def test_a_nested_skill_is_denied_at_the_path_it_actually_has(cfg, session_dir, monkeypatch):
    """`_skill_denials` wrote `/skills/{name}/**`, which is where a skill sits only
    while every skill sits at the top level.
    """
    _two_parties(cfg)
    captured = capture_build(monkeypatch)

    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("research::lookup",)),
    )

    # Minus `.harness`, which every request is denied whatever it activates.
    denied = [
        p
        for r in captured["permissions"]
        if r.mode == "deny" and "read" in r.operations
        for p in r.paths
        if p != "/.harness/**"
    ]

    assert "/skills/legal/lookup/**" in denied
    assert "/skills/research/lookup/**" not in denied, "the activated one must stay readable"
    assert all(p.startswith("/skills/") for p in denied), "every rule must sit under a route"

"""Which skills the agent will actually have, versus which look like skills.

Two readers disagreed. Kingfisher listed directories; deepagents opened them and
kept what it could parse. A skill it drops was advertised anyway, so activating
one passed validation, allowed the name through the filter, and produced an
agent with no skills and no complaint.

These pin both halves: what the registry answers, and that naming something it
does not hold is now refused rather than silently honoured.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.conftest import FakeToolCallingModel

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.catalogue import Catalogue
from kingfisher.infrastructure.harness import skill_registry
from kingfisher.infrastructure.harness.agent import available_skills, build_agent
from kingfisher.infrastructure.skill_store import LocalSkillRepository

GOOD = "---\nname: {name}\ndescription: {desc}\n---\nBody of the skill.\n"


def _skill(root, folder, text):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(text, encoding="utf-8")


def _read(root):
    return skill_registry.read(LocalSkillRepository(root), root=root)


# -- what the registry answers --------------------------------------------


def test_it_offers_what_deepagents_will_load(cfg):
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))

    assert _read(cfg.skills_dir).names == ("good",)


def test_a_skill_with_no_description_is_not_offered(cfg):
    """deepagents refuses it, so the agent will never hear of it. Advertising it
    is what let a caller activate one and get nothing."""
    _skill(cfg.skills_dir, "nodesc", "---\nname: nodesc\n---\nBody.\n")

    registry = _read(cfg.skills_dir)

    assert registry.names == ()
    assert registry.unloadable == ("nodesc",)


def test_a_header_naming_something_else_is_offered_under_the_header_name(cfg):
    """The folder is not the name -- deepagents files it under what the header
    says. So it is neither missing nor loadable-as-typed: it is present under a
    name nobody wrote, which is why it is offered and not reported."""
    _skill(cfg.skills_dir, "folder", GOOD.format(name="header", desc="Disagrees."))

    registry = _read(cfg.skills_dir)

    assert registry.names == ("header",)
    assert registry.unloadable == (), "it loaded; it is only under another name"


def test_a_description_comes_back_for_a_listing(cfg):
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="What it is for."))

    assert _read(cfg.skills_dir).description("good") == "What it is for."


def test_an_empty_catalogue_is_not_an_error(cfg):
    """A deployment may legitimately ship no skills. What it must not be is an
    empty catalogue nobody mentioned, which `unloadable` covers."""
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)

    assert _read(cfg.skills_dir).names == ()


# -- the bug it exists to close -------------------------------------------


def test_validation_offers_only_what_will_load(cfg):
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))
    _skill(cfg.skills_dir, "nodesc", "---\nname: nodesc\n---\nBody.\n")

    assert available_skills(cfg, None) == ("good",)


def test_activating_a_skill_the_agent_cannot_load_is_refused(cfg, session_dir):
    """It used to build. The grant was accepted, `ScopedSkills` allowed the
    name, deepagents never listed it, and the agent got no skills at all --
    with nothing anywhere saying so."""
    _skill(cfg.skills_dir, "nodesc", "---\nname: nodesc\n---\nBody.\n")

    with pytest.raises(CapabilityError, match="unknown skill"):
        build_agent(
            replace(cfg, skills_enabled=True),
            session_dir=session_dir,
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(skills=("nodesc",)),
        )


def test_a_loadable_skill_still_builds(cfg, session_dir):
    """The negative control: closing the hole must not close the door."""
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))

    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("good",)),
    )


def test_the_catalogue_reads_it_once(cfg):
    """Warmed with the rest and cached. deepagents itself loads once per session
    and checkpoints the answer, so re-reading per turn would be answering a
    question nobody re-asks."""
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))
    catalogue = Catalogue.from_config(cfg)

    assert catalogue.registry is catalogue.registry


# -- the coupling, pinned --------------------------------------------------


def test_the_private_lister_is_still_there():
    """`SkillMetadata` is public and the lister is not, so this reaches for an
    underscore -- the same coupling `WorkspaceScopedBackend` takes on
    `_get_backend_and_key`, and pinned for the same reason.

    Without this, a deepagents release that renames it turns the registry empty:
    every skill becomes unloadable, every activation is refused, and the message
    blames the catalogue. That is the original bug wearing a different hat, so
    the rename has to fail *here* instead.
    """
    from deepagents.middleware.skills import _list_skills_with_errors

    assert callable(_list_skills_with_errors)


def test_the_metadata_still_carries_what_the_registry_reads():
    """Three keys are load-bearing: `name` is what a request activates, `path`
    is what tells a loaded skill from a missing one, and `description` is what
    a listing prints. A shape change upstream should fail here rather than
    silently produce a registry of blanks."""
    from deepagents.middleware.skills import SkillMetadata

    assert {"name", "path", "description"} <= set(SkillMetadata.__annotations__)

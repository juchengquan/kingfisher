"""Which skills the agent will actually have, versus which look like skills."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import available_skills, build_agent
from kingfisher.skills import registry as skill_registry
from kingfisher.skills.catalogue import LocalSkillRepository
from tests.conftest import FakeToolCallingModel

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
    """deepagents refuses it, so the agent will never hear of it."""
    _skill(cfg.skills_dir, "nodesc", "---\nname: nodesc\n---\nBody.\n")

    registry = _read(cfg.skills_dir)

    assert registry.names == ()
    assert registry.unloadable == ("nodesc",)


def test_a_header_naming_something_else_is_offered_under_the_header_name(cfg):
    """The folder is not the name -- deepagents files it under what the header says."""
    _skill(cfg.skills_dir, "folder", GOOD.format(name="header", desc="Disagrees."))

    registry = _read(cfg.skills_dir)

    assert registry.names == ("header",)
    assert registry.unloadable == (), "it loaded; it is only under another name"


def test_a_description_comes_back_for_a_listing(cfg):
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="What it is for."))

    assert _read(cfg.skills_dir).description("good") == "What it is for."


def test_an_empty_catalogue_is_not_an_error(cfg):
    """A deployment may legitimately ship no skills."""
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)

    assert _read(cfg.skills_dir).names == ()


# -- the bug it exists to close -------------------------------------------


def test_validation_offers_only_what_will_load(cfg):
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))
    _skill(cfg.skills_dir, "nodesc", "---\nname: nodesc\n---\nBody.\n")

    assert available_skills(cfg, None) == ("good",)


def test_activating_a_skill_the_agent_cannot_load_is_refused(cfg, session_dir):
    """It used to build."""
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
    """Warmed with the rest and cached."""
    _skill(cfg.skills_dir, "good", GOOD.format(name="good", desc="A fine skill."))
    catalogue = Definitions.from_config(cfg)

    assert catalogue.registry is catalogue.registry


# -- the coupling, pinned --------------------------------------------------


def test_the_private_lister_is_still_there():
    """`SkillMetadata` is public and the lister is not, so this reaches for an
    underscore -- the same coupling `WorkspaceScopedBackend` takes on
    `_get_backend_and_key`, and pinned for the same reason.
    """
    from deepagents.middleware.skills import _list_skills_with_errors

    assert callable(_list_skills_with_errors)


def test_the_metadata_still_carries_what_the_registry_reads():
    """Three keys are load-bearing: `name` is what a request activates, `path` is what
    tells a loaded skill from a missing one, and `description` is what a listing
    prints.
    """
    from deepagents.middleware.skills import SkillMetadata

    assert {"name", "path", "description"} <= set(SkillMetadata.__annotations__)


# -- present, and under a name nobody typed ---------------------------------


def test_a_header_naming_something_else_is_reported(cfg):
    """The gap the two neighbouring reports left open."""
    _skill(cfg.skills_dir, "company-lookup", GOOD.format(name="find-company", desc="Looks up."))

    registry = _read(cfg.skills_dir)

    assert registry.names == ("find-company",), "it loads, under the header name"
    assert registry.unloadable == (), "it is not missing"
    assert registry.misfiled == (("company-lookup", "find-company"),)


def test_a_header_that_agrees_is_not_reported(cfg):
    """The negative control, and the one that matters most: every well-formed skill in
    every catalogue takes this path, so a false positive here is a warning on every
    listing.
    """
    _skill(cfg.skills_dir, "tidy", GOOD.format(name="tidy", desc="Tidies."))

    assert _read(cfg.skills_dir).misfiled == ()


def test_a_nested_skill_is_judged_by_its_own_directory(cfg):
    """Not by the folder above it."""
    _skill(cfg.skills_dir, "research/lookup", GOOD.format(name="lookup", desc="Looks up."))

    assert _read(cfg.skills_dir).misfiled == ()


def test_a_nested_skill_can_be_misfiled_too(cfg):
    """And the report names the directory, not the reference -- it is the directory
    somebody has to rename.
    """
    _skill(cfg.skills_dir, "research/lookup", GOOD.format(name="finder", desc="Finds."))

    assert _read(cfg.skills_dir).misfiled == (("lookup", "finder"),)

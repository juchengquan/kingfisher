"""Two subagents called `surveyor`, from folders nobody coordinated."""

from __future__ import annotations

import re

import pytest

from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.definitions.subagents import GENERAL_PURPOSE
from kingfisher.core.definitions.subagents.catalogue import LocalSubagentRepository
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.plugins import backend_at
from tests.conftest import FakeToolCallingModel, subagents_dir

SPEC = """
name: {name}
description: Surveys things, the {vendor} way.
system_prompt: |
  Do the thing.
builtin_tools: [read_file]
tools: []
"""


def _two_vendors(cfg, *, name="surveyor"):
    for vendor in ("vendor", "team"):
        directory = subagents_dir(cfg) / vendor
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{name}.yaml").write_text(
            SPEC.format(name=name, vendor=vendor), encoding="utf-8"
        )


def _build(cfg, session_dir, subagents):
    return build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=subagents),
    )


# -- the catalogue keeps both ---------------------------------------------


def test_two_folders_may_each_define_one_name(cfg):
    """This took the whole catalogue down, not just the pair: `--list` returned an error
    and no run could start.
    """
    _two_vendors(cfg)

    assert sorted(LocalSubagentRepository(subagents_dir(cfg)).specs) == [
        "team/surveyor.yaml::surveyor",
        "vendor/surveyor.yaml::surveyor",
    ]


def test_a_unique_name_stays_flat(cfg):
    """Every catalogue without a clash is untouched, which is all of them."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "alone.yaml").write_text(
        SPEC.format(name="alone", vendor="only"), encoding="utf-8"
    )

    assert tuple(LocalSubagentRepository(subagents_dir(cfg)).specs) == ("alone",)


# -- the refusal, where the clash actually happens ------------------------


def test_activating_both_is_refused(cfg, session_dir):
    """The safety property, and the reason the catalogue can afford to keep both."""
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="would never run"):
        _build(
            cfg,
            session_dir,
            ("vendor/surveyor.yaml::surveyor", "team/surveyor.yaml::surveyor"),
        )


def test_the_refusal_names_both_files(cfg, session_dir):
    _two_vendors(cfg)

    with pytest.raises(CapabilityError) as raised:
        _build(
            cfg,
            session_dir,
            ("vendor/surveyor.yaml::surveyor", "team/surveyor.yaml::surveyor"),
        )

    assert "vendor/surveyor.yaml::surveyor" in str(raised.value)
    assert "team/surveyor.yaml::surveyor" in str(raised.value)


def test_a_bare_name_two_files_offer_is_refused(cfg, session_dir):
    """Told apart from activating both, because they are different mistakes: one is a
    caller who has not noticed there are two, the other is a caller who wants both
    and cannot have them.
    """
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="more than one source offers"):
        _build(cfg, session_dir, ("surveyor",))


def test_activating_every_subagent_is_refused_when_two_share_a_name(cfg, session_dir):
    """`*` cannot mean "both" here, and quietly meaning "one of them" is the failure
    this exists to stop.
    """
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="would never run"):
        _build(cfg, session_dir, "*")


def test_activating_one_of_them_works(cfg, session_dir):
    """The negative control: closing the hole must not close the door."""
    _two_vendors(cfg)

    _build(cfg, session_dir, ("team/surveyor.yaml::surveyor",))


def test_an_unknown_name_is_still_unknown(cfg, session_dir):
    """The distinction only exists if the other branch survives."""
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="unknown subagent"):
        _build(cfg, session_dir, ("nosuchthing",))


def test_two_different_names_are_not_a_clash(cfg, session_dir):
    """The rule is about the name a roster keys on, not about how many subagents a
    request activates.
    """
    _two_vendors(cfg)
    (subagents_dir(cfg) / "other.yaml").write_text(
        SPEC.format(name="other", vendor="third"), encoding="utf-8"
    )

    _build(cfg, session_dir, ("team/surveyor.yaml::surveyor", "other"))


# -- what the model is handed ---------------------------------------------


def test_the_activated_subagent_keeps_its_plain_name(cfg, session_dir):
    """A reference is how a *request* says which; the model reaches a subagent by
    `subagent_type`, and that stays `surveyor`.
    """
    _two_vendors(cfg)

    built = _build(cfg, session_dir, ("team/surveyor.yaml::surveyor",))

    named = [spec["name"] for spec in built.subagents or ()]

    assert "surveyor" in named, named
    assert "team/surveyor.yaml::surveyor" not in named, (
        "a reference reached the model, which reaches a subagent by this name"
    )


def test_subtracting_something_else_still_hits_the_clash(cfg, session_dir):
    """The reachable path the CLI actually has."""
    from kingfisher.core.access.capabilities import all_but

    _two_vendors(cfg)
    (subagents_dir(cfg) / "other.yaml").write_text(
        SPEC.format(name="other", vendor="third"), encoding="utf-8"
    )
    offered = tuple(LocalSubagentRepository(subagents_dir(cfg)).specs)

    with pytest.raises(CapabilityError, match="would never run"):
        _build(cfg, session_dir, all_but(("other",), offered=offered))


def test_subtracting_one_of_the_pair_leaves_a_workable_roster(cfg, session_dir):
    """And the way out: name which one to drop, and the rest activates."""
    from kingfisher.core.access.capabilities import all_but

    _two_vendors(cfg)
    offered = tuple(LocalSubagentRepository(subagents_dir(cfg)).specs)

    _build(cfg, session_dir, all_but(("vendor/surveyor.yaml::surveyor",), offered=offered))


# -- the name kingfisher's own helper answers to ------------------------------

#: A deployment's own `general-purpose`, in each way one can be declared.
TAKES_GENERAL_PURPOSE = {
    "yaml": ("general-purpose.yaml", SPEC.format(name="general-purpose", vendor="our")),
    "portable": (
        "ours.py",
        "SUBAGENTS = [{'name': 'general-purpose', 'description': 'd', 'system_prompt': 'Go.'}]\n",
    ),
    "compiled": (
        "ours.py",
        "SUBAGENTS = [{'name': 'general-purpose', 'description': 'd', "
        "'build': lambda model, tools: None}]\n",
    ),
}


@pytest.mark.parametrize("declared", list(TAKES_GENERAL_PURPOSE))
def test_a_subagent_taking_general_purpose_is_refused_at_load(cfg, declared):
    """It loaded, and every agent naming it, or `["*"]`, then died at build on deepagents'
    "Duplicate subagent name 'general-purpose'", which says nothing of the file to rename."""
    filename, text = TAKES_GENERAL_PURPOSE[declared]
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / filename).write_text(text, encoding="utf-8")

    with pytest.raises(SubagentError, match=rf"^{re.escape(filename)}: .*give this one another"):
        _ = LocalSubagentRepository(subagents_dir(cfg)).specs


def test_a_name_beside_general_purpose_still_loads(cfg):
    """The control: the refusal is the name, not a prefix or the file the name came from."""
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "general-purpose.yaml").write_text(
        SPEC.format(name="general-purpose-research", vendor="our"), encoding="utf-8"
    )

    assert set(LocalSubagentRepository(subagents_dir(cfg)).specs) == {"general-purpose-research"}


def test_the_reserved_name_is_the_one_deepagents_gives_its_helper():
    """deepagents renaming its helper would leave the refusal guarding a name nothing uses,
    and the duplicate-name crash back under whatever the helper is called now."""
    from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT

    assert GENERAL_PURPOSE_SUBAGENT["name"] == GENERAL_PURPOSE

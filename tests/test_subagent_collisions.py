"""Two subagents called `surveyor`, from folders nobody coordinated.

The last of the three. Skills got sources, tools got references, and this is
the same clash in the last place that still refused it outright -- which killed
the whole catalogue, not just the pair, and could not be fixed by anyone who
owned neither file.

Unlike tools, the refusal here is a plain refusal rather than a split grant.
The difference is what each axis defaults to: `subagents` activates nothing
unless asked, so a caller who never wanted two can never trip this. `tools`
defaults to everything, which is why that axis had to separate what a run may
draw on from what an agent carries.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.subagent_store import LocalSubagentRepository
from tests.conftest import FakeToolCallingModel, capture_build, subagents_dir

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
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(builtin_tools=("read_file", "task"), subagents=subagents),
    )


# -- the catalogue keeps both ---------------------------------------------


def test_two_folders_may_each_define_one_name(cfg):
    """This took the whole catalogue down, not just the pair: `--list` returned
    an error and no run could start."""
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
    """The safety property, and the reason the catalogue can afford to keep
    both. A roster is keyed by name: handing deepagents two subagents called
    `surveyor` compiles one, with no error and nothing saying which survived.
    """
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
    """Told apart from activating both, because they are different mistakes:
    one is a caller who has not noticed there are two, the other is a caller who
    wants both and cannot have them."""
    _two_vendors(cfg)

    with pytest.raises(CapabilityError, match="more than one source offers"):
        _build(cfg, session_dir, ("surveyor",))


def test_activating_every_subagent_is_refused_when_two_share_a_name(cfg, session_dir):
    """`*` cannot mean "both" here, and quietly meaning "one of them" is the
    failure this exists to stop. Cheap to refuse: `subagents` activates nothing
    by default, so only a caller who explicitly asked for everything sees it.
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
    """The rule is about the name a roster keys on, not about how many
    delegates a request activates."""
    _two_vendors(cfg)
    (subagents_dir(cfg) / "other.yaml").write_text(
        SPEC.format(name="other", vendor="third"), encoding="utf-8"
    )

    _build(cfg, session_dir, ("team/surveyor.yaml::surveyor", "other"))


# -- what the model is handed ---------------------------------------------


def test_the_activated_delegate_keeps_its_plain_name(cfg, session_dir, monkeypatch):
    """A reference is how a *request* says which; the model reaches a delegate
    by `subagent_type`, and that stays `surveyor`. Only one is ever activated,
    so there is nothing to tell apart at that end.
    """
    _two_vendors(cfg)
    captured = capture_build(monkeypatch)

    _build(cfg, session_dir, ("team/surveyor.yaml::surveyor",))

    named = [spec["name"] for spec in captured["subagents"]]

    assert "surveyor" in named, named
    assert "team/surveyor.yaml::surveyor" not in named, (
        "a reference reached the model, which reaches a delegate by this name"
    )


def test_subtracting_something_else_still_hits_the_clash(cfg, session_dir):
    """The reachable path the CLI actually has. `--subagents '*'` is library-only
    -- the driver never parses a bare `*` -- so a caller reaches "everything" by
    subtracting, and everything still contains both.
    """
    from kingfisher.domain.capabilities import all_but

    _two_vendors(cfg)
    (subagents_dir(cfg) / "other.yaml").write_text(
        SPEC.format(name="other", vendor="third"), encoding="utf-8"
    )
    offered = tuple(LocalSubagentRepository(subagents_dir(cfg)).specs)

    with pytest.raises(CapabilityError, match="would never run"):
        _build(cfg, session_dir, all_but(("other",), offered=offered))


def test_subtracting_one_of_the_pair_leaves_a_workable_roster(cfg, session_dir):
    """And the way out: name which one to drop, and the rest activates."""
    from kingfisher.domain.capabilities import all_but

    _two_vendors(cfg)
    offered = tuple(LocalSubagentRepository(subagents_dir(cfg)).specs)

    _build(cfg, session_dir, all_but(("vendor/surveyor.yaml::surveyor",), offered=offered))

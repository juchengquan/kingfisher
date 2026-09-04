"""Two rules that stopped applying when skills learned to live in folders.

Neither was found by reading the code. One came from a five-line probe, the
other from a CLI invocation nearly skipped because the reasoning said it was
fine -- the same way the `--list` contradiction and the `unloadable` blind spot
turned up while the folders work was still warm.

Both are about a check that was written when every skill sat at the root, and
that answered a question about the root long after that stopped being the whole
catalogue.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import CapabilityError, all_but
from kingfisher.skills import registry as skill_registry
from kingfisher.skills.catalogue import LocalSkillRepository

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nBody.\n"


def _skill(root, folder, name, desc="What it is for."):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")


def _read(root):
    return skill_registry.read(LocalSkillRepository(root), root=root)


# -- an upload may not take a name the catalogue already uses --------------


def test_a_foldered_skill_still_counts_as_taken(cfg):
    """`SkillRepository.names` lists the root and stops, so it answered `()` for
    a catalogue whose skills all lived in folders -- and the upload rule quietly
    stopped applying to every one of them."""
    _skill(cfg.skills_dir, "research/lookup", "lookup")

    assert LocalSkillRepository(cfg.skills_dir).names == (), "the old source, for contrast"
    assert _read(cfg.skills_dir).taken == ("lookup",)


def test_taken_answers_bare_names_where_names_spells_them_out(cfg):
    """Two different questions. `names` says what a request may *write*, so a
    colliding name is spelt out; `taken` says what is *spoken for*, and no
    upload will ever be called `research::lookup`."""
    for party in ("research", "legal"):
        _skill(cfg.skills_dir, f"{party}/lookup", "lookup")
    registry = _read(cfg.skills_dir)

    assert registry.names == ("legal::lookup", "research::lookup")
    assert registry.taken == ("lookup",)


def test_a_root_skill_is_taken_too(cfg):
    """The negative control: the case that always worked must keep working."""
    _skill(cfg.skills_dir, "flat", "flat")

    assert _read(cfg.skills_dir).taken == ("flat",)


def test_an_empty_catalogue_takes_nothing(cfg):
    cfg.skills_dir.mkdir(parents=True, exist_ok=True)

    assert _read(cfg.skills_dir).taken == ()


# -- subtracting a name that is no longer enough ---------------------------


def test_subtracting_an_ambiguous_name_says_which_ones(cfg):
    """It refused before this, so nothing unsafe happened -- but with the same
    sentence a genuinely absent name gets, which sends a reader hunting for a
    skill they can see in the listing printed underneath."""
    offered = ("code-review", "legal::lookup", "research::lookup")

    with pytest.raises(CapabilityError, match="more than one source offers it"):
        all_but(("lookup",), offered=offered)


def test_the_ambiguous_refusal_names_both_spellings():
    offered = ("legal::lookup", "research::lookup")

    with pytest.raises(CapabilityError) as raised:
        all_but(("lookup",), offered=offered)

    assert "legal::lookup" in str(raised.value)
    assert "research::lookup" in str(raised.value)


def test_a_genuinely_unknown_name_is_still_unknown():
    """The distinction only exists if the other branch survives -- one is a
    typo, the other is a name that stopped being enough."""
    with pytest.raises(CapabilityError, match="unknown name"):
        all_but(("nosuchthing",), offered=("legal::lookup", "research::lookup"))


def test_subtracting_a_qualified_name_works():
    assert all_but(("research::lookup",), offered=("legal::lookup", "research::lookup")) == (
        "legal::lookup",
    )


def test_an_unambiguous_bare_name_is_untouched():
    """Every catalogue without a collision subtracts exactly as it always did."""
    assert all_but(("lookup",), offered=("lookup", "code-review")) == ("code-review",)

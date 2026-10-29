"""`skill_locks.yaml`: who is told about each skill, said once, beside the definitions."""

from __future__ import annotations

import pytest

from kingfisher import CallerError, Kingfisher
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.skill_locks import SkillLockError
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, subagents_dir


def _skill(root, folder: str, name: str) -> None:
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Something to do.\n---\nDo it.\n", encoding="utf-8"
    )


def _locked(cfg, text: str) -> Definitions:
    """The catalogue, with `skill_locks.yaml` beside its skills folder."""
    (cfg.skills_dir.parent / "skill_locks.yaml").write_text(text, encoding="utf-8")
    return Definitions.from_config(cfg)


def test_a_lock_naming_no_skill_stops_startup(cfg):
    """A skill renamed under its lock would otherwise be told to everyone, silently."""
    _skill(cfg.skills_dir, "audit", "audit")

    with pytest.raises(SkillLockError, match=r"locks 'adit'.*unknown skill"):
        _locked(cfg, "- name: adit\n  source_ids: [A]\n").warm()


def test_a_lock_on_a_subagents_own_skill_is_sent_to_the_subagent(cfg):
    """A subagent's own skill reaches whoever reaches it, so a lock here could only fail,
    and "unknown skill" would send its author looking for a typo."""
    bundle = subagents_dir(cfg) / "stamps"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "stamps.yaml").write_text(
        "name: stamps\ndescription: Stamps.\nbuiltin_tools: [read_file]\n"
        "skills: [{name: iso-8601, source: bundled}]\nsystem_prompt: |\n  Stamp.\n",
        encoding="utf-8",
    )
    _skill(bundle / "skills", "iso-8601", "iso-8601")

    with pytest.raises(SkillLockError, match="subagent 'stamps''s own folder"):
        _locked(cfg, "- name: iso-8601\n  source_ids: [A]\n").warm()


def test_a_bare_name_two_folders_share_is_refused_rather_than_picked(cfg):
    """Picking one would leave the other's namesake told to everyone."""
    for party in ("research", "legal"):
        _skill(cfg.skills_dir, f"{party}/lookup", "lookup")

    with pytest.raises(SkillLockError, match="write legal::lookup, research::lookup"):
        _locked(cfg, "- name: lookup\n  source_ids: [A]\n").warm()


def test_one_skill_locked_under_both_spellings_is_refused(cfg):
    """Two locks on one skill are two answers, and only the author knows which they meant."""
    _skill(cfg.skills_dir, "audit", "audit")

    with pytest.raises(SkillLockError, match="'audit' and 'catalogue::audit' are one skill"):
        _locked(
            cfg,
            "- name: audit\n  source_ids: [A]\n- name: catalogue::audit\n  source_ids: [B]\n",
        ).warm()


def test_a_name_locked_twice_shows_both_and_picks_neither(cfg):
    """What two agents that disagreed leave behind once both are pasted in."""
    with pytest.raises(SkillLockError, match=r"locked twice, to \[A\] and to \[A, B\]"):
        _locked(
            cfg, "- name: audit\n  source_ids: [A]\n- name: audit\n  source_ids: [A, B]\n"
        ).warm()


@pytest.mark.parametrize(
    ("written", "refused"),
    [
        ("- name: audit\n", "locks it to nobody in particular"),
        ('- name: audit\n  source_ids: ["*"]\n', "locks it to nobody in particular"),
        ("- name: audit\n  source_ids: []\n", "Leave the line out"),
        ("- name: audit\n  sorce_ids: [A]\n", "did you mean 'source_ids'"),
        ("- name: audit\n  source_ids: [42]\n", "42 is not a name"),
        ("audit: {source_ids: [A]}\n", "this file is a list"),
    ],
    ids=["no-lock", "star", "empty", "typo", "number", "mapping"],
)
def test_a_lock_that_cannot_be_read_as_one_is_refused(cfg, written, refused):
    """Each read as no lock would leave the skill told to everyone its author meant to
    keep it from."""
    with pytest.raises(SkillLockError, match=refused):
        _locked(cfg, written).warm()


def test_a_skill_lock_alone_makes_every_call_say_who_is_calling(cfg):
    """Left out of the vocabulary, a deployment whose only lock is a skill's would run
    every call unscoped, and the locked skill would be told to everyone."""
    _skill(cfg.skills_dir, "audit", "audit")
    an_agent(cfg, builtin_tools="[read_file]", skills="[audit]")
    (cfg.skills_dir.parent / "skill_locks.yaml").write_text(
        "- name: audit\n  source_ids: [A]\n", encoding="utf-8"
    )

    with pytest.raises(CallerError, match="source_ids="):
        Kingfisher(cfg, sessions=LocalSessions()).run("anything")


def test_no_file_is_no_lock(cfg):
    """The control: a deployment with no `skill_locks.yaml` starts as it always did."""
    _skill(cfg.skills_dir, "audit", "audit")

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.locked_skills == {}
    assert catalogue.vocabulary is None


@pytest.mark.parametrize("broken", [False, True], ids=["control", "broken"])
def test_a_caller_is_shown_no_skill_while_the_locks_will_not_load(cfg, broken):
    """Read as no locks, a file startup refuses would show a caller the very skills it was
    written to keep from them."""
    from kingfisher.core.inventory import inventory

    for name in ("audit", "review"):
        _skill(cfg.skills_dir, name, name)
    an_agent(cfg, source_ids="[A, B]", builtin_tools="[read_file]", skills="[audit, review]")
    stray = "- name: nowhere\n  source_ids: [A]\n" if broken else ""
    _locked(cfg, "- name: audit\n  source_ids: [A]\n" + stray)

    shown = inventory(cfg, source_ids=["B"]).skills

    assert set(shown) == (set() if broken else {"review"})

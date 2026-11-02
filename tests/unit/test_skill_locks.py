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


def test_a_skill_locked_to_a_datazone_is_told_only_inside_it(cfg):
    """A lock's datazone dropped would tell the skill to every caller holding its source
    ids, in every zone."""
    from kingfisher.core.access.checks import caller_holds
    from kingfisher.core.access.source_ids import with_datazone

    _skill(cfg.skills_dir, "audit", "audit")
    roots = _locked(cfg, "- name: audit\n  source_ids: [A]\n  datazone: emea\n")

    def shut(zone: str | None) -> frozenset[str]:
        return roots.skills_shut(caller_holds(roots.vocabulary, with_datazone(["A"], zone)))

    # The control: inside the zone, the skill is told.
    assert shut("emea") == frozenset()
    assert shut(None) and shut("apac")


def test_a_refusal_names_no_skill_the_callers_locks_shut(scripted):
    """An agent offering every skill refused a request naming one that does not exist by
    listing every skill the definitions offer, the ones a lock keeps from this caller
    among them."""
    from dataclasses import replace

    from kingfisher import Capabilities, CapabilityError, Request
    from tests.conftest import EVERY_TOOL

    cfg = replace(scripted, skills_enabled=True)
    for name in ("audit", "plain"):
        _skill(cfg.skills_dir, name, name)
    _locked(cfg, "- name: audit\n  source_ids: [A]\n")
    an_agent(cfg, skills='["*"]', **EVERY_TOOL)
    asked = Request("go", agent="only", capabilities=Capabilities(skills=("ghost",)))

    with pytest.raises(CapabilityError) as refused:
        Kingfisher(cfg, sessions=LocalSessions()).sent(asked, source_ids=["B"])

    # The control: it still names what this caller may ask for.
    assert "plain" in str(refused.value)
    assert "audit" not in str(refused.value)


@pytest.mark.parametrize("caller", ["A", "B"], ids=["control", "shut-out"])
def test_a_skill_a_lock_shuts_is_refused_to_read_file(cfg, session_dir, caller):
    """Denials worked out from the skills a caller may be told of, rather than from every
    skill, would leave a locked skill readable by `read_file` to the caller it shuts out."""
    from tests.unit.test_capability_wiring import _reads_a_skill

    (cfg.skills_dir.parent / "skill_locks.yaml").write_text(
        "- name: tabular-qa\n  source_ids: [A]\n", encoding="utf-8"
    )

    read = _reads_a_skill(cfg, session_dir, held=frozenset({caller}))

    assert ("permission denied" in read) is (caller == "B"), read


@pytest.mark.parametrize(
    ("written", "verdict", "said"),
    [
        (None, "ok", "none: no skill_locks.yaml"),
        ("- name: audit\n  source_ids: [A]\n", "ok", "1, in "),
        ("- name: [audit\n", "fail", "cannot read it"),
    ],
    ids=["no-file", "one-lock", "unreadable"],
)
def test_doctor_says_how_many_skills_are_locked_and_where(cfg, written, verdict, said):
    """The file sits apart from the skills it locks, so a deploy that leaves it behind
    opens every one of them and starts as before; a doctor saying nothing of it leaves
    that to be found by a caller shown a skill they should not be."""
    from kingfisher.cli.health import examine

    _skill(cfg.skills_dir, "audit", "audit")
    if written is not None:
        (cfg.skills_dir.parent / "skill_locks.yaml").write_text(written, encoding="utf-8")

    (check,) = [one for one in examine(cfg) if one.name == "skill locks"]

    assert (check.verdict, said in check.detail) == (verdict, True), check

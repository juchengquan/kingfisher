"""Two skills called `lookup`, from two parties who never met."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from kingfisher.core.access.capabilities import Capabilities, CapabilityError
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.backend import skills_sources
from kingfisher.core.agent.middlewares.narrowing import NarrowedSkillsMiddleware
from kingfisher.core.definitions.agents.spec import AgentError
from kingfisher.core.definitions.catalogue import Definitions
from kingfisher.core.definitions.skills import registry as skill_registry
from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import FakeToolCallingModel, an_agent

SKILL = "---\nname: {name}\ndescription: {desc}\n---\nBody of the skill.\n"


def _skill(root, folder, name, desc):
    directory = root / folder
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "SKILL.md").write_text(SKILL.format(name=name, desc=desc), encoding="utf-8")


def _two_parties(cfg):
    for party in ("research", "legal"):
        _skill(cfg.skills_dir, f"{party}/lookup", "lookup", f"The {party} way.")
    return skill_registry.read(LocalSkillRepository(cfg.skills_dir))


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
    return skill_registry.read(LocalSkillRepository(cfg.skills_dir))


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

    wired = replace(cfg, skills_enabled=True)
    build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("research::lookup",)),
    )


def test_a_request_naming_it_bare_is_refused_at_build(cfg, session_dir):
    _two_parties(cfg)

    with pytest.raises(CapabilityError, match="more than one source"):
        wired = replace(cfg, skills_enabled=True)
        build_agent(
            wired,
            backend=backend_at(wired, session_dir),
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


def test_a_broken_skill_is_reported_though_another_source_shares_its_name(cfg):
    """The case this whole file is about, and the one that hid a failure completely.

    `unloadable` asked whether any path kept *in this root* contained `/<name>/`, and
    every source under one root shares that list -- so `research/lookup` loading said
    that a broken `legal/lookup` had come back. Measured before this existed: no
    `unloadable` line, `doctor` green, and a grant of `legal::lookup` refused with "no
    skill 'lookup' in 'legal'" and nothing anywhere saying why.
    """
    _skill(cfg.skills_dir, "research/lookup", "lookup", "The research way.")
    (cfg.skills_dir / "legal" / "lookup").mkdir(parents=True)
    (cfg.skills_dir / "legal" / "lookup" / "SKILL.md").write_text(
        "---\nname: lookup\n---\nNo description.\n", encoding="utf-8"
    )

    registry = _read(cfg)

    assert registry.unloadable == ("legal/lookup",)
    assert registry.names == ("lookup",), "and the one that did load is still offered"


def test_two_sources_that_both_load_are_reported_as_missing_nothing(cfg):
    """The control the rule above needs: comparing by path rather than by name must not
    start naming a skill that is plainly there.
    """
    registry = _two_parties(cfg)

    assert registry.unloadable == ()
    assert registry.names == ("legal::lookup", "research::lookup")


# -- one skill, two spellings ----------------------------------------------

#: `audit` narrowed to A, and the same skill again qualified, under a line reaching A or B.
BOTH_SPELLINGS = (
    "source_ids: [A, B]\nbuiltin_tools: [read_file]\n"
    "skills:\n  - name: audit\n    source_ids: [A]\n  - catalogue::audit\n"
)


def _written(cfg, kind, body):
    """One definition of `kind` carrying `body`, beside a skill called `audit`."""
    _skill(cfg.skills_dir, "audit", "audit", "Audit the books.")
    directory = cfg.catalogue_roots["agents" if kind == "agent" else "subagents"]
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{kind}.yaml").write_text(
        f"name: {kind}\ndescription: d\n{body}system_prompt: |\n  You do it.\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(("kind", "error"), [("agent", AgentError), ("subagent", SubagentError)])
def test_one_skill_written_bare_and_qualified_is_refused(cfg, kind, error):
    """Both entries loaded and resolved to one skill, so the one with no audience cancelled
    the narrower one's: a caller holding only B was offered a skill written for A, and the
    access report said nothing.
    """
    _written(cfg, kind, BOTH_SPELLINGS)

    with pytest.raises(error, match=r"names 'audit' and 'catalogue::audit', which are one"):
        Definitions.from_config(cfg).warm()


def test_two_skills_sharing_a_bare_name_are_still_two_entries(cfg):
    """The control: the check compares what a name means, not how it reads, so the two
    parties' `lookup` in one list stay two skills.
    """
    _two_parties(cfg)
    an_agent(
        cfg,
        "boss",
        builtin_tools="[read_file]",
        skills="[research::lookup, legal::lookup]",
    )

    warmed = Definitions.from_config(cfg).warm()

    assert warmed.agents.specs["boss"].skills == ("research::lookup", "legal::lookup")


@pytest.mark.parametrize("skills", ["[nope, gone]", "[lookup, gone]"], ids=["unknown", "ambiguous"])
def test_two_names_meaning_no_one_skill_are_not_one_skill(cfg, skills):
    """Two entries each meaning no single skill were both keyed under `None`, so startup
    refused the deployment as "one skill (None)" -- a reason that names nothing, where the
    build would have said which skill was unknown or could be either.
    """
    _two_parties(cfg)
    an_agent(cfg, "boss", builtin_tools="[read_file]", skills=skills)

    warmed = Definitions.from_config(cfg).warm()

    assert warmed.agents.specs["boss"].skills == tuple(skills.strip("[]").split(", "))


# -- the two loaders, which do not delegate --------------------------------


def test_a_reload_request_is_not_read_as_already_loaded(cfg, session_dir):
    """deepagents puts `skills_metadata` in the graph's input schema and documents
    `None` there as a request to load the skills again -- for a skill added since the
    last turn. Asked with `in`, that reads as "already loaded", so the load is skipped
    and `modify_request`'s `state.get(...) or []` hands a request that activated skills
    an empty index.

    Both spellings, and a loaded index beside them: every call site in this suite
    passes `{}`, where the branch is not reached at all.
    """
    _two_parties(cfg)
    backend = backend_at(replace(cfg, skills_enabled=True), session_dir)
    middleware = NarrowedSkillsMiddleware(
        allowed=("research::lookup",),
        backend=backend,
        sources=skills_sources(("research", "legal")),
    )

    first_load = middleware.before_agent({}, None, {})
    asked_again = middleware.before_agent({"skills_metadata": None}, None, {})
    asked_again_async = asyncio.run(middleware.abefore_agent({"skills_metadata": None}, None, {}))
    already = middleware.before_agent({"skills_metadata": [{"name": "lookup"}]}, None, {})

    assert first_load is not None and first_load["skills_metadata"], "nothing loaded at all"
    for loaded in (asked_again, asked_again_async):
        assert loaded is not None, "a reload request was read as already loaded"
        assert loaded["skills_metadata"] == first_load["skills_metadata"]
    assert already is None, "an index that is there was loaded a second time"


def test_the_async_loader_agrees_with_the_sync_one(cfg, session_dir):
    """`before_agent` and `abefore_agent` each build their own dictionary -- neither
    calls the other -- so overriding one leaves the other collapsing.

    It fails *open* -- a synchronous run offers both skills and a run on an event loop
    silently offers one -- and "it delegates" is the assumption that let the shell
    sandbox nest itself twice while thirteen tests passed.
    """
    _two_parties(cfg)
    backend = backend_at(replace(cfg, skills_enabled=True), session_dir)
    middleware = NarrowedSkillsMiddleware(
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
    backend = backend_at(replace(cfg, skills_enabled=True), session_dir)
    middleware = NarrowedSkillsMiddleware(
        allowed=("research::lookup",),
        backend=backend,
        sources=skills_sources(("research", "legal")),
    )

    loaded = middleware.before_agent({}, None, {})["skills_metadata"]
    shown = middleware._format_skills_list(loaded)

    assert "The research way." in shown
    assert "The legal way." not in shown


# -- the boundary that was failing open ------------------------------------


def test_a_nested_skill_is_denied_at_the_path_it_actually_has(cfg, session_dir):
    """`skill_denials` wrote `/skills/{name}/**`, which is where a skill sits only
    while every skill sits at the top level.
    """
    _two_parties(cfg)

    wired = replace(cfg, skills_enabled=True)
    built = build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(skills=("research::lookup",)),
    )

    # Minus `.harness`, which every request is denied whatever it activates.
    denied = [
        p
        for r in built.permissions
        if r.mode == "deny" and "read" in r.operations
        for p in r.paths
        if p != "/.harness/**"
    ]

    assert "/skills/legal/lookup/**" in denied
    assert "/skills/research/lookup/**" not in denied, "the activated one must stay readable"
    assert all(p.startswith("/skills/") for p in denied), "every rule must sit under a route"

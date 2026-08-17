"""Where a deployment's definitions are read from, and who gets to decide.

The catalogue used to be three hardcoded reads of `Config`, one each in
`available_skills`, `defined_subagents` and the tool loader. It is now one
mapping settled at construction, so a deployment that stages its definitions
somewhere else has one place to say so -- and so `--list`, the upload collision
check and the agent cannot end up reading three different answers.
"""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from kingfisher.application.service import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.ports import SubagentRepository
from kingfisher.domain.request import Request
from kingfisher.domain.subagent import SubagentError, SubagentSpec
from kingfisher.infrastructure.catalogue import Catalogue, resolve_catalogue
from kingfisher.infrastructure.harness.agent import (
    available_skills,
    build_agent,
    defined_subagents,
    workspace_tool_names,
)
from kingfisher.infrastructure.harness.backend import SKILLS_ROUTE, build_backend
from tests.conftest import FakeToolCallingModel, capture_build

SUBAGENT = """name: reviewer
description: Checks an analysis for arithmetic errors.
system_prompt: |
  You review analyses.

"""

TOOL = """from langchain_core.tools import tool


@tool
def elsewhere(x: int) -> int:
    \"\"\"A tool that only the staged catalogue defines.\"\"\"
    return x


TOOLS = [elsewhere]
"""

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)


def _staged(root, *, skill=None, subagent=None, tool=None):
    """A catalogue laid out somewhere that is not a workspace.

    Returns the `Catalogue`, and `_roots` gives the directories back for a test
    that needs to write into them.
    """
    roots = {kind: root / kind for kind in ("skills", "subagents", "tools")}
    for path in roots.values():
        path.mkdir(parents=True, exist_ok=True)
    if skill is not None:
        (roots["skills"] / skill).mkdir()
        (roots["skills"] / skill / "SKILL.md").write_text(
            f"---\nname: {skill}\ndescription: A skill.\n---\nDo the thing.\n", encoding="utf-8"
        )
    if subagent is not None:
        (roots["subagents"] / "reviewer.yaml").write_text(subagent, encoding="utf-8")
    if tool is not None:
        (roots["tools"] / "extra.py").write_text(tool, encoding="utf-8")
    return Catalogue.from_roots(roots)


def _roots(catalogue):
    """The three directories behind a locally-backed catalogue."""
    return {kind: getattr(catalogue, kind).root for kind in ("skills", "subagents", "tools")}


def test_omitted_it_is_the_three_directories_config_names(cfg):
    """The fallback, and the whole reason 45 call sites did not have to change.

    `build_agent` derives from `cfg` or raises but never invents, which is the
    rule `model=` already followed. Catalogue roots have a `cfg`-derived answer,
    so this is that rule and not an exception to it.
    """
    assert resolve_catalogue(cfg) == Catalogue.from_roots(
        {"skills": cfg.skills_dir, "subagents": cfg.subagents_dir, "tools": cfg.tools_dir}
    )


def test_relocated_directories_are_created_rather_than_silently_empty(tmp_path, cfg):
    """The gap this closes, and it predates the feature.

    `build_backend` created `skills_dir` and only that one. Point
    `KINGFISHER_SUBAGENTS_DIR` or `KINGFISHER_TOOLS_DIR` at somewhere that does
    not exist yet and nothing created it and nothing said so: `load_all` and
    `load_tools` both read a missing directory as an empty one, so the
    deployment started cleanly with a catalogue it had configured and did not
    get.
    """
    elsewhere = tmp_path / "elsewhere"
    relocated = replace(
        cfg,
        skills_root=elsewhere / "s",
        subagents_root=elsewhere / "a",
        tools_root=elsewhere / "t",
    )
    assert not (elsewhere / "a").exists()

    roots = resolve_catalogue(relocated)

    assert all(path.is_dir() for path in _roots(roots).values())


def test_a_supplied_catalogue_must_already_exist(tmp_path, cfg):
    """Creating one would hide the failure it is there to surface.

    A derived root is kingfisher's own, so making it is repair. A supplied one
    was staged by whoever supplied it, and an absent one most likely means the
    staging is what went wrong -- so creating it would turn a fetch that failed
    into an agent quietly told about no skills at all.
    """
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError, match="not a directory"):
        resolve_catalogue(
            cfg,
            {"skills": missing, "subagents": missing, "tools": missing},
        )
    assert not missing.exists()


def test_a_supplied_catalogue_names_all_three(tmp_path, cfg):
    """Leaving one out would mean an empty one, which is never what was meant."""
    roots = _staged(tmp_path / "staged")
    with pytest.raises(ConfigError, match="missing tools"):
        staged = _roots(roots)
        resolve_catalogue(cfg, {"skills": staged["skills"], "subagents": staged["subagents"]})


def test_the_agent_reads_the_supplied_catalogue_and_not_the_workspace(tmp_path, cfg):
    """Supplied roots replace the configured ones; they do not add to them.

    Every function downstream assumes one root per kind -- `load_tools` takes a
    directory, `_skill_denials` emits against one route -- so a deployment that
    wants both merges them itself and decides its own collision rule.
    """
    (cfg.skills_dir / "in-the-workspace").mkdir(parents=True)
    (cfg.skills_dir / "in-the-workspace" / "SKILL.md").write_text(
        "---\nname: in-the-workspace\ndescription: A skill.\n---\nDo the thing.\n",
        encoding="utf-8",
    )
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT, tool=TOOL)

    assert available_skills(cfg, None, catalogue=roots) == ("staged-only",)
    assert tuple(defined_subagents(cfg, None, catalogue=roots)) == ("reviewer",)
    assert workspace_tool_names(cfg, catalogue=roots) == ("elsewhere",)

    # And the configured one is still what is read when nothing is supplied.
    assert available_skills(cfg, None) == ("in-the-workspace",)


def test_the_skills_route_follows_the_catalogue(tmp_path, cfg, session_dir):
    """The file tools have to reach what the listing advertised.

    Skills are not read by kingfisher; they are read by the agent, through this
    route. A catalogue that moved the listing without moving the route would
    advertise a skill and then fail to open it.
    """
    roots = _staged(tmp_path / "staged", skill="staged-only")
    backend = build_backend(cfg, session_dir, catalogue=roots)

    routed = backend.routes[SKILLS_ROUTE]

    assert str(routed.cwd) == str(roots.skills.root.resolve())
    assert backend.read(f"{SKILLS_ROUTE}staged-only/SKILL.md")


@macos
def test_the_shell_reaches_a_supplied_catalogue(cfg, session_dir):
    """The other half of the same answer, and the half a route check cannot see.

    `execute` bypasses tool-level permissions entirely, so the sandbox profile
    decides whether the shell can read a skill at all, and `$KINGFISHER_SKILLS`
    is how a skill's own scripts address the catalogue they live in. Both used
    to come off `cfg` while the route followed the catalogue -- a split view
    rather than a refusal, of exactly the kind `readable_roots` documents
    already having caused once.

    Staged under the operator's home on purpose. The profile denies the home and
    re-allows what has to stay readable, so anywhere else is readable regardless
    and would prove nothing: this fails if the grant names the configured
    directory instead of the supplied one, and a catalogue in `/tmp` would not.
    """
    probe = Path.home() / "kingfisher-supplied-catalogue-probe"
    roots = _staged(probe)
    (roots.skills.root / "demo").mkdir()
    (roots.skills.root / "demo" / "run.sh").write_text("echo from-the-supplied-catalogue\n")
    try:
        backend = build_backend(cfg, session_dir, catalogue=roots)

        result = backend.execute('sh "$KINGFISHER_SKILLS/demo/run.sh"')

        assert result.exit_code == 0, f"the shell cannot reach it: {result.output}"
        assert "from-the-supplied-catalogue" in str(result.output)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_the_service_settles_it_once_and_hands_it_down(tmp_path, cfg):
    """Resolved at construction, not per request.

    A deployment that fetches its catalogue pays for that once per `Kingfisher`
    rather than once per turn -- and a catalogue that cannot be read fails at
    startup, which is what `Kingfisher` already promises about a broken
    workspace or an unreachable state directory.
    """
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT)

    service = Kingfisher(cfg, catalogue=roots)

    assert service.catalogue == roots


def test_a_broken_catalogue_fails_at_startup(tmp_path, cfg):
    """Rather than on the first turn, when a caller is already waiting."""
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError):
        Kingfisher(cfg, catalogue={"skills": missing, "subagents": missing,
                                         "tools": missing})


def test_a_delegate_is_activated_from_the_supplied_catalogue(tmp_path, cfg, monkeypatch,
                                                             session_dir):
    """The subagent half, through `build_agent` rather than beside it.

    `_activated_subagents` resolves what a request wired *before* the tools,
    because whether a definition names one decides if the tool probe runs. It
    reads the catalogue to do that, so it needs the same one everything else
    got -- and a request activating a delegate the staged catalogue defines is
    the only thing that shows it did.
    """
    roots = _staged(tmp_path / "staged", subagent=SUBAGENT)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(subagents=("reviewer",)),
        catalogue=roots,
    )

    names = [spec["name"] for spec in captured["subagents"]]
    assert "reviewer" in names, "the staged catalogue's delegate was not wired"


def test_the_agent_it_builds_offers_the_staged_definitions(tmp_path, cfg, session_dir):
    """End to end: what the service resolved is what the graph was built from."""
    roots = _staged(tmp_path / "staged", skill="staged-only")
    enabled = replace(cfg, skills_enabled=True)

    graph = build_agent(
        enabled,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        catalogue=roots,
    )

    assert graph is not None
    assert available_skills(enabled, session_dir, catalogue=roots) == ("staged-only",)


# -- the type itself ------------------------------------------------------


def test_the_three_directories_are_attributes_not_keys(cfg):
    """A string key that is wrong is a `KeyError` at runtime, and in this
    codebase that surfaces as an empty catalogue -- the silent emptiness these
    modules keep refusing. An attribute that is wrong is a type error before it
    runs.
    """
    catalogue = Catalogue.from_config(cfg)

    assert catalogue.skills.root == cfg.skills_dir
    assert catalogue.subagents.root == cfg.subagents_dir
    assert catalogue.tools.root == cfg.tools_dir
    assert not hasattr(catalogue, "__getitem__"), "indexing would let both idioms survive"


def test_resolving_accepts_one_that_is_already_resolved(tmp_path, cfg):
    """A deployment stages directories and hands over a mapping, which is the
    documented seam. Something already holding a `Catalogue` -- another
    kingfisher, a test fixture -- should not have to take it apart to pass it
    back. The fixture in this file hit exactly that.
    """
    staged = _staged(tmp_path / "staged")

    assert resolve_catalogue(cfg, staged) == staged


def test_a_resolved_one_is_still_checked(tmp_path, cfg):
    """Accepting the type is not accepting it unread. A supplied catalogue is
    staged by whoever supplies it, so a directory that is not there is a staging
    failure and has to say so however it arrived.
    """
    missing = tmp_path / "never-staged"
    handed = Catalogue.from_roots({"skills": missing, "subagents": missing, "tools": missing})

    with pytest.raises(ConfigError, match="not a directory"):
        resolve_catalogue(cfg, handed)


# -- the catalogue reads once ---------------------------------------------


def test_the_catalogue_reads_each_kind_once_not_once_per_turn(cfg, monkeypatch):
    """A deployment's definitions are static, so reading them per turn was work
    every turn paid for nothing. Measured before building: 4ms per turn at five
    of each kind, 81ms at a hundred.

    Counted through the modules that actually bind the name, not just the
    store's. Two do, and they are the two halves this is about: `catalogue.py`
    builds the deployment's repository once, and `layered.py` builds the
    session's per turn. Patching only one measured nothing and reported a clean
    zero, which is what this originally did.

    Counted on the *read* and not on construction: a repository is cheap to make
    and holds only a path, so what this is about is the walk-and-parse behind
    `specs`.

    The stub caches, because the real one does and that is now where the
    guarantee lives. `Catalogue` used to hold the cache itself; it holds
    repositories instead, so reading once is something they do and this is what
    says so. Written without the cache, this measured 7 reads rather than 4 --
    one per turn for the catalogue on top of one per turn for the session.
    """
    from functools import cached_property

    from kingfisher.infrastructure import catalogue as catalogue_module
    from kingfisher.infrastructure import layered as layered_module
    from kingfisher.infrastructure.subagent_store import LocalSubagentRepository
    from tests.test_run import StubAgent

    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "a.yaml").write_text(
        "name: alpha\ndescription: A.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )

    reads = []

    class Counting(LocalSubagentRepository):
        @cached_property
        def specs(self):
            reads.append(self.root)
            return LocalSubagentRepository(self.root).specs

    monkeypatch.setattr(catalogue_module, "LocalSubagentRepository", Counting)
    monkeypatch.setattr(layered_module, "LocalSubagentRepository", Counting)

    service = Kingfisher(cfg, agent=StubAgent("ok"))
    at_construction = len(reads)
    for _ in range(3):
        service.run(Request("go"))

    assert at_construction == 1, "the catalogue was not read when the service was wired"
    # One per turn remains, and it is the session's own uploads -- those arrive
    # per request and cannot be read in advance.
    assert len(reads) - at_construction == 3
    assert all("sessions" in str(d) for d in reads[at_construction:])


def test_a_definition_written_after_wiring_is_not_this_deployments(cfg):
    """The cost of reading once, stated as behaviour rather than left to be
    discovered. A dev loop gets the old behaviour by building a new service,
    which is what `--seed-assets` then running already does."""
    from tests.test_run import StubAgent

    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    service = Kingfisher(cfg, agent=StubAgent("ok"))

    (cfg.subagents_dir / "late.yaml").write_text(
        "name: late\ndescription: Written afterwards.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )

    assert "late" not in service.catalogue.subagents.specs
    assert "late" in Kingfisher(cfg, agent=StubAgent("ok")).catalogue.subagents.specs


def test_listing_still_survives_a_definition_that_will_not_load(cfg):
    """`--list` is run *because* something is wrong, so it must not be the thing
    that dies. Warming belongs to `Kingfisher`, not to `resolve_catalogue`, and
    a test caught the first version doing it in the wrong place.
    """
    (cfg.subagents_dir).mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "broken.yaml").write_text("name: x\nnonsense: 1\n", encoding="utf-8")

    catalogue = resolve_catalogue(cfg)  # must not raise

    with pytest.raises(SubagentError):
        _ = catalogue.subagents.specs


# -- a deployment's own repository ----------------------------------------


@dataclass(frozen=True)
class InMemorySubagents:
    """A subagent store with no directory anywhere behind it.

    The shape a deployment reaches for when its definitions live in a database
    or arrive over a wire. It satisfies `SubagentRepository` by having the two
    members and nothing else -- no base class, no registration.
    """

    held: dict

    @property
    def names(self):
        return tuple(self.held)

    @property
    def specs(self):
        return self.held


def _spec(name):
    return SubagentSpec(
        name=name, description="Supplied from memory.", system_prompt="You are supplied."
    )


def test_one_kind_can_be_swapped_without_touching_the_other_two(tmp_path, cfg):
    """What the object bought. `Catalogue` is frozen, so exchanging a single
    seam is `replace` and the other two keep whatever they were.
    """
    staged = _staged(tmp_path / "staged", skill="staged-only")
    swapped = replace(staged, subagents=InMemorySubagents({"from-memory": _spec("from-memory")}))

    assert isinstance(swapped.subagents, SubagentRepository)
    assert swapped.subagents.names == ("from-memory",)
    # untouched, and still reading the directory they were staged in
    assert swapped.skills is staged.skills
    assert swapped.skills.names == ("staged-only",)


def test_a_supplied_repository_needs_no_directory_to_be_accepted(cfg):
    """The check `resolve_catalogue` makes is about *staging*, and staging is
    something only a directory-backed store does. A repository holding its
    definitions elsewhere has no root that could be missing, so demanding one
    would refuse exactly the deployments the ports exist for.
    """
    handed = replace(
        Catalogue.from_config(cfg), subagents=InMemorySubagents({"x": _spec("x")})
    )

    assert resolve_catalogue(cfg, handed) is handed


def test_the_agent_is_built_from_a_supplied_repository(cfg, session_dir):
    """End of the chain, and the assertion that matters: nothing between the
    port and the graph knows which kind of store answered.
    """
    catalogue = replace(
        Catalogue.from_config(cfg), subagents=InMemorySubagents({"ghost": _spec("ghost")})
    )

    defined = defined_subagents(cfg, session_dir, catalogue=catalogue)

    assert "ghost" in defined
    assert defined["ghost"].system_prompt == "You are supplied."


def test_a_skills_store_with_no_directory_is_mounted_from_what_it_holds(cfg, session_dir):
    """This used to be a refusal. `SkillRepository.files` ended it: a repository
    that can hand over bytes is mountable whatever it is backed by, so a store
    with nothing on disk now builds a backend the agent can read.

    The refusal was honest while the port answered only with names -- a route
    needs file contents and a name cannot supply them -- but it was a limit of
    the port, not of the route, and it read as a limit of the design.
    """
    from kingfisher.infrastructure.harness.backend import SKILLS_ROUTE

    @dataclass(frozen=True)
    class Nowhere:
        @property
        def names(self):
            return ("imaginary",)

        def files(self, name):
            if name != "imaginary":
                raise KeyError(name)
            return {"SKILL.md": "---\nname: imaginary\ndescription: d\n---\n\nbody\n"}

    catalogue = replace(Catalogue.from_config(cfg), skills=Nowhere())

    backend = build_backend(cfg, session_dir, catalogue=catalogue)

    assert "body" in str(backend.read(f"{SKILLS_ROUTE}imaginary/SKILL.md"))


def test_the_other_two_kinds_need_no_directory_at_all(cfg, session_dir):
    """The refusal above is about skills specifically, and it would be a bad
    outcome if it quietly generalised: subagents are documents kingfisher parses
    and tools are modules it imports, so neither reaches the agent through a
    route. A catalogue whose subagents live in memory builds a backend fine.
    """
    catalogue = replace(
        Catalogue.from_config(cfg), subagents=InMemorySubagents({"x": _spec("x")})
    )

    assert build_backend(cfg, session_dir, catalogue=catalogue) is not None


def test_a_definition_that_will_not_parse_fails_at_startup_too(cfg):
    """The other half of "fails at startup", and the half `warm` is for.

    The test above stages a directory that is not there, which `resolve_catalogue`
    refuses before anything is read. This is a directory that exists holding a
    definition that does not parse -- nothing refuses that except reading it, so
    without `warm` the failure waits for the first turn, with a caller on the
    other end of it.

    Mutation-tested: emptying `warm` leaves this the only test that notices.
    """
    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    (cfg.subagents_dir / "broken.yaml").write_text(
        "name: broken\ndescription: A.\nsystem_prompt: |\n  x\nnonsense_field: true\n",
        encoding="utf-8",
    )

    with pytest.raises(SubagentError):
        Kingfisher(cfg, agent=None)

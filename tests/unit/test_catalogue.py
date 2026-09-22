"""Where a deployment's definitions are read from, and who gets to decide."""

from __future__ import annotations

import platform
import shutil
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from kingfisher.application import service as service_module
from kingfisher.application.service import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.domain.ports import SubagentRepository
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.infrastructure.harness.activation import (
    available_skills,
    defined_subagents,
)
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import default_backend
from kingfisher.infrastructure.harness.tools import workspace_tool_names
from kingfisher.kinds.middlewares.catalogue import MiddlewareError
from kingfisher.kinds.skills.catalogue import reachable
from kingfisher.kinds.subagents.spec import SubagentError, SubagentSpec
from kingfisher.layout import SKILLS_ROUTE
from tests.conftest import (
    FakeToolCallingModel,
    middlewares_dir,
    subagents_dir,
    tools_dir,
)

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
    """A catalogue laid out somewhere that is not a workspace."""
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
    return Definitions.from_roots(roots)


def _roots(catalogue):
    """The three directories behind a locally-backed catalogue."""
    return {kind: getattr(catalogue, kind).root for kind in ("skills", "subagents", "tools")}


def test_omitted_it_is_the_three_directories_config_names(cfg):
    """The fallback, and the whole reason 45 call sites did not have to change."""
    assert resolve_definitions(cfg) == Definitions.from_roots(
        {"skills": cfg.skills_dir, "subagents": subagents_dir(cfg), "tools": tools_dir(cfg)}
    )


def test_relocated_directories_are_created_rather_than_silently_empty(tmp_path, cfg):
    """The gap this closes, and it predates the feature."""
    elsewhere = tmp_path / "elsewhere"
    relocated = replace(
        cfg,
        skills_root=elsewhere / "s",
        subagents_root=elsewhere / "a",
        tools_root=elsewhere / "t",
    )
    assert not (elsewhere / "a").exists()

    roots = resolve_definitions(relocated)

    assert all(path.is_dir() for path in _roots(roots).values())


def test_a_supplied_catalogue_must_already_exist(tmp_path, cfg):
    """Creating one would hide the failure it is there to surface."""
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError, match="not a directory"):
        resolve_definitions(
            cfg,
            {"skills": missing, "subagents": missing, "tools": missing},
        )
    assert not missing.exists()


def test_a_supplied_catalogue_names_all_three(tmp_path, cfg):
    """Leaving one out would mean an empty one, which is never what was meant."""
    roots = _staged(tmp_path / "staged")
    with pytest.raises(ConfigError, match="missing tools"):
        staged = _roots(roots)
        resolve_definitions(cfg, {"skills": staged["skills"], "subagents": staged["subagents"]})


def test_the_agent_reads_the_supplied_catalogue_and_not_the_workspace(tmp_path, cfg):
    """Supplied roots replace the configured ones; they do not add to them."""
    (cfg.skills_dir / "in-the-workspace").mkdir(parents=True)
    (cfg.skills_dir / "in-the-workspace" / "SKILL.md").write_text(
        "---\nname: in-the-workspace\ndescription: A skill.\n---\nDo the thing.\n",
        encoding="utf-8",
    )
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT, tool=TOOL)

    assert available_skills(cfg, catalogue=roots) == ("staged-only",)
    assert tuple(defined_subagents(cfg, catalogue=roots)) == ("reviewer",)
    assert workspace_tool_names(cfg, catalogue=roots) == ("elsewhere",)

    # And the configured one is still what is read when nothing is supplied.
    assert available_skills(cfg) == ("in-the-workspace",)


def test_the_skills_route_follows_the_catalogue(tmp_path, cfg, session_dir):
    """The file tools have to reach what the listing advertised."""
    roots = _staged(tmp_path / "staged", skill="staged-only")
    backend = default_backend(cfg, session_dir, catalogue=roots)

    routed = backend.routes[SKILLS_ROUTE]

    assert str(routed.cwd) == str(roots.skills.root.resolve())
    assert backend.read(f"{SKILLS_ROUTE}staged-only/SKILL.md")


@macos
def test_the_shell_reaches_a_supplied_catalogue(cfg, session_dir):
    """The other half of the same answer, and the half a route check cannot see.

    Staged under the operator's home on purpose: the profile denies the home and re-
    allows what has to stay readable, so a catalogue anywhere else is readable
    regardless and would prove nothing.
    """
    probe = Path.home() / "kingfisher-supplied-catalogue-probe"
    roots = _staged(probe)
    (roots.skills.root / "demo").mkdir()
    (roots.skills.root / "demo" / "run.sh").write_text("echo from-the-supplied-catalogue\n")
    try:
        backend = default_backend(cfg, session_dir, catalogue=roots)

        result = backend.execute('sh "$KINGFISHER_SKILLS/demo/run.sh"')

        assert result.exit_code == 0, f"the shell cannot reach it: {result.output}"
        assert "from-the-supplied-catalogue" in str(result.output)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_the_service_settles_it_once_and_hands_it_down(tmp_path, cfg):
    """Resolved at construction, not per request."""
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT)

    service = Kingfisher(cfg, backend=default_backend, catalogue=roots)

    assert service.catalogue == roots


def test_a_broken_catalogue_fails_at_startup(tmp_path, cfg):
    """Rather than on the first turn, when a caller is already waiting."""
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError):
        Kingfisher(cfg, backend=default_backend, catalogue={"skills": missing, "subagents": missing,
                                         "tools": missing})


NOT_MIDDLEWARE = """
class NotMiddleware:
    name = "nope"


MIDDLEWARES = [NotMiddleware]
"""


def test_a_broken_middleware_module_fails_at_startup_too(cfg):
    """The fifth kind, which `warm()` read for none of the time it existed.

    The refusal was written to fire "as the directory is read rather than at the
    first turn" -- true of the refusal and false of everything else, because nothing
    read the directory until a definition named one. So a deployment started, said it
    was fine, and died on the first request activating an agent that names it.
    """
    middlewares_dir(cfg).mkdir(parents=True, exist_ok=True)
    (middlewares_dir(cfg) / "wrong.py").write_text(NOT_MIDDLEWARE, encoding="utf-8")

    with pytest.raises(MiddlewareError, match="AgentMiddleware"):
        Definitions.from_config(cfg).warm()


def test_a_workspace_with_no_middleware_still_warms(cfg):
    """The control beside it: `NoMiddleware` answers an empty mapping rather than
    raising, so the read above must not turn "none offered" into a failure.
    """
    Definitions.from_config(cfg).warm()


SHADOWING_TOOL = """
from langchain_core.tools import tool


@tool
def read_file(text: str) -> str:
    '''A workspace tool wearing a built-in's name.'''
    return text


TOOLS = [read_file]
"""

ITS_OWN_NAME = SHADOWING_TOOL.replace("read_file", "probe_shadow")


def test_a_tool_wearing_a_builtin_name_fails_at_startup(cfg):
    """The refusal `warm` cannot make for itself.

    Whether a workspace tool shadows a built-in is only answerable from an assembled
    graph, and `warm` has no `Config` to assemble one with -- so this waited for the
    first request that touched tools, and refused a turn somebody was already
    waiting on rather than a deployment nobody was.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "shadow.py").write_text(SHADOWING_TOOL, encoding="utf-8")

    with pytest.raises(CapabilityError, match="read_file"):
        Kingfisher(cfg, backend=default_backend)


def test_a_workspace_whose_tools_clash_with_nothing_still_starts(cfg):
    """The control beside it: the probe has to tell a clash from a tool.

    Without this the refusal above passes just as well if startup refused every
    workspace that defines a tool at all.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "fine.py").write_text(ITS_OWN_NAME, encoding="utf-8")

    Kingfisher(cfg, backend=default_backend)


def test_a_workspace_with_no_tools_never_assembles_the_probe(cfg, monkeypatch):
    """What keeps the cost where it belongs.

    Nothing can shadow a built-in when the workspace defines no tools, so the graph
    is not worth compiling -- and this is the assertion that keeps that true, since
    a probe on every construction is about 10ms that most of this suite would pay
    for nothing.
    """

    def refuse(*_args, **_kwargs):
        pytest.fail("the probe was assembled for a workspace with no tools")

    monkeypatch.setattr(service_module, "builtin_tool_names", refuse)

    Kingfisher(cfg, backend=default_backend)


def test_a_delegate_is_activated_from_the_supplied_catalogue(tmp_path, cfg, session_dir):
    """The subagent half, through `build_agent` rather than beside it."""
    roots = _staged(tmp_path / "staged", subagent=SUBAGENT)

    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(subagents=("reviewer",)),
        catalogue=roots,
    )

    names = [spec["name"] for spec in built.subagents or ()]
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
    ).graph

    assert graph is not None
    assert available_skills(enabled, catalogue=roots) == ("staged-only",)


# -- the type itself ------------------------------------------------------


def test_the_three_directories_are_attributes_not_keys(cfg):
    """A string key that is wrong is a `KeyError` at runtime, and in this codebase that
    surfaces as an empty catalogue -- the silent emptiness these modules keep
    refusing.
    """
    catalogue = Definitions.from_config(cfg)

    assert catalogue.skills.root == cfg.skills_dir
    assert catalogue.subagents.root == subagents_dir(cfg)
    assert catalogue.tools.root == tools_dir(cfg)
    assert not hasattr(catalogue, "__getitem__"), "indexing would let both idioms survive"


def test_resolving_accepts_one_that_is_already_resolved(tmp_path, cfg):
    """A deployment stages directories and hands over a mapping, which is the documented
    seam.
    """
    staged = _staged(tmp_path / "staged")

    assert resolve_definitions(cfg, staged) == staged


def test_a_resolved_one_is_still_checked(tmp_path, cfg):
    """Accepting the type is not accepting it unread."""
    missing = tmp_path / "never-staged"
    handed = Definitions.from_roots({"skills": missing, "subagents": missing, "tools": missing})

    with pytest.raises(ConfigError, match="not a directory"):
        resolve_definitions(cfg, handed)


# -- the catalogue reads once ---------------------------------------------


def test_the_catalogue_reads_each_kind_once_not_once_per_turn(cfg, monkeypatch):
    """A deployment's definitions are static, so reading them per turn was work every
    turn paid for nothing. Measured before building: 4ms per turn at five of
    each kind, 81ms at a hundred.

    Counted through the module that binds the name rather than the store's own:
    patching the store measured nothing and reported a clean zero, which is what
    this originally did.
    """
    from functools import cached_property

    from kingfisher.infrastructure import catalogue as catalogue_module
    from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
    from tests.unit.test_run import StubAgent

    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "a.yaml").write_text(
        "name: alpha\ndescription: A.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )

    reads = []

    class Counting(LocalSubagentRepository):
        @cached_property
        def specs(self):
            reads.append(self.root)
            return LocalSubagentRepository(self.root).specs

    monkeypatch.setattr(catalogue_module, "LocalSubagentRepository", Counting)

    service = Kingfisher(cfg, graph=StubAgent("ok"))
    at_construction = len(reads)
    for _ in range(3):
        service.run(Request("go"))

    assert at_construction == 1, "the catalogue was not read when the service was wired"
    # And not again: a turn reads no definitions of its own.
    assert len(reads) == at_construction
    assert all("sessions" in str(d) for d in reads[at_construction:])


def test_a_definition_written_after_wiring_is_not_this_deployments(cfg):
    """The cost of reading once, stated as behaviour rather than left to be discovered."""
    from tests.unit.test_run import StubAgent

    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    service = Kingfisher(cfg, graph=StubAgent("ok"))

    (subagents_dir(cfg) / "late.yaml").write_text(
        "name: late\ndescription: Written afterwards.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )

    assert "late" not in service.catalogue.subagents.specs
    assert "late" in Kingfisher(cfg, graph=StubAgent("ok")).catalogue.subagents.specs


def test_listing_still_survives_a_definition_that_will_not_load(cfg):
    """`--list` is run *because* something is wrong, so it must not be the thing that
    dies.
    """
    (subagents_dir(cfg)).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: x\nnonsense: 1\n", encoding="utf-8")

    catalogue = resolve_definitions(cfg)  # must not raise

    with pytest.raises(SubagentError):
        _ = catalogue.subagents.specs


# -- a repository with no directory behind it ----------------------------


@dataclass(frozen=True)
class InMemorySubagents:
    """A subagent store with no directory anywhere behind it."""

    held: dict

    @property
    def names(self):
        return tuple(self.held)

    @property
    def specs(self):
        return self.held

    # Nothing to say about a folder, and said rather than left off: a member the
    # port declares is read without a default, so leaving one off is an error at
    # the first reader instead of an empty answer.
    @property
    def root(self):
        return None

    @property
    def bundles(self):
        return {}

    @property
    def sources(self):
        return {}

    @property
    def orphaned_assets(self):
        return ()


def _spec(name):
    return SubagentSpec(
        name=name, description="Supplied from memory.", system_prompt="You are supplied."
    )


def test_one_kind_can_be_swapped_without_touching_the_other_two(tmp_path, cfg):
    """What the object bought."""
    staged = _staged(tmp_path / "staged", skill="staged-only")
    swapped = replace(staged, subagents=InMemorySubagents({"from-memory": _spec("from-memory")}))

    assert isinstance(swapped.subagents, SubagentRepository)
    assert swapped.subagents.names == ("from-memory",)
    # untouched, and still reading the directory they were staged in
    assert swapped.skills is staged.skills
    assert [one.name for one in reachable(swapped.skills.root)] == ["staged-only"]


def test_a_supplied_repository_needs_no_directory_to_be_accepted(cfg):
    """The check `resolve_definitions` makes is about *staging*, and staging is
    something only a directory-backed store does.
    """
    handed = replace(
        Definitions.from_config(cfg), subagents=InMemorySubagents({"x": _spec("x")})
    )

    assert resolve_definitions(cfg, handed) is handed


def test_the_agent_is_built_from_a_supplied_repository(cfg, session_dir):
    """End of the chain, and the assertion that matters: nothing between the port and
    the graph knows which kind of store answered.
    """
    catalogue = replace(
        Definitions.from_config(cfg), subagents=InMemorySubagents({"ghost": _spec("ghost")})
    )

    defined = defined_subagents(cfg, catalogue=catalogue)

    assert "ghost" in defined
    assert defined["ghost"].system_prompt == "You are supplied."


def test_the_other_two_kinds_need_no_directory_at_all(cfg, session_dir):
    """Skills need a directory, because deepagents reads them off one and the shell runs
    their scripts from it. That is about skills specifically, and it would be a bad
    outcome if it quietly generalised: subagents are documents kingfisher parses and
    tools are modules it imports, so neither reaches the agent through a route.
    """
    catalogue = replace(
        Definitions.from_config(cfg), subagents=InMemorySubagents({"x": _spec("x")})
    )

    assert default_backend(cfg, session_dir, catalogue=catalogue) is not None


def test_a_definition_that_will_not_parse_fails_at_startup_too(cfg):
    """The other half of "fails at startup", and the half `warm` is for.

    Mutation-tested: emptying `warm` leaves this the only test that notices.
    """
    for kind in ("skills", "subagents", "tools"):
        (cfg.workspace / kind).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text(
        "name: broken\ndescription: A.\nsystem_prompt: |\n  x\nnonsense_field: true\n",
        encoding="utf-8",
    )

    with pytest.raises(SubagentError):
        Kingfisher(cfg, graph=None)

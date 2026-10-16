"""Where a deployment's definitions are read from, and who gets to decide."""

from __future__ import annotations

import platform
import shutil
from dataclasses import replace
from pathlib import Path

import pytest

from kingfisher.application import service as service_module
from kingfisher.application.service import Kingfisher
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.domain.request import Request
from kingfisher.infrastructure.catalogue import Definitions, resolve_definitions
from kingfisher.infrastructure.harness.activation import (
    available_skills,
    defined_subagents,
)
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import LocalSessions, backend_at
from kingfisher.infrastructure.harness.kinds.tools import workspace_tool_names
from kingfisher.kinds.middlewares.catalogue import MiddlewareError
from kingfisher.kinds.subagents.spec import SubagentError
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
    """The staged kinds' directories behind a locally-backed catalogue."""
    return {kind: getattr(catalogue, kind).root for kind in ("skills", "subagents", "tools")}


def test_omitted_it_is_the_three_directories_config_names(cfg):
    """The fallback, and the whole reason 45 call sites did not have to change."""
    assert resolve_definitions(cfg) == Definitions.from_roots(
        {"skills": cfg.skills_dir, "subagents": subagents_dir(cfg), "tools": tools_dir(cfg)}
    )


def test_a_missing_kind_folder_means_none_of_that_kind_and_is_never_made(cfg):
    """Catches a start writing into the definitions folder, which other deployments may
    read and a failed mount would leave empty, rather than reading it as it is."""
    for kind in ("skills", "subagents", "tools"):
        shutil.rmtree(cfg.catalogue_roots[kind])

    catalogue = Kingfisher(cfg, sessions=LocalSessions()).catalogue

    assert available_skills(cfg, catalogue=catalogue) == ()
    assert catalogue.subagents.specs == {}
    assert catalogue.tools.found == ()
    assert not any(cfg.catalogue_roots[kind].exists() for kind in ("skills", "subagents", "tools"))


def test_a_missing_definitions_folder_is_refused_rather_than_made(tmp_path, cfg):
    """Making one would serve an empty catalogue where a seed or a mount failed."""
    unseeded = replace(cfg, definitions=tmp_path / "never-seeded")

    with pytest.raises(ConfigError, match="kingfisher seed"):
        resolve_definitions(unseeded)
    assert not unseeded.definitions.exists()


def test_a_staged_catalogue_gets_no_kind_folder_made_anywhere(tmp_path, cfg):
    """The layout made every configured kind in the workspace even where the definitions
    were staged somewhere else, and none of them would be read from there.
    """
    from kingfisher.infrastructure.catalogue import DEFINITION_KINDS

    staged = tmp_path / "staged"
    supplied = _roots(_staged(staged))
    fresh = replace(cfg, workspace=tmp_path / "fresh", definitions=staged)

    kf = Kingfisher(fresh, sessions=LocalSessions())

    assert not set(DEFINITION_KINDS) & {p.name for p in kf.workspace.iterdir()}, (
        "a kind folder in the workspace, where the definitions are not read from"
    )
    assert {p.name for p in staged.iterdir()} == set(supplied), (
        "a folder made beside the staged catalogue, which its caller did not stage"
    )


def test_a_supplied_folder_missing_a_kind_offers_none_of_it(tmp_path, cfg):
    """Catches a kind the staged folder left out being read from the configured folder
    instead, which would serve definitions the deployment chose not to supply.
    """
    (tools_dir(cfg) / "extra.py").write_text(TOOL, encoding="utf-8")
    staged = tmp_path / "staged"
    _staged(staged, skill="staged-only")
    shutil.rmtree(staged / "tools")

    roots = resolve_definitions(replace(cfg, definitions=staged))

    assert roots.skills.root == staged / "skills"
    assert available_skills(cfg, catalogue=roots) == ("staged-only",)
    assert workspace_tool_names(cfg, catalogue=roots) == ()
    # The control: the configured folder does offer it, so the line above is not
    # passing on a tool that never loads.
    assert workspace_tool_names(cfg) == ("elsewhere",)


def test_the_agent_reads_the_supplied_catalogue_and_not_the_configured_one(tmp_path, cfg):
    """Supplied roots replace the configured ones; they do not add to them."""
    (cfg.skills_dir / "configured").mkdir(parents=True)
    (cfg.skills_dir / "configured" / "SKILL.md").write_text(
        "---\nname: configured\ndescription: A skill.\n---\nDo the thing.\n",
        encoding="utf-8",
    )
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT, tool=TOOL)

    assert available_skills(cfg, catalogue=roots) == ("staged-only",)
    assert tuple(defined_subagents(cfg, catalogue=roots)) == ("reviewer",)
    assert workspace_tool_names(cfg, catalogue=roots) == ("elsewhere",)

    # And the configured one is still what is read when nothing is supplied.
    assert available_skills(cfg) == ("configured",)


def test_the_skills_route_follows_the_catalogue(tmp_path, cfg, session_dir):
    """The file tools have to reach what the listing advertised."""
    roots = _staged(tmp_path / "staged", skill="staged-only")
    backend = backend_at(cfg, session_dir, catalogue=roots)

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
        backend = backend_at(cfg, session_dir, catalogue=roots)

        result = backend.execute('sh "$KINGFISHER_SKILLS/demo/run.sh"')

        assert result.exit_code == 0, f"the shell cannot reach it: {result.output}"
        assert "from-the-supplied-catalogue" in str(result.output)
    finally:
        shutil.rmtree(probe, ignore_errors=True)


def test_the_service_settles_it_once_and_hands_it_down(tmp_path, cfg):
    """Resolved at construction, not per request."""
    roots = _staged(tmp_path / "staged", skill="staged-only", subagent=SUBAGENT)

    service = Kingfisher(replace(cfg, definitions=tmp_path / "staged"), sessions=LocalSessions())

    assert service.catalogue == roots


def test_a_broken_catalogue_fails_at_startup(tmp_path, cfg):
    """Rather than on the first turn, when a caller is already waiting."""
    missing = tmp_path / "never-staged"
    with pytest.raises(ConfigError):
        Kingfisher(replace(cfg, definitions=missing), sessions=LocalSessions())


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
    """The control beside it: a definitions folder with no `middlewares/` answers an
    empty mapping rather than raising, so the read above must not turn "none offered"
    into a failure.
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
        Kingfisher(cfg, sessions=LocalSessions())


def test_a_workspace_whose_tools_clash_with_nothing_still_starts(cfg):
    """The control beside it: the probe has to tell a clash from a tool.

    Without this the refusal above passes just as well if startup refused every
    workspace that defines a tool at all.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "fine.py").write_text(ITS_OWN_NAME, encoding="utf-8")

    Kingfisher(cfg, sessions=LocalSessions())


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

    Kingfisher(cfg, sessions=LocalSessions())


def test_a_subagent_is_activated_from_the_supplied_catalogue(tmp_path, cfg, session_dir):
    """The subagent half, through `build_agent` rather than beside it."""
    roots = _staged(tmp_path / "staged", subagent=SUBAGENT)

    built = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir, catalogue=roots),
        model=FakeToolCallingModel(responses=[]),
        capabilities=Capabilities(subagents=("reviewer",)),
        catalogue=roots,
    )

    names = [spec["name"] for spec in built.subagents or ()]
    assert "reviewer" in names, "the staged catalogue's subagent was not wired"


def test_the_agent_it_builds_offers_the_staged_definitions(tmp_path, cfg, session_dir):
    """End to end: what the service resolved is what the graph was built from."""
    roots = _staged(tmp_path / "staged", skill="staged-only")
    enabled = replace(cfg, skills_enabled=True)

    graph = build_agent(
        enabled,
        backend=backend_at(enabled, session_dir, catalogue=roots),
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

    service = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
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

    service = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())

    (subagents_dir(cfg) / "late.yaml").write_text(
        "name: late\ndescription: Written afterwards.\nsystem_prompt: |\n  x\n", encoding="utf-8"
    )

    assert "late" not in service.catalogue.subagents.specs
    assert "late" in Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    ).catalogue.subagents.specs


def test_listing_still_survives_a_definition_that_will_not_load(cfg):
    """`--list` is run *because* something is wrong, so it must not be the thing that
    dies.
    """
    (subagents_dir(cfg)).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "broken.yaml").write_text("name: x\nnonsense: 1\n", encoding="utf-8")

    catalogue = resolve_definitions(cfg)  # must not raise

    with pytest.raises(SubagentError):
        _ = catalogue.subagents.specs


def test_a_definition_that_will_not_parse_fails_at_startup_too(cfg):
    """The other half of "fails at startup", and the half `warm` is for.

    Mutation-tested: emptying `warm` leaves this the only test that notices.
    """
    (subagents_dir(cfg) / "broken.yaml").write_text(
        "name: broken\ndescription: A.\nsystem_prompt: |\n  x\nnonsense_field: true\n",
        encoding="utf-8",
    )

    with pytest.raises(SubagentError):
        Kingfisher(cfg, graph=None, sessions=LocalSessions())

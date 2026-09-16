"""A subagent's own tools and skills, kept in a folder named after it."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.inventory import inventory
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import default_backend, skills_sources
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.kinds.tools.catalogue import ToolError
from kingfisher.kinds.tools.spec import Offering, tool_name
from kingfisher.layout import BUNDLED_SKILLS_ROUTE, SKILLS_ROUTE, denied_scopes
from kingfisher.presentation.cli.health import examine, worst
from kingfisher.presentation.cli.listing import _catalogue, failed
from tests.conftest import FakeToolCallingModel, capture_build

DEFINITION = "name: {name}\ndescription: A subagent.\nsystem_prompt: |\n  x\n"


def define(directory, name, filename=None):
    """One definition, written where the caller says."""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (filename or f"{name}.yaml")
    path.write_text(DEFINITION.format(name=name), encoding="utf-8")
    return path


# -- what makes a folder a bundle -------------------------------------------


def test_a_folder_named_after_its_definition_is_that_subagents_bundle(tmp_path):
    """The whole rule, and the only one."""
    define(tmp_path / "surveyor", "surveyor")
    (tmp_path / "surveyor" / "tools").mkdir()
    (tmp_path / "surveyor" / "skills" / "sampling").mkdir(parents=True)

    bundles = LocalSubagentRepository(tmp_path).bundles

    assert set(bundles) == {"surveyor"}
    assert bundles["surveyor"].tools == tmp_path / "surveyor" / "tools"
    assert bundles["surveyor"].skills == tmp_path / "surveyor" / "skills"
    assert bundles["surveyor"].where == "surveyor"


def test_a_definition_sitting_loose_has_no_bundle(tmp_path):
    """`subagents/reviewer.yaml` has no folder to be named after, and the simple case
    has to stay simple -- a definition should not need a directory.
    """
    define(tmp_path, "reviewer")

    assert LocalSubagentRepository(tmp_path).bundles == {}


def test_a_grouping_folder_is_not_a_bundle(tmp_path):
    """`analysis/profiler.yaml` is the shape the shipped catalogue already uses."""
    define(tmp_path / "analysis", "profiler")

    repository = LocalSubagentRepository(tmp_path)

    assert repository.bundles == {}
    assert set(repository.specs) == {"profiler"}


def test_a_bundle_folder_is_keyed_the_way_a_grant_names_it(tmp_path):
    """Two catalogues may each ship a `surveyor`, so `specs` qualifies the name."""
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "other" / "surveyor", "surveyor")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.bundles) == set(repository.specs)
    assert len(repository.bundles) == 2


#: A compiled delegate, small enough to declare inline and real enough to build.
COMPILED = """
def build(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)


SUBAGENTS = [
    {"name": "surveyor", "description": "A compiled subagent.", "build": build}
]
"""


def test_a_subagent_declared_in_a_package_gets_no_bundle(tmp_path):
    """The folder a package names is the package, whose `__init__.py` decides what it
    exports -- so `surveyor/tools/` there is `surveyor.tools`, importable and not a
    bundle.

    This was called `..._declared_in_python_...` and proved only the package half,
    which read as a rule about every Python definition. A module in a folder named
    after it is the test below, and does get one.
    """
    package = tmp_path / "surveyor"
    package.mkdir()
    (package / "__init__.py").write_text(
        "SUBAGENTS = [\n"
        '    {"name": "surveyor", "description": "A subagent.", '
        '"build": lambda model, tools: None}\n'
        "]\n",
        encoding="utf-8",
    )
    (package / "tools").mkdir()

    assert LocalSubagentRepository(tmp_path).bundles == {}


def test_a_compiled_subagent_in_a_folder_named_after_it_owns_a_bundle(tmp_path):
    """The other Python shape, and it is the ordinary rule rather than an exception:
    a folder holding `surveyor.py` is a folder, not a package, so it is a bundle for
    the same reason a folder holding `surveyor.yaml` is.
    """
    folder = tmp_path / "surveyor"
    (folder / "tools").mkdir(parents=True)
    (folder / "surveyor.py").write_text(COMPILED, encoding="utf-8")

    assert set(LocalSubagentRepository(tmp_path).bundles) == {"surveyor"}


# -- the ambiguity that has to be refused -----------------------------------


def test_a_bundle_folder_holding_a_second_definition_is_refused(tmp_path):
    """There is no honest answer to "is `helper` inside the bundle or beside it", and
    the two answers differ in what `helper` may call.
    """
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "surveyor", "helper")

    with pytest.raises(SubagentError) as raised:
        _ = LocalSubagentRepository(tmp_path).bundles

    message = str(raised.value)
    assert "surveyor/helper.yaml" in message
    assert "surveyor/surveyor.yaml" in message


def test_the_refusal_says_what_is_at_stake(tmp_path):
    """A message naming two files and not the consequence sends someone to rename
    something without knowing which way.
    """
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "surveyor", "helper")

    with pytest.raises(SubagentError) as raised:
        _ = LocalSubagentRepository(tmp_path).bundles

    assert "surveyor/tools" in str(raised.value)


# -- the one that is reported, not refused ----------------------------------


def test_assets_under_a_folder_no_definition_claims_are_reported(tmp_path):
    """Legal -- a grouping folder may have directories in it -- and nine times in ten a
    bundle whose definition was renamed.
    """
    define(tmp_path / "analysis", "profiler")
    (tmp_path / "analysis" / "tools").mkdir()

    repository = LocalSubagentRepository(tmp_path)

    assert repository.orphaned_assets == ("analysis",)
    assert repository.bundles == {}


def test_a_real_bundle_is_not_reported_as_orphaned(tmp_path):
    """The other half."""
    define(tmp_path / "surveyor", "surveyor")
    (tmp_path / "surveyor" / "tools").mkdir()

    assert LocalSubagentRepository(tmp_path).orphaned_assets == ()


# -- what a bundle's own directories may hold -------------------------------


def test_a_skills_config_file_is_not_read_as_a_subagent(tmp_path):
    """The hazard the reserved names exist for."""
    define(tmp_path / "surveyor", "surveyor")
    sampling = tmp_path / "surveyor" / "skills" / "sampling"
    sampling.mkdir(parents=True)
    (sampling / "config.yaml").write_text("rows: 100\n", encoding="utf-8")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.specs) == {"surveyor"}


def test_a_yaml_beside_a_bundles_tools_is_not_read_either(tmp_path):
    """Same reason, other directory."""
    define(tmp_path / "surveyor", "surveyor")
    tools = tmp_path / "surveyor" / "tools"
    tools.mkdir()
    (tools / "fixtures.yaml").write_text("a: 1\n", encoding="utf-8")

    assert set(LocalSubagentRepository(tmp_path).specs) == {"surveyor"}


def test_the_reserved_names_are_skipped_wherever_they_appear(tmp_path):
    """Not scoped to bundles, and deliberately: knowing whether a folder is a bundle
    means reading the definition that decides it, which is the walk this rule is part
    of.
    """
    define(tmp_path / "tools", "buried")

    assert LocalSubagentRepository(tmp_path).specs == {}


# -- what a bundle's tools are, and are not ---------------------------------


TOOL = '''
def {name}() -> str:
    "A tool."
    return "{answer}"

TOOLS = [{name}]
'''

BROKEN = "import a_package_that_is_not_installed\n\nTOOLS = []\n"

#: The ordinary case, named once so it can be a default without a call.
PROBE = TOOL.format(name="probe", answer="ok")


def catalogue_with_bundle(tmp_path, tool=PROBE):
    """A deployment whose `surveyor` brings one tool of its own."""
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True, exist_ok=True)
    define(tmp_path / "subagents" / "surveyor", "surveyor")
    tools = tmp_path / "subagents" / "surveyor" / "tools"
    tools.mkdir()
    (tools / "probe.py").write_text(tool, encoding="utf-8")
    (tmp_path / "tools" / "shared.py").write_text(
        TOOL.format(name="shared", answer="ok"), encoding="utf-8"
    )
    return Definitions.from_roots({kind: tmp_path / kind for kind in
                                   ("agents", "skills", "subagents", "tools")})


def test_a_bundles_tools_are_loaded_under_their_owners_name(tmp_path):
    """The name is the one a grant would use, so a caller holding a subagent name from
    `specs` has a key that finds its tools here.
    """
    catalogue = catalogue_with_bundle(tmp_path)

    assert set(catalogue.bundled_tools) == {"surveyor"}
    assert [f.name for f in catalogue.bundled_tools["surveyor"].found] == ["probe"]


def test_a_bundles_tool_is_not_in_the_shared_offering(tmp_path):
    """The whole feature in one assertion."""
    catalogue = catalogue_with_bundle(tmp_path)

    offered = Offering.of(catalogue.tools.found)

    assert "shared" in offered.workspace
    assert "probe" not in offered.workspace


def test_a_broken_private_tool_fails_at_startup(tmp_path):
    """The existing rule, not a new one: a broken tool exits 1, a broken skill does not."""
    catalogue = catalogue_with_bundle(tmp_path, tool=BROKEN)

    with pytest.raises(ToolError):
        catalogue.warm()


def test_a_catalogue_with_no_bundles_has_no_bundled_tools(tmp_path):
    """A deployment that never writes one pays nothing, and a store-backed catalogue has
    no folders to find a bundle in at all.
    """
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True)
    define(tmp_path / "subagents", "reviewer")

    catalogue = Definitions.from_roots(
        {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    )

    assert catalogue.bundled_tools == {}
    catalogue.warm()


def test_a_bundle_with_skills_but_no_tools_is_not_a_tool_repository(tmp_path):
    """`bundle.tools` answers `None` when there is no directory, and a repository
    pointed at a path that is not there would report an empty catalogue rather than
    the absence of one.
    """
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True)
    define(tmp_path / "subagents" / "surveyor", "surveyor")
    (tmp_path / "subagents" / "surveyor" / "skills" / "sampling").mkdir(parents=True)

    catalogue = Definitions.from_roots(
        {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    )

    assert catalogue.bundled_tools == {}


def test_a_private_tool_is_not_read_as_a_subagent_module(tmp_path):
    """The Python half of the reserved names, and the one Task 1 missed."""
    define(tmp_path / "surveyor", "surveyor")
    tools = tmp_path / "surveyor" / "tools"
    tools.mkdir()
    (tools / "probe.py").write_text(TOOL.format(name="probe", answer="ok"), encoding="utf-8")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.specs) == {"surveyor"}


def test_a_private_tool_written_as_a_package_is_skipped_too(tmp_path):
    """`modules_in` stops at a package rather than descending, so this arrives as a
    directory rather than a file and has to be excluded by the same rule.
    """
    define(tmp_path / "surveyor", "surveyor")
    package = tmp_path / "surveyor" / "tools" / "probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(TOOL.format(name="probe", answer="ok"), encoding="utf-8")

    assert set(LocalSubagentRepository(tmp_path).specs) == {"surveyor"}


# -- what the delegate actually ends up holding -----------------------------


PRIVATE_OWNER = """name: surveyor
description: Surveys files.
tools: [shared]
system_prompt: |
  You survey.
"""

NO_TOOLS_LINE = """name: surveyor
description: Surveys files.
system_prompt: |
  You survey.
"""


def workspace_with_bundle(cfg, definition=PRIVATE_OWNER, private="probe"):
    """A workspace whose `surveyor` brings one tool of its own."""
    bundle = cfg.workspace / "subagents" / "surveyor"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "surveyor.yaml").write_text(definition, encoding="utf-8")
    (bundle / "tools").mkdir(exist_ok=True)
    # A different answer from the catalogue's, so a test can say which of two
    # files defining one name actually reached the delegate.
    (bundle / "tools" / f"{private}.py").write_text(
        TOOL.format(name=private, answer="from the bundle"), encoding="utf-8"
    )
    tools = cfg.workspace / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "shared.py").write_text(
        TOOL.format(name="shared", answer="from the catalogue"), encoding="utf-8"
    )


def only(captured, name):
    """The delegate we are asking about, as deepagents received it."""
    (found,) = [s for s in captured["subagents"] if s["name"] == name]
    return found


def built_subagent(cfg, session_dir, monkeypatch):
    """The one delegate this workspace defines, as deepagents received it."""
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",), tools=("shared",)),
    )
    return only(captured, "surveyor")


def test_a_delegate_holds_the_tool_from_its_own_folder(cfg, session_dir, monkeypatch):
    """The request granted `shared` and never heard of `probe`."""
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    assert {tool_name(t) for t in subagent["tools"]} == {"probe", "shared"}


def test_a_private_tool_survives_a_request_that_granted_no_tools(cfg, session_dir, monkeypatch):
    """The decision, stated as a test."""
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",), tools=()),
    )

    subagent = only(captured, "surveyor")
    assert {tool_name(t) for t in subagent["tools"]} == {"probe"}


def test_a_private_tool_is_in_the_delegates_allowlist(cfg, session_dir, monkeypatch):
    """The failure this would otherwise have been is silent rather than absent."""
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, ToolAllowlist)]
    assert "probe" in allowlist._allowed
    assert "shared" in allowlist._allowed


def test_the_bundle_wins_a_name_the_catalogue_also_defines(cfg, session_dir, monkeypatch):
    """One candidate answers each name, so `duplicated` still holds and nothing is
    silently replaced -- the order is stated before the lookup.
    """
    workspace_with_bundle(cfg, private="shared")

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    (held,) = subagent["tools"]
    assert tool_name(held) == "shared"
    # Which of the two files answered, since both define a `shared`.
    assert held() == "from the bundle"


def test_the_main_agent_never_holds_another_delegates_private_tool(
    cfg, session_dir, monkeypatch
):
    """The point of the whole feature."""
    workspace_with_bundle(cfg)
    captured = capture_build(monkeypatch)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",)),
    )

    assert "probe" not in {tool_name(t) for t in captured["tools"]}
    assert "shared" in {tool_name(t) for t in captured["tools"]}


# -- the skills half --------------------------------------------------------


SKILL = "---\nname: {name}\ndescription: {description}\n---\n\nDo the thing.\n"


def with_private_skill(cfg, name="sampling"):
    """A `surveyor` whose bundle also holds a skill."""
    bundle = cfg.workspace / "subagents" / "surveyor"
    folder = bundle / "skills" / name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(
        SKILL.format(name=name, description="Samples rows before trusting a file."),
        encoding="utf-8",
    )


def test_a_delegate_is_told_about_the_skill_in_its_own_folder(cfg, session_dir, monkeypatch):
    """`skills:` defaults to none, so a delegate saying nothing gets no index at all.

    Rendered rather than read off `_allowed`, and that is the whole guard: the
    grant was recorded under the label the bundle was *read* under and the index
    is keyed by the label it is *mounted* under, so this delegate was handed
    "No skills available yet" while `_allowed` looked right.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    (narrowed,) = [m for m in subagent["middleware"] if isinstance(m, NarrowedSkills)]
    assert any(key.endswith("sampling") for key in narrowed._allowed)
    assert "sampling" in narrowed._format_skills_list(narrowed._qualified())


def test_a_bundles_skill_is_mounted_read_only(cfg, session_dir):
    """The route sits under `/skills/` for exactly this reason."""
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    backend = default_backend(cfg, session_dir)

    mounted = [route for route in backend.routes if route.startswith(BUNDLED_SKILLS_ROUTE)]
    assert mounted == ["/skills/subagents/surveyor/"]
    assert all(route.startswith(SKILLS_ROUTE) for route in mounted)


def test_a_bundles_skills_add_a_mount_and_no_rule(cfg, session_dir):
    """A mount per bundle, and still one deny rule for all of `/skills/`."""
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    backend = default_backend(cfg, session_dir)
    mounted = [route for route in backend.routes if route.startswith(BUNDLED_SKILLS_ROUTE)]

    assert mounted, "no bundle mounted, so this asserts nothing about bundles"
    assert denied_scopes() == ("/.harness/**", "/data/**", "/skills/**")
    assert all(route.startswith("/skills/") for route in mounted), (
        "a mount outside the scope the one rule covers would be writable"
    )


def test_a_bundles_skill_is_not_in_the_shared_registry(cfg, session_dir, monkeypatch):
    """The skills counterpart of keeping bundle tools out of `Offering`: a private skill
    in the shared registry would be one any request could grant and any agent could
    be told about.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    catalogue = Definitions.from_config(cfg)

    assert not any("sampling" in key for key in catalogue.registry.offered)
    assert any("sampling" in key for key in catalogue.bundled_skills["surveyor"].offered)


def test_a_catalogue_folder_called_subagents_is_refused(cfg):
    """A folder that would shadow every bundle is refused rather than skipped."""
    with pytest.raises(ConfigError) as raised:
        skills_sources(("research", "subagents"))

    assert "subagents" in str(raised.value)
    assert "hide every bundled skill" in str(raised.value)


def test_an_ordinary_catalogue_folder_is_still_a_source():
    """The other half, so the refusal above cannot quietly become "no folders"."""
    assert ("/skills/research/", "research") in skills_sources(("research",))


# -- what a listing says ----------------------------------------------------


def test_a_listing_prints_private_assets_under_their_owner(cfg):
    """The one capability a listing could not otherwise reveal."""
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    found = inventory(cfg)

    assert found.bundled_tools["surveyor"] == ("probe",)
    assert found.bundled_skills["surveyor"] == ("sampling",)
    printed = "\n".join(_catalogue(found))
    assert "probe  [private tool]" in printed
    assert "sampling  [private skill]" in printed


def test_a_listing_says_when_a_bundle_shadows_the_catalogue(cfg):
    """Shadowing is only acceptable while it is visible."""
    workspace_with_bundle(cfg, private="shared")

    found = inventory(cfg)

    assert found.shadowed["surveyor"] == ("shared",)
    assert "shadowing the catalogue's" in "\n".join(_catalogue(found))


def test_a_listing_names_the_folder_whose_definition_was_renamed(cfg):
    """A renamed definition takes its bundle with it and says nothing, which is the
    failure `orphaned_assets` was written for and nothing printed for as long as it
    existed: `redactor.yaml` told readers a listing reports the orphan while no
    caller anywhere read the field.

    Driven by renaming a bundle that works, rather than by a folder built orphaned,
    so the loss is the same one a reader would hit.
    """
    workspace_with_bundle(cfg)
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )

    found = inventory(cfg)

    # The silent half, asserted first because it is what the line is about: the
    # delegate still loads, and holds none of what is in its folder.
    assert "surveys" in found.subagents
    assert found.bundled_tools == {}
    assert found.orphaned_assets == ("surveyor",)
    assert "surveyor/ holds tools/ or skills/" in "\n".join(_catalogue(found))


def test_a_listing_does_not_report_a_grouping_folder_that_holds_no_assets(cfg):
    """The control. `analysis/profiler.yaml` next to the shipped bundle is ordinary
    organisation, and a check that reported every folder naming no definition would
    put a warning on it forever.
    """
    define(cfg.workspace / "subagents" / "analysis", "profiler")

    found = inventory(cfg)

    assert found.orphaned_assets == ()
    assert "holds tools/ or skills/" not in "\n".join(_catalogue(found))


def test_an_orphaned_bundle_does_not_make_the_listing_non_zero(cfg):
    """Legal, and the split `misfiled` already draws: a folder naming no definition
    is reported, never refused, so a deployment that meant it still exits zero.
    """
    workspace_with_bundle(cfg)
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )

    found = inventory(cfg)

    assert found.orphaned_assets == ("surveyor",)
    assert not failed(found)


def test_doctor_warns_about_an_orphaned_bundle_and_does_not_fail_on_it(cfg):
    """Its own check name rather than `delegate tools`: the folder may hold only
    `skills/`, and a row naming the wrong half sends a reader looking for a tool
    that was never there.
    """
    workspace_with_bundle(cfg)
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )

    checks = examine(cfg)
    named = {check.name: check for check in checks}

    assert named["delegate bundles"].verdict == "warn"
    assert "surveyor/" in named["delegate bundles"].detail
    assert worst(checks) != "fail", "a folder naming no definition is legal"


def test_doctor_names_the_delegate_whose_private_tools_will_not_load(cfg):
    """Its own check rather than folded into `tools`, for the reason the field is
    its own: a reader has to be told which delegate to go and open.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    bundle = cfg.workspace / "subagents" / "surveyor" / "tools"
    (bundle / "probe.py").write_text(BROKEN, encoding="utf-8")

    checks = {check.name: check for check in examine(cfg)}

    assert checks["delegate tools"].verdict == "fail"
    assert checks["tools"].verdict == "ok", "the catalogue's own tools still loaded"


def test_doctor_warns_about_shadowing_and_does_not_fail_on_it(cfg):
    """Nothing is broken: the delegate answers with its own and the catalogue's
    never reaches it. That is a decision somebody made, and it is only acceptable
    while it is visible -- so `doctor` says it and still exits zero.
    """
    workspace_with_bundle(cfg, private="shared")

    checks = examine(cfg)
    named = {check.name: check for check in checks}

    assert named["delegate tools"].verdict == "warn"
    assert "surveyor" in named["delegate tools"].detail
    assert worst(checks) != "fail", "shadowing stops nothing, so it must not gate"


def test_a_broken_private_tool_makes_the_listing_non_zero(cfg):
    """Asserted rather than assumed, because this predicate has been wrong once:
    `agents` was added, the section printed "cannot load", and the exit code still
    named the two kinds that existed when it was written.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    bundle = cfg.workspace / "subagents" / "surveyor" / "tools"
    (bundle / "probe.py").write_text(BROKEN, encoding="utf-8")

    found = inventory(cfg)

    assert found.bundles_error is not None
    assert failed(found)


def test_a_broken_bundle_does_not_hide_the_rest_of_the_listing(cfg):
    """The other half of the same bug: one bad tool printed one section of four."""
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    (cfg.workspace / "subagents" / "surveyor" / "tools" / "probe.py").write_text(
        BROKEN, encoding="utf-8"
    )

    found = inventory(cfg)

    assert "surveyor" in found.subagents
    assert found.subagents_error is None
    assert "shared" in found.tools


# -- saying what is in there, and being held to it --------------------------


def echoing(cfg, tools=None, skills=None, *, skill=False, written=None):
    """A `surveyor` bundle whose definition writes the `bundle:` key given.

    Rendered rather than taken as text, so a test says what it claims and the YAML
    shape lives in one place -- `written` is for the handful that need a malformed
    one, and those say so by passing it.
    """
    halves = "".join(
        f"  {half}: [{', '.join(names)}]\n"
        for half, names in (("tools", tools), ("skills", skills))
        if names is not None
    )
    key = written if written is not None else (f"bundle:\n{halves}" if halves else "")
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE + key)
    if skill:
        with_private_skill(cfg)


def test_a_definition_that_names_what_its_folder_holds_loads(cfg):
    """The control, and it has to come first: every refusal below would also fire on a
    definition that simply cannot be read, and then none of them would be about the
    `bundle:` key at all.
    """
    echoing(cfg, tools=["probe"], skills=["sampling"], skill=True)

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.subagents.specs["surveyor"].bundle == {
        "tools": ("probe",),
        "skills": ("sampling",),
    }


def test_a_name_the_folder_does_not_hold_is_refused(cfg):
    """The stale direction: a definition still naming a tool somebody deleted."""
    echoing(cfg, tools=["probe", "gone"])

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "gone" in str(raised.value)
    assert "surveyor/tools/" in str(raised.value)


def test_a_tool_the_folder_gained_is_refused(cfg):
    """The direction the key is actually for, and the one a subset check would let
    through: a tool dropped into the folder reaches this delegate with no line in any
    file changed, so a definition that has written the list has to go red for it.
    """
    echoing(cfg, tools=["probe"])
    arrived = cfg.workspace / "subagents" / "surveyor" / "tools" / "later.py"
    arrived.write_text(TOOL.format(name="later", answer="ok"), encoding="utf-8")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "later" in str(raised.value)
    assert "bundle.tools does not name" in str(raised.value)


def test_a_skill_the_folder_holds_and_the_definition_does_not_name_is_refused(cfg):
    """Both halves, or the second is a line that reads like a check and is not one."""
    echoing(cfg, tools=["probe"], skills=[], skill=True)

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "sampling" in str(raised.value)
    assert "bundle.skills does not name" in str(raised.value)


def test_a_half_left_out_is_not_a_half_claiming_none(cfg):
    """`skills:` absent says nothing about skills; `skills: []` says there are none.

    Collapsing the two would make the shorter form silently assert something, which
    is how a definition naming only its tools would start refusing every bundle that
    also ships a skill.
    """
    echoing(cfg, tools=["probe"], skill=True)

    catalogue = Definitions.from_config(cfg).warm()

    assert set(catalogue.subagents.specs["surveyor"].bundle) == {"tools"}


def test_a_definition_that_echoes_a_folder_it_no_longer_owns_is_refused(cfg):
    """The rename, from the side the definition is on.

    `orphaned_assets` sees the folder left behind and cannot see what the file thought
    it had; this is the only place the two halves of that rename meet, and without it
    a renamed bundle is a delegate that quietly holds nothing.
    """
    echoing(cfg, tools=["probe"])
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "owns no folder" in str(raised.value)
    assert "bundle" in str(raised.value)


def test_a_rename_is_reported_from_both_sides(cfg):
    """The folder left behind and the file that walked away from it, which are
    different facts: the orphan report knows a folder reaches nobody and cannot know
    what was supposed to be in it, and this knows what the definition thought it had
    and not that the folder is still sitting there.

    Also what opting in costs and buys: without `bundle:` a rename is a warning and a
    zero exit, and with it a refusal.
    """
    echoing(cfg, tools=["probe"])
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )

    found = inventory(cfg)
    named = {check.name: check for check in examine(cfg)}

    assert found.orphaned_assets == ("surveyor",)
    assert "owns no folder" in found.miscounted_bundles["surveys"]
    assert named["delegate bundles"].verdict == "warn"
    assert named["bundle claims"].verdict == "fail"


def test_a_star_is_refused_because_it_would_check_nothing(cfg):
    """`['*']` would say only that a bundle reaches its owner, which is true of every
    bundle -- a claim that cannot be wrong, in a key whose only job is to be wrong
    when the folder changes.
    """
    echoing(cfg, tools=['"*"'])

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "may not be" in str(raised.value)


def test_both_spellings_of_the_star_are_refused_the_same_way(cfg):
    """`"*"` and `["*"]` are one mistake, and the generic reader answered the first
    with "write ['*'] instead" -- advice pointing straight at the second, which this
    key forbids. Asserted on the reason rather than on failing at all, because it
    failed at all before too.
    """
    for spelling in ('  tools: "*"\n', '  tools: ["*"]\n'):
        echoing(cfg, written=f"bundle:\n{spelling}")

        with pytest.raises(SubagentError) as raised:
            Definitions.from_config(cfg).warm()

        assert "true of every bundle" in str(raised.value), spelling
        assert "write" not in str(raised.value), spelling


def test_a_bundle_that_is_not_a_mapping_says_which_halves_it_takes(cfg):
    """`reader.mapping` refuses with "a mapping of your own keys" -- true of
    `metadata:` and the opposite of true here, where the two keys are the format's.
    Somebody writing `bundle: [mask_secrets]` needs to be told which half they meant.
    """
    echoing(cfg, written="bundle: [probe]\n")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "your own keys" not in str(raised.value)
    assert "tools and/or skills" in str(raised.value)


def test_an_empty_bundle_is_refused_for_the_same_reason(cfg):
    """`bundle: {}` describes nothing, so it cannot be wrong, so it checks nothing --
    while looking in a diff exactly like a definition that had opted in.
    """
    echoing(cfg, written="bundle: {}\n")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "bundle is empty" in str(raised.value)


def test_a_half_that_is_not_a_directory_a_bundle_holds_is_refused(cfg):
    """`bundle: {middlewares: [...]}` names a folder no bundle has, so it would sit
    there describing nothing and checking nothing -- the same failure as an empty one,
    wearing a plausible word.
    """
    echoing(cfg, written="bundle:\n  middlewares: [call-cap]\n")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "middlewares" in str(raised.value)
    assert "['tools', 'skills']" in str(raised.value)


def test_the_bundle_key_covers_every_directory_a_bundle_holds():
    """Two lists that must agree, in both directions.

    `BUNDLE_KEYS` is what a definition may describe and `ASSET_DIRECTORIES` is what
    the walk keeps out of the definition scan. A third asset kind added to one and
    not the other is either a folder nobody can describe or a key describing a folder
    that is read as a subagent, and neither has a symptom before it happens.
    """
    from kingfisher.kinds.subagents.catalogue import ASSET_DIRECTORIES
    from kingfisher.kinds.subagents.reading import BUNDLE_KEYS

    assert set(BUNDLE_KEYS) == set(ASSET_DIRECTORIES)


def test_a_definition_saying_nothing_is_not_asked_to(cfg):
    """Optional, and the shipped set is mostly definitions that write no `bundle:`."""
    echoing(cfg)

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.subagents.specs["surveyor"].bundle == {}


def test_a_listing_names_the_delegate_whose_echo_has_gone_stale(cfg):
    """A refusal reachable only through the constructor is one `list` cannot see, which
    is how `orphaned_assets` came to be computed for a year and printed never.
    """
    echoing(cfg, tools=["probe", "gone"])

    found = inventory(cfg)

    assert "gone" in found.miscounted_bundles["surveyor"]
    assert "gone" in "\n".join(_catalogue(found))
    assert failed(found), "the catalogue refuses this, so a zero exit would be a lie"


def test_doctor_names_the_delegate_whose_echo_has_gone_stale(cfg):
    """The exit code is held by `test_doctor_fails_on_every_refusal_a_file_can_reach`;
    what this adds is that the row says which definition to open.
    """
    echoing(cfg, tools=["probe", "gone"])

    named = {check.name: check for check in examine(cfg)}

    assert named["bundle claims"].verdict == "fail"
    assert "surveyor" in named["bundle claims"].detail


def test_a_clean_catalogue_says_so_rather_than_saying_nothing(cfg):
    """An absent row and a passing one look identical in a list of checks, and the
    absent one is what a check that stopped running looks like.
    """
    echoing(cfg, tools=["probe"])

    named = {check.name: check for check in examine(cfg)}

    assert named["bundle claims"].verdict == "ok"


# -- a compiled delegate's own folder ---------------------------------------


def compiled_bundle(cfg, *, skill=False):
    """A workspace whose `surveyor` is a graph in a folder holding its own tool."""
    folder = cfg.workspace / "subagents" / "surveyor"
    (folder / "tools").mkdir(parents=True, exist_ok=True)
    (folder / "surveyor.py").write_text(COMPILED, encoding="utf-8")
    (folder / "tools" / "probe.py").write_text(
        TOOL.format(name="probe", answer="from the bundle"), encoding="utf-8"
    )
    tools = cfg.workspace / "tools"
    tools.mkdir(exist_ok=True)
    (tools / "shared.py").write_text(
        TOOL.format(name="shared", answer="from the catalogue"), encoding="utf-8"
    )
    if skill:
        with_private_skill(cfg)


def dispatched_by(subagent) -> tuple[str, ...]:
    """What the graph a compiled delegate built will actually answer to.

    Read off the built graph rather than off what `build` was handed, which is the
    whole point: a tool passed to `build` and dropped on the floor is a delegate
    that does not have it, and the two look identical from this side of the call.
    """
    node = getattr(subagent["runnable"], "nodes", {}).get("tools")
    return tuple(sorted(getattr(getattr(node, "bound", None), "tools_by_name", None) or {}))


def test_a_compiled_delegate_is_handed_the_tools_in_its_own_folder(
    cfg, session_dir, monkeypatch
):
    """`compiled` took every parameter `as_subagent` resolves except this one, so a
    compiled delegate's bundle reached the listing and never the graph: `probe
    [private tool]` printed under a delegate that dispatched nothing.
    """
    compiled_bundle(cfg)
    captured = capture_build(monkeypatch)
    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",)),
    )

    assert "probe" in dispatched_by(only(captured, "surveyor"))


def test_a_compiled_delegates_bundle_wins_a_name_the_catalogue_also_defines(cfg, tmp_path):
    """The assembled path's rule, which has to hold here too: `build` is handed one
    list and whatever it builds dispatches by name, so two tools of a name is one tool
    and nothing saying which.

    Asserted on the list `build` *receives*, which is the only place the duplicate is
    visible. Through a built graph it is not: `tools_by_name` is a dict, so counting
    its keys can never see two, and the later entry wins by accident of order -- an
    earlier version of this test asserted exactly that and passed against a `compiled`
    that deduplicated nothing.
    """
    from langchain_core.runnables import RunnableLambda

    from kingfisher.infrastructure.harness.subagents import compiled
    from kingfisher.kinds.subagents.spec import SubagentSpec
    from kingfisher.kinds.tools.catalogue import LocalToolRepository

    # Two `probe` definitions answering differently, so the winner can be named.
    for where, answer in (
        (tmp_path / "shared", "from the catalogue"),
        (tmp_path / "own", "from the bundle"),
    ):
        where.mkdir()
        (where / "probe.py").write_text(TOOL.format(name="probe", answer=answer), encoding="utf-8")
    (tmp_path / "shared" / "other.py").write_text(
        TOOL.format(name="other", answer="ok"), encoding="utf-8"
    )

    handed: list = []

    def build(model, tools):
        handed.extend(tools)
        return RunnableLambda(lambda value: value)

    compiled(
        SubagentSpec(name="surveyor", description="A compiled subagent.", build=build),
        cfg,
        catalogue=LocalToolRepository(tmp_path / "shared").found,
        private=LocalToolRepository(tmp_path / "own").found,
    )

    # `tool_name` rather than an attribute: a workspace tool reaches `build` as
    # whatever its module exported, which for a plain function is the function.
    names = [tool_name(one) for one in handed]
    assert names.count("probe") == 1, "two tools of a name reached `build`"
    assert names[0] == "probe", "the bundle's goes first, so the order is stated here"
    assert handed[0]() == "from the bundle"
    assert "other" in names, "the catalogue's own still arrive"


def test_a_compiled_delegates_bundled_skill_is_reported_as_reaching_nothing(cfg):
    """A skills index arrives through middleware and a compiled graph is given none,
    so this half of a bundle cannot work -- while the other half now does, which is
    exactly why it needs saying: there is no symptom to notice.
    """
    compiled_bundle(cfg, skill=True)

    found = inventory(cfg)

    assert found.stranded_skills == {"surveyor": ("sampling",)}
    assert "told about no skills" in "\n".join(_catalogue(found))


def test_an_assembled_delegates_bundled_skill_is_not_reported_as_stranded(cfg):
    """The control. The warning is about graphs, not about bundles -- pointed at every
    bundled skill it would fire on the shipped `redactor`, whose skill arrives.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    found = inventory(cfg)

    assert found.stranded_skills == {}
    assert "told about no skills" not in "\n".join(_catalogue(found))


def test_doctor_warns_that_a_compiled_delegate_is_told_about_no_skills(cfg):
    """Warned and not failed: the delegate runs, and what it is missing is a procedure
    nobody told it about.
    """
    compiled_bundle(cfg, skill=True)

    checks = examine(cfg)
    named = {check.name: check for check in checks}

    assert named["delegate bundles"].verdict == "warn"
    assert "sampling" in named["delegate bundles"].detail
    assert worst(checks) != "fail"


#: The same delegate, describing the folder it sits in. A dict rather than YAML,
#: because a compiled delegate is only ever declared in Python.
COMPILED_ECHOING = """
def build(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)


SUBAGENTS = [
    {
        "name": "surveyor",
        "description": "A compiled subagent.",
        "build": build,
        "bundle": {"tools": [%r]},
    }
]
"""


def test_a_compiled_delegate_may_describe_its_own_folder(cfg):
    """`bundle:` was refused here on the argument that a compiled graph never sees
    its folder. Half of that stopped being true when its tools started reaching
    `build`, and `NOT_COMPILED` is for keys that would do nothing -- so a reason that
    no longer holds is a refusal with nothing behind it.
    """
    compiled_bundle(cfg)
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.py"
    definition.write_text(COMPILED_ECHOING % "probe", encoding="utf-8")

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.subagents.specs["surveyor"].bundle == {"tools": ("probe",)}


def test_a_compiled_delegate_is_held_to_what_it_describes(cfg):
    """Held rather than merely permitted, which is the only reason to allow the key:
    a definition that may write it and is never checked is decoration.
    """
    compiled_bundle(cfg)
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.py"
    definition.write_text(COMPILED_ECHOING % "gone", encoding="utf-8")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "gone" in str(raised.value)
    assert "surveyor/tools/" in str(raised.value)


# -- the one that ships -----------------------------------------------------


def test_the_shipped_bundle_is_a_bundle(shipped):
    """`kingfisher seed` should produce a working example of every shape the formats doc
    describes, and this is the one a reader copies.
    """
    repository = LocalSubagentRepository(shipped / "subagents")
    bundles = repository.bundles

    assert set(bundles) == {"redactor"}
    assert bundles["redactor"].tools is not None
    assert bundles["redactor"].skills is not None
    # The neighbour that is *not* one, shipped beside it on purpose: a
    # folder naming no definition is organisation and stays so.
    assert "profiler" in repository.specs
    assert repository.orphaned_assets == ()


def test_the_shipped_bundle_says_what_it_holds(workspace_with_presets):
    """The example is where a reader meets this, and `assets_examples/` is held to
    working -- so the check is driven by reading the shipped catalogue rather than by
    comparing the definition against a list written here, which would pass against
    itself however wrong both were.
    """
    catalogue = Definitions.from_config(workspace_with_presets).warm()
    spec = catalogue.subagents.specs["redactor"]

    assert spec.bundle == {"tools": ("mask_secrets",), "skills": ("redaction",)}
    # Driven: `warm` above is what refuses a stale echo, so reaching this line is the
    # assertion. Named anyway, because a `warm()` whose refusal moved elsewhere would
    # leave the two lines above passing on a definition nothing checked.
    assert inventory(workspace_with_presets).miscounted_bundles == {}


def test_the_shipped_bundles_tool_loads_and_masks(tmp_path, shipped):
    """An example is judged by whether an agent can run it, not by this package's
    layering -- so it is imported and called.
    """
    from kingfisher.kinds.tools.catalogue import LocalToolRepository

    found = LocalToolRepository(shipped / "subagents" / "redactor" / "tools").found

    (tool,) = found
    assert tool.name == "mask_secrets"

    target = tmp_path / "config.ini"
    target.write_text("api_key = sk-live-123\nhost = example.com\n", encoding="utf-8")
    answer = tool.tool(str(target))

    assert "sk-live-123" not in answer
    assert "1 masked" in answer


def test_the_shipped_bundle_takes_nothing_from_the_catalogue(
    workspace_with_presets, session_dir, monkeypatch
):
    """`redactor.yaml` writes `tools: []`, so it holds its own tool and no shared one.

    Without the line it held every catalogue tool, `sql_query` and `http_fetch` among
    them, on the delegate whose job is being careful with what it returns.
    """
    captured = capture_build(monkeypatch)
    build_agent(
        workspace_with_presets,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("redactor",)),
    )

    (allowlist,) = [
        m for m in only(captured, "redactor")["middleware"] if isinstance(m, ToolAllowlist)
    ]
    assert set(allowlist._allowed) == {"ls", "glob", "grep", "mask_secrets"}

"""A subagent's own tools and skills, kept in a folder named after it."""

from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.inventory import inventory
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import default_backend, skills_sources
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.kinds.agents import reading as agent_reading
from kingfisher.kinds.agents.spec import AgentError
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.kinds.tools.catalogue import ToolError
from kingfisher.kinds.tools.spec import Offering, tool_name
from kingfisher.layout import BUNDLED_SKILLS_ROUTE, SKILLS_ROUTE, denied_scopes
from kingfisher.presentation.cli.health import examine, worst
from kingfisher.presentation.cli.listing import _catalogue, failed
from tests.conftest import FakeToolCallingModel, a_subagent

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
    # `.root`, because `.tools` is the repository reading that folder rather than the
    # folder itself -- a bundle has two backings now, and only one of them is a path.
    assert bundles["surveyor"].tools.root == tmp_path / "surveyor" / "tools"
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


def owner(*, shared=None, tools=("probe",), skills=()):
    """`surveyor`, listing `shared` as written and `tools`/`skills` from its own folder.

    `shared` entries are YAML as written -- `'"*"'` for every catalogue tool -- and
    `None` leaves the line to hold only the bundled ones, so a definition listing
    nothing at all has no `tools:` line and inherits.
    """
    lines = ["name: surveyor", "description: Surveys files."]
    for field, plain, own in (("tools", shared or (), tools), ("skills", (), skills)):
        entries = [*plain, *(f"{{name: {one}, source: bundled}}" for one in own)]
        if entries:
            lines.append(f"{field}: [{', '.join(entries)}]")
    return "\n".join([*lines, "system_prompt: |", "  You survey.", ""])


PRIVATE_OWNER = owner(shared=("shared",))
OWN_TOOL_ONLY = owner()
OWN_TOOL_AND_SKILL = owner(skills=("sampling",))


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


def only(built, name):
    """The delegate we are asking about, as deepagents received it.

    Takes the record and returns one of deepagents' `SubAgent` mappings, so the
    outer read is an attribute and everything the callers do with the result stays
    a subscript. Imported by `test_portable_subagents` too, which is why the
    parameter moved rather than the call sites.
    """
    (found,) = [s for s in built.subagents if s["name"] == name]
    return found


def built_subagent(cfg, session_dir):
    """The one delegate this workspace defines, as deepagents received it."""
    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",), tools=("shared",)),
    )
    return only(built, "surveyor")


def test_a_delegate_holds_the_tool_from_its_own_folder(cfg, session_dir):
    """The request granted `shared` and never heard of `probe`."""
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir)

    assert {tool_name(t) for t in subagent["tools"]} == {"probe", "shared"}


def test_a_private_tool_survives_a_request_that_granted_no_tools(cfg, session_dir):
    """The decision, stated as a test."""
    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)

    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",), tools=()),
    )

    subagent = only(built, "surveyor")
    assert {tool_name(t) for t in subagent["tools"]} == {"probe"}


def test_only_what_the_definition_lists_reaches_the_delegate(cfg, session_dir):
    """A build that never ran `warm` is handed the folder as it is, so a file the
    definition does not list would reach the delegate if the build trusted the
    folder -- `warm` refusing the pair does not help a caller that skipped it.
    """
    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)
    unlisted = cfg.workspace / "subagents" / "surveyor" / "tools" / "later.py"
    unlisted.write_text(TOOL.format(name="later", answer="ok"), encoding="utf-8")

    subagent = built_subagent(cfg, session_dir)

    assert {tool_name(t) for t in subagent["tools"]} == {"probe"}


def test_a_private_tool_is_in_the_delegates_allowlist(cfg, session_dir):
    """The failure this would otherwise have been is silent rather than absent."""
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir)

    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, ToolAllowlist)]
    assert "probe" in allowlist._allowed
    assert "shared" in allowlist._allowed


#: Every catalogue tool, and a `shared` of its own that answers for the catalogue's.
INHERITS_AND_OWNS_SHARED = owner(shared=('"*"',), tools=("shared",))


def test_a_bundled_tool_answers_for_its_name_when_the_catalogue_is_inherited(
    cfg, session_dir
):
    """`*` takes the catalogue's `shared` and the list takes the folder's, and a
    delegate dispatches by name -- so one has to go, and the one the definition named
    `source: bundled` is the one that stays.
    """
    workspace_with_bundle(cfg, definition=INHERITS_AND_OWNS_SHARED, private="shared")

    subagent = built_subagent(cfg, session_dir)

    (held,) = subagent["tools"]
    assert tool_name(held) == "shared"
    # Which of the two files answered, since both define a `shared`.
    assert held() == "from the bundle"


def test_one_name_listed_as_both_shared_and_bundled_is_refused(cfg):
    """Two entries for one name leave which one the delegate calls unsaid."""
    workspace_with_bundle(
        cfg, definition=owner(shared=("shared",), tools=("shared",)), private="shared"
    )

    with pytest.raises(SubagentError) as raised:
        _ = Definitions.from_config(cfg).subagents.specs

    assert "names 'shared' twice" in str(raised.value)


def test_the_main_agent_never_holds_another_delegates_private_tool(cfg, session_dir):
    """The point of the whole feature."""
    workspace_with_bundle(cfg)

    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",)),
    )

    assert "probe" not in {tool_name(t) for t in built.tools or ()}
    assert "shared" in {tool_name(t) for t in built.tools or ()}


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


def test_a_delegate_is_told_about_the_skill_in_its_own_folder(cfg, session_dir):
    """Rendered rather than read off `_allowed`, and that is the whole guard: the
    grant was recorded under the label the bundle was *read* under and the index
    is keyed by the label it is *mounted* under, so this delegate was handed
    "No skills available yet" while `_allowed` looked right.
    """
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)

    subagent = built_subagent(cfg, session_dir)

    (narrowed,) = [m for m in subagent["middleware"] if isinstance(m, NarrowedSkills)]
    assert any(key.endswith("sampling") for key in narrowed._allowed)
    assert "sampling" in narrowed._format_skills_list(narrowed._qualified())


def test_a_skill_the_definition_does_not_list_is_not_indexed(cfg, session_dir):
    """The skills half of listing being what grants, for a build that skipped `warm`."""
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)
    with_private_skill(cfg, name="unlisted")

    subagent = built_subagent(cfg, session_dir)

    (narrowed,) = [m for m in subagent["middleware"] if isinstance(m, NarrowedSkills)]
    assert any(key.endswith("sampling") for key in narrowed._allowed)
    assert not any(key.endswith("unlisted") for key in narrowed._allowed)


def test_a_bundles_skill_is_mounted_read_only(cfg, session_dir):
    """The route sits under `/skills/` for exactly this reason."""
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)

    backend = default_backend(cfg, session_dir)

    mounted = [route for route in backend.routes if route.startswith(BUNDLED_SKILLS_ROUTE)]
    assert mounted == ["/skills/subagents/surveyor/"]
    assert all(route.startswith(SKILLS_ROUTE) for route in mounted)


def test_a_bundles_skills_add_a_mount_and_no_rule(cfg, session_dir):
    """A mount per bundle, and still one deny rule for all of `/skills/`."""
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
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
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
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
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)

    found = inventory(cfg)

    assert found.bundled_tools["surveyor"] == ("probe",)
    assert found.bundled_skills["surveyor"] == ("sampling",)
    printed = "\n".join(_catalogue(found))
    assert "probe  [private tool]" in printed
    assert "sampling  [private skill]" in printed


def test_a_listing_says_when_a_bundle_shadows_the_catalogue(cfg):
    """Shadowing is only acceptable while it is visible."""
    workspace_with_bundle(cfg, definition=INHERITS_AND_OWNS_SHARED, private="shared")

    found = inventory(cfg)

    assert found.shadowed["surveyor"] == ("shared",)
    assert "shadowing the catalogue's" in "\n".join(_catalogue(found))


def renamed(cfg):
    """Rename a working `surveyor` out from under its folder."""
    definition = cfg.workspace / "subagents" / "surveyor" / "surveyor.yaml"
    definition.write_text(
        definition.read_text(encoding="utf-8").replace("name: surveyor", "name: surveys"),
        encoding="utf-8",
    )


def test_a_listing_names_the_folder_whose_definition_was_renamed(cfg):
    """A renamed definition takes its bundle with it, which is the failure
    `orphaned_assets` was written for and nothing printed for as long as it existed.

    Driven by renaming a bundle that works, rather than by a folder built orphaned,
    so the loss is the same one a reader would hit.
    """
    workspace_with_bundle(cfg)
    renamed(cfg)

    found = inventory(cfg)

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


def orphaned_folder(cfg):
    """A grouping folder holding a `tools/` that no definition is named for."""
    define(cfg.workspace / "subagents" / "analysis", "profiler")
    tools = cfg.workspace / "subagents" / "analysis" / "tools"
    tools.mkdir()
    (tools / "probe.py").write_text(PROBE, encoding="utf-8")


def test_an_orphaned_bundle_does_not_make_the_listing_non_zero(cfg):
    """Legal, and the split `misfiled` already draws: a folder naming no definition
    is reported, never refused, so a deployment that meant it still exits zero.

    A grouping folder rather than a rename, because a renamed definition that lists
    bundled entries is refused from its own side -- which is the next section.
    """
    orphaned_folder(cfg)

    found = inventory(cfg)

    assert found.orphaned_assets == ("analysis",)
    assert not failed(found)


def test_doctor_warns_about_an_orphaned_bundle_and_does_not_fail_on_it(cfg):
    """Its own check name rather than `delegate tools`: the folder may hold only
    `skills/`, and a row naming the wrong half sends a reader looking for a tool
    that was never there.
    """
    orphaned_folder(cfg)

    checks = examine(cfg)
    named = {check.name: check for check in checks}

    assert named["delegate bundles"].verdict == "warn"
    assert "analysis/" in named["delegate bundles"].detail
    assert worst(checks) != "fail", "a folder naming no definition is legal"


def test_doctor_names_the_delegate_whose_private_tools_will_not_load(cfg):
    """Its own check rather than folded into `tools`, for the reason the field is
    its own: a reader has to be told which delegate to go and open.
    """
    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)
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
    workspace_with_bundle(cfg, definition=INHERITS_AND_OWNS_SHARED, private="shared")

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
    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)
    bundle = cfg.workspace / "subagents" / "surveyor" / "tools"
    (bundle / "probe.py").write_text(BROKEN, encoding="utf-8")

    found = inventory(cfg)

    assert found.bundles_error is not None
    assert failed(found)


def test_a_broken_bundle_does_not_hide_the_rest_of_the_listing(cfg):
    """The other half of the same bug: one bad tool printed one section of four."""
    workspace_with_bundle(cfg, definition=OWN_TOOL_ONLY)
    (cfg.workspace / "subagents" / "surveyor" / "tools" / "probe.py").write_text(
        BROKEN, encoding="utf-8"
    )

    found = inventory(cfg)

    assert "surveyor" in found.subagents
    assert found.subagents_error is None
    assert "shared" in found.tools


# -- the list and the folder, held to each other ----------------------------


def listing(cfg, tools=("probe",), skills=(), *, skill=False):
    """A `surveyor` bundle whose definition lists the given names as `source: bundled`."""
    workspace_with_bundle(cfg, definition=owner(tools=tools, skills=skills))
    if skill:
        with_private_skill(cfg)


def test_a_definition_that_lists_its_folder_loads(cfg):
    """The control, and it has to come first: every refusal below would also fire on a
    definition that simply cannot be read, and then none of them would be about the
    list at all.
    """
    listing(cfg, tools=("probe",), skills=("sampling",), skill=True)

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.subagents.specs["surveyor"].bundled == {
        "tools": ("probe",),
        "skills": ("sampling",),
    }


def test_a_name_the_folder_does_not_hold_is_refused(cfg):
    """The stale direction: a definition still naming a tool somebody deleted."""
    listing(cfg, tools=("probe", "gone"))

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "gone" in str(raised.value)
    assert "surveyor/tools/" in str(raised.value)


def test_a_tool_the_folder_gained_is_refused(cfg):
    """The direction a subset check would let through: a tool dropped into the folder
    and not listed. Granting it would be a capability arriving with no line changed;
    ignoring it is the silent loss the next test is about.
    """
    listing(cfg, tools=("probe",))
    arrived = cfg.workspace / "subagents" / "surveyor" / "tools" / "later.py"
    arrived.write_text(TOOL.format(name="later", answer="ok"), encoding="utf-8")

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "later" in str(raised.value)
    assert "tools: does not list" in str(raised.value)


def test_a_folder_the_definition_lists_nothing_from_is_refused(cfg):
    """The upgrade. Every bundle written while the folder granted by itself has a
    definition listing nothing, and read under the new rule each would quietly lose
    its tools -- so it is refused, and the refusal says what to write.
    """
    listing(cfg, tools=())

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "probe" in str(raised.value)
    assert "source: bundled" in str(raised.value)


def test_a_skill_the_folder_holds_and_the_definition_does_not_list_is_refused(cfg):
    """Both halves, or the second is a line that reads like a check and is not one."""
    listing(cfg, tools=("probe",), skill=True)

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "sampling" in str(raised.value)
    assert "skills: does not list" in str(raised.value)


def test_a_definition_that_lists_from_a_folder_it_no_longer_owns_is_refused(cfg):
    """The rename, from the side the definition is on.

    `orphaned_assets` sees the folder left behind and cannot see what the file thought
    it had; this is the only place the two halves of that rename meet, and without it
    a renamed bundle is a delegate that quietly holds nothing.
    """
    listing(cfg, tools=("probe",))
    renamed(cfg)

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "owns no folder" in str(raised.value)


def test_a_rename_is_reported_from_both_sides(cfg):
    """The folder left behind and the file that walked away from it, which are
    different facts: the orphan report knows a folder reaches nobody and cannot know
    what was supposed to be in it, and this knows what the definition thought it had
    and not that the folder is still sitting there.
    """
    listing(cfg, tools=("probe",))
    renamed(cfg)

    found = inventory(cfg)
    named = {check.name: check for check in examine(cfg)}

    assert found.orphaned_assets == ("surveyor",)
    assert "owns no folder" in found.miscounted_bundles["surveys"]
    assert named["delegate bundles"].verdict == "warn"
    assert named["bundled entries"].verdict == "fail"


def test_a_definition_with_an_empty_folder_is_not_asked_to_list_anything(cfg):
    """A folder named after its definition that holds no assets is still a bundle,
    and there is nothing in it to list.
    """
    define(cfg.workspace / "subagents" / "surveyor", "surveyor")

    catalogue = Definitions.from_config(cfg).warm()

    assert not any(catalogue.subagents.specs["surveyor"].bundled.values())


def read_surveyor(tools_line):
    """One definition with the given `tools:` line, read without a catalogue."""
    return a_subagent(
        f"name: surveyor\ndescription: d\n{tools_line}\nsystem_prompt: |\n  x\n",
        "surveyor.yaml",
    )


def test_source_shared_means_the_plain_name():
    """Written out, so a definition can say it beside a bundled entry; read the same."""
    spec = read_surveyor("tools: [{name: shared, source: shared}, {name: probe, source: bundled}]")

    assert spec.tools == ("shared",)
    assert spec.bundled["tools"] == ("probe",)


def test_a_source_that_is_neither_is_refused():
    """A misspelt `bundled` read as shared would grant the catalogue's instead."""
    with pytest.raises(SubagentError) as raised:
        read_surveyor("tools: [{name: probe, source: private}]")

    assert "'shared'" in str(raised.value)
    assert "'bundled'" in str(raised.value)


def test_a_bundled_entry_has_no_audience_of_its_own():
    """A bundled entry arrives with the delegate whatever the caller holds, so an
    audience on it would read as a restriction that nothing applies.
    """
    with pytest.raises(SubagentError) as raised:
        read_surveyor("tools: [{name: probe, source: bundled, source_ids: [A]}]")

    assert "reaches whoever reaches this delegate" in str(raised.value)


def test_a_bundled_entry_names_no_file():
    """The folder is where a bundled entry lives, so a path before `::` could only
    disagree with it.
    """
    with pytest.raises(SubagentError) as raised:
        read_surveyor("tools: [{name: probe.py::probe, source: bundled}]")

    assert "Write the name alone" in str(raised.value)


def test_the_old_bundle_key_says_what_to_write_instead():
    """Every deployment upgrading meets this line, so it carries the new spelling and
    the one change in meaning a list brings: only what is listed arrives.
    """
    with pytest.raises(SubagentError) as raised:
        read_surveyor("bundle:\n  tools: [probe]")

    assert "source: bundled" in str(raised.value)
    assert "'*'" in str(raised.value)


def test_an_agent_may_not_list_a_bundled_entry():
    """An agent has no folder of its own, so `bundled` there could only mean nothing."""
    with pytest.raises(AgentError) as raised:
        agent_reading.read(
            "name: a\ndescription: d\ntools: [{name: probe, source: bundled}]\n"
            "system_prompt: |\n  x\n",
            Path("a.yaml"),
        )

    assert "no folder of its own" in str(raised.value)


def test_an_agent_may_say_an_entry_is_shared():
    """The control: the key reads the same in either file, and only `bundled` parts."""
    spec = agent_reading.read(
        "name: a\ndescription: d\ntools: [{name: shared, source: shared}]\n"
        "system_prompt: |\n  x\n",
        Path("a.yaml"),
    )

    assert spec.tools == ("shared",)


def test_the_bundle_key_covers_every_directory_a_bundle_holds():
    """Two lists that must agree, in both directions.

    `BUNDLE_KEYS` is what a delegate may take from its folder and `ASSET_DIRECTORIES`
    is what the walk keeps out of the definition scan. A third asset kind added to one
    and not the other is either a folder nobody can list from or a key naming a folder
    that is read as a subagent, and neither has a symptom before it happens.
    """
    from kingfisher.kinds.subagents.catalogue import ASSET_DIRECTORIES
    from kingfisher.kinds.subagents.spec import BUNDLE_KEYS

    assert set(BUNDLE_KEYS) == set(ASSET_DIRECTORIES)


def test_a_listing_names_the_delegate_whose_list_has_gone_stale(cfg):
    """A refusal reachable only through the constructor is one `list` cannot see, which
    is how `orphaned_assets` came to be computed for a year and printed never.
    """
    listing(cfg, tools=("probe", "gone"))

    found = inventory(cfg)

    assert "gone" in found.miscounted_bundles["surveyor"]
    assert "gone" in "\n".join(_catalogue(found))
    assert failed(found), "the catalogue refuses this, so a zero exit would be a lie"


def test_doctor_names_the_delegate_whose_list_has_gone_stale(cfg):
    """The exit code is held by `test_doctor_fails_on_every_refusal_a_file_can_reach`;
    what this adds is that the row says which definition to open.
    """
    listing(cfg, tools=("probe", "gone"))

    named = {check.name: check for check in examine(cfg)}

    assert named["bundled entries"].verdict == "fail"
    assert "surveyor" in named["bundled entries"].detail


def test_a_clean_catalogue_says_so_rather_than_saying_nothing(cfg):
    """An absent row and a passing one look identical in a list of checks, and the
    absent one is what a check that stopped running looks like.
    """
    listing(cfg, tools=("probe",))

    named = {check.name: check for check in examine(cfg)}

    assert named["bundled entries"].verdict == "ok"


# -- a compiled delegate's own folder ---------------------------------------


#: `COMPILED`, listing what it takes from its folder. A dict rather than YAML,
#: because a compiled delegate is only ever declared in Python.
COMPILED_LISTING = """
def build(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)


SUBAGENTS = [
    {
        "name": "surveyor",
        "description": "A compiled subagent.",
        "build": build,
        "tools": %s,
    }
]
"""

#: Every catalogue tool, and `probe` from its own folder.
INHERITS_AND_OWNS_PROBE = '["*", {"name": "probe", "source": "bundled"}]'


def compiled_bundle(cfg, *, skill=False, tools=INHERITS_AND_OWNS_PROBE):
    """A workspace whose `surveyor` is a graph in a folder holding its own tool."""
    folder = cfg.workspace / "subagents" / "surveyor"
    (folder / "tools").mkdir(parents=True, exist_ok=True)
    (folder / "surveyor.py").write_text(COMPILED_LISTING % tools, encoding="utf-8")
    (folder / "tools" / "probe.py").write_text(
        TOOL.format(name="probe", answer="from the bundle"), encoding="utf-8"
    )
    shared = cfg.workspace / "tools"
    shared.mkdir(exist_ok=True)
    (shared / "shared.py").write_text(
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


def test_a_compiled_delegate_is_handed_the_tools_in_its_own_folder(cfg, session_dir):
    """`compiled` took every parameter `as_subagent` resolves except this one, so a
    compiled delegate's bundle reached the listing and never the graph: `probe
    [private tool]` printed under a delegate that dispatched nothing.
    """
    compiled_bundle(cfg)
    built = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("surveyor",)),
    )

    assert "probe" in dispatched_by(only(built, "surveyor"))


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
        SubagentSpec(
            name="surveyor",
            description="A compiled subagent.",
            system_prompt="",
            build=build,
        ),
        cfg,
        catalogue=LocalToolRepository(tmp_path / "shared").found,
        private=LocalToolRepository(tmp_path / "own").found,
    )

    # `tool_name` rather than an attribute, and invoked rather than called: a compiled
    # delegate's tools arrive wrapped, so a plain function reaches `build` as the tool
    # `create_agent` would have made of it rather than as the function itself.
    names = [tool_name(one) for one in handed]
    assert names.count("probe") == 1, "two tools of a name reached `build`"
    assert names[0] == "probe", "the bundle's goes first, so the order is stated here"
    assert handed[0].invoke({}) == "from the bundle"
    assert "other" in names, "the catalogue's own still arrive"


def test_a_compiled_delegates_bundled_skill_is_reported_as_reaching_nothing(cfg):
    """A skills index arrives through middleware and a compiled graph is given none,
    so this half of a bundle cannot work -- and the listing says so on the line, beside
    the refusal below, so a reader learns why rather than only that.
    """
    compiled_bundle(cfg, skill=True)

    found = inventory(cfg)

    assert found.stranded_skills == {"surveyor": ("sampling",)}
    assert "told about no skills" in "\n".join(_catalogue(found))


def test_an_assembled_delegates_bundled_skill_is_not_reported_as_stranded(cfg):
    """The control. The note is about graphs, not about bundles -- pointed at every
    bundled skill it would fire on the shipped `redactor`, whose skill arrives.
    """
    workspace_with_bundle(cfg, definition=OWN_TOOL_AND_SKILL)
    with_private_skill(cfg)

    found = inventory(cfg)

    assert found.stranded_skills == {}
    assert "told about no skills" not in "\n".join(_catalogue(found))


def test_a_compiled_delegate_with_a_skill_in_its_folder_is_refused(cfg):
    """It cannot list the skill -- `skills` is refused for a graph deepagents never
    indexes -- so the skill is unlisted, and an unlisted file is refused.
    """
    compiled_bundle(cfg, skill=True)

    with pytest.raises(SubagentError) as raised:
        Definitions.from_config(cfg).warm()

    assert "sampling" in str(raised.value)


def test_doctor_fails_on_a_compiled_delegate_with_a_skill_in_its_folder(cfg):
    """Failed rather than warned, because startup refuses it now."""
    compiled_bundle(cfg, skill=True)

    named = {check.name: check for check in examine(cfg)}

    assert named["bundled entries"].verdict == "fail"
    assert "sampling" in named["bundled entries"].detail


def test_a_compiled_delegate_lists_from_its_own_folder(cfg):
    """The same entries a document writes, in a dict: a compiled delegate owns a folder
    like any other, and its tools reach `build`.
    """
    compiled_bundle(cfg)

    catalogue = Definitions.from_config(cfg).warm()

    assert catalogue.subagents.specs["surveyor"].bundled == {
        "tools": ("probe",),
        "skills": (),
    }


def test_a_compiled_delegate_is_held_to_what_it_lists(cfg):
    """Held rather than merely permitted: a list that is never checked is decoration."""
    compiled_bundle(cfg, tools='[{"name": "gone", "source": "bundled"}]')

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

    # Both shapes ship, and the pair is the assertion: `redactor` owns a folder named
    # after it, `timestamps` carries what a folder would have held. A reader meets the
    # two side by side, and dropping either would leave the other looking like the
    # only way a subagent can own anything.
    assert set(bundles) == {"redactor", "timestamps"}
    assert bundles["redactor"].tools is not None
    assert bundles["redactor"].skills is not None
    assert bundles["redactor"].root is not None
    assert bundles["timestamps"].root is None
    assert bundles["timestamps"].tools is not None
    assert bundles["timestamps"].skills is not None
    # The neighbour that is *not* one, shipped beside it on purpose: a
    # folder naming no definition is organisation and stays so.
    assert "profiler" in repository.specs
    assert repository.orphaned_assets == ()


def test_the_shipped_bundle_lists_what_it_holds(workspace_with_presets):
    """The example is where a reader meets this, and `assets_examples/` is held to
    working -- so the check is driven by reading the shipped catalogue rather than by
    comparing the definition against a list written here, which would pass against
    itself however wrong both were.
    """
    catalogue = Definitions.from_config(workspace_with_presets).warm()
    spec = catalogue.subagents.specs["redactor"]

    assert spec.bundled == {"tools": ("mask_secrets",), "skills": ("redaction",)}
    # Driven: `warm` above is what refuses a stale list, so reaching this line is the
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
    workspace_with_presets, session_dir
):
    """`redactor.yaml` lists only its own tool, so it holds no shared one.

    Without a `tools:` line it held every catalogue tool, `sql_query` and `http_fetch`
    among them, on the delegate whose job is being careful with what it returns.
    """
    built = build_agent(
        workspace_with_presets,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(subagents=("redactor",)),
    )

    (allowlist,) = [
        m for m in only(built, "redactor")["middleware"] if isinstance(m, ToolAllowlist)
    ]
    assert set(allowlist._allowed) == {"ls", "glob", "grep", "mask_secrets"}

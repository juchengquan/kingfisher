"""A subagent's own tools and skills, kept in a folder named after it.

A folder under `subagents/` is normally organisation and nothing else. It
becomes a bundle when it holds a definition whose `name` is the folder's --
which is the same directory-name-against-declared-name relationship
`skill_registry.misfiled` already watches, decided here rather than reported,
because kingfisher owns this format and deepagents owns that one.

What hangs on it is capability, not tidiness. An agent that omits `tools:` gets
every tool there is, so anything in the shared `tools/` is a tool the top-level
agent holds; a tool in a bundle reaches its own delegate and nobody else.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.inventory import inventory
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.layered import for_session
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import (
    BUNDLED_SKILLS_ROUTE,
    SKILLS_ROUTE,
    build_backend,
    skills_sources,
)
from kingfisher.infrastructure.harness.narrowing import NarrowedSkills, ToolAllowlist
from kingfisher.presentation.cli.listing import _catalogue, failed
from kingfisher.subagents.catalogue import LocalSubagentRepository
from kingfisher.subagents.spec import SubagentError
from kingfisher.tools.catalogue import ToolError
from kingfisher.tools.spec import Offering, tool_name
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
    """The whole rule, and the only one. The folder is `surveyor`, the
    definition inside says `name: surveyor`, so what is in it is surveyor's.
    """
    define(tmp_path / "surveyor", "surveyor")
    (tmp_path / "surveyor" / "tools").mkdir()
    (tmp_path / "surveyor" / "skills" / "sampling").mkdir(parents=True)

    bundles = LocalSubagentRepository(tmp_path).bundles

    assert set(bundles) == {"surveyor"}
    assert bundles["surveyor"].tools == tmp_path / "surveyor" / "tools"
    assert bundles["surveyor"].skills == tmp_path / "surveyor" / "skills"
    assert bundles["surveyor"].where == "surveyor"


def test_a_definition_sitting_loose_has_no_bundle(tmp_path):
    """`subagents/reviewer.yaml` has no folder to be named after, and the simple
    case has to stay simple -- a definition should not need a directory.
    """
    define(tmp_path, "reviewer")

    assert LocalSubagentRepository(tmp_path).bundles == {}


def test_a_grouping_folder_is_not_a_bundle(tmp_path):
    """`analysis/profiler.yaml` is the shape the shipped catalogue already uses.
    A folder that names no definition groups them, exactly as it always has.
    """
    define(tmp_path / "analysis", "profiler")

    repository = LocalSubagentRepository(tmp_path)

    assert repository.bundles == {}
    assert set(repository.specs) == {"profiler"}


def test_a_bundle_folder_is_keyed_the_way_a_grant_names_it(tmp_path):
    """Two catalogues may each ship a `surveyor`, so `specs` qualifies the name.
    Anything keyed differently would hand a caller a name from one map that
    finds nothing in the other.
    """
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "other" / "surveyor", "surveyor")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.bundles) == set(repository.specs)
    assert len(repository.bundles) == 2


def test_a_subagent_declared_in_python_gets_no_bundle(tmp_path):
    """`where` is then a module path, and the folder it names is a package whose
    `__init__.py` decides what it exports. A package that also held a `tools/`
    would be saying two different things with one directory.
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


# -- the ambiguity that has to be refused -----------------------------------


def test_a_bundle_folder_holding_a_second_definition_is_refused(tmp_path):
    """There is no honest answer to "is `helper` inside the bundle or beside
    it", and the two answers differ in what `helper` may call. That makes it a
    question about capability rather than tidiness, so it is refused instead of
    guessed.
    """
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "surveyor", "helper")

    with pytest.raises(SubagentError) as raised:
        _ = LocalSubagentRepository(tmp_path).bundles

    message = str(raised.value)
    assert "surveyor/helper.yaml" in message
    assert "surveyor/surveyor.yaml" in message


def test_the_refusal_says_what_is_at_stake(tmp_path):
    """A message naming two files and not the consequence sends someone to
    rename something without knowing which way. What hangs on it is reach.
    """
    define(tmp_path / "surveyor", "surveyor")
    define(tmp_path / "surveyor", "helper")

    with pytest.raises(SubagentError) as raised:
        _ = LocalSubagentRepository(tmp_path).bundles

    assert "surveyor/tools" in str(raised.value)


# -- the one that is reported, not refused ----------------------------------


def test_assets_under_a_folder_no_definition_claims_are_reported(tmp_path):
    """Legal -- a grouping folder may have directories in it -- and nine times
    in ten a bundle whose definition was renamed. Refusing would fail a working
    catalogue; saying nothing leaves a delegate holding nothing and no symptom.
    """
    define(tmp_path / "analysis", "profiler")
    (tmp_path / "analysis" / "tools").mkdir()

    repository = LocalSubagentRepository(tmp_path)

    assert repository.orphaned_assets == ("analysis",)
    assert repository.bundles == {}


def test_a_real_bundle_is_not_reported_as_orphaned(tmp_path):
    """The other half. A rule that named every folder with a `tools/` would be
    noise, and noise is what gets scrolled past.
    """
    define(tmp_path / "surveyor", "surveyor")
    (tmp_path / "surveyor" / "tools").mkdir()

    assert LocalSubagentRepository(tmp_path).orphaned_assets == ()


# -- what a bundle's own directories may hold -------------------------------


def test_a_skills_config_file_is_not_read_as_a_subagent(tmp_path):
    """The hazard the reserved names exist for. A skill may keep whatever it
    needs beside `SKILL.md`, and `config.yaml` is an ordinary thing to keep --
    which the definition walk would otherwise parse, fail on, and take the whole
    catalogue down over a file that was never a definition.
    """
    define(tmp_path / "surveyor", "surveyor")
    sampling = tmp_path / "surveyor" / "skills" / "sampling"
    sampling.mkdir(parents=True)
    (sampling / "config.yaml").write_text("rows: 100\n", encoding="utf-8")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.specs) == {"surveyor"}


def test_a_yaml_beside_a_bundles_tools_is_not_read_either(tmp_path):
    """Same reason, other directory. A tool is Python and may sit next to its
    own fixtures.
    """
    define(tmp_path / "surveyor", "surveyor")
    tools = tmp_path / "surveyor" / "tools"
    tools.mkdir()
    (tools / "fixtures.yaml").write_text("a: 1\n", encoding="utf-8")

    assert set(LocalSubagentRepository(tmp_path).specs) == {"surveyor"}


def test_the_reserved_names_are_skipped_wherever_they_appear(tmp_path):
    """Not scoped to bundles, and deliberately: knowing whether a folder is a
    bundle means reading the definition that decides it, which is the walk this
    rule is part of. What it costs is a grouping folder called `tools`.
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
    """The name is the one a grant would use, so a caller holding a subagent
    name from `specs` has a key that finds its tools here.
    """
    catalogue = catalogue_with_bundle(tmp_path)

    assert set(catalogue.bundled_tools) == {"surveyor"}
    assert [f.name for f in catalogue.bundled_tools["surveyor"].found] == ["probe"]


def test_a_bundles_tool_is_not_in_the_shared_offering(tmp_path):
    """The whole feature in one assertion. An agent omitting `tools:` gets
    every tool there is, so a tool that reached this offering would be a tool
    the top-level agent holds -- which is the thing a bundle exists to prevent.
    """
    catalogue = catalogue_with_bundle(tmp_path)

    offered = Offering.of(catalogue.tools.found)

    assert "shared" in offered.workspace
    assert "probe" not in offered.workspace


def test_a_broken_private_tool_fails_at_startup(tmp_path):
    """The existing rule, not a new one: a broken tool exits 1, a broken skill
    does not. A deployment that starts, reports itself fine, and fails on the
    first request that happens to activate `surveyor` is the shape of the bug
    `list` exiting zero over a broken agent catalogue already was.
    """
    catalogue = catalogue_with_bundle(tmp_path, tool=BROKEN)

    with pytest.raises(ToolError):
        catalogue.warm()


def test_a_catalogue_with_no_bundles_has_no_bundled_tools(tmp_path):
    """A deployment that never writes one pays nothing, and a store-backed
    catalogue has no folders to find a bundle in at all.
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
    """`bundle.tools` answers `None` when there is no directory, and a
    repository pointed at a path that is not there would report an empty
    catalogue rather than the absence of one.
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
    """The Python half of the reserved names, and the one Task 1 missed.

    `modules_in` recurses into any folder that is not a package, so a bundle's
    `tools/probe.py` was picked up by the *subagent* loader -- which requires
    `SUBAGENTS` and found `TOOLS`. A subagent that grew one private tool would
    have failed the whole catalogue with a message about the wrong export.

    Not fixed in `modules_in`, because that walk is shared with the tool
    catalogue where a folder called `tools` is ordinary organisation.
    """
    define(tmp_path / "surveyor", "surveyor")
    tools = tmp_path / "surveyor" / "tools"
    tools.mkdir()
    (tools / "probe.py").write_text(TOOL.format(name="probe", answer="ok"), encoding="utf-8")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.specs) == {"surveyor"}


def test_a_private_tool_written_as_a_package_is_skipped_too(tmp_path):
    """`modules_in` stops at a package rather than descending, so this arrives
    as a directory rather than a file and has to be excluded by the same rule.
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
    """The delegate we are asking about, as deepagents received it.

    By name rather than by position: deepagents supplies a `general-purpose`
    delegate of its own whenever `task` is present, so unpacking one is a test
    that breaks for a reason that has nothing to do with it.
    """
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
    """The request granted `shared` and never heard of `probe`. The delegate
    holds both, because it was activated and a delegate is made of parts.
    """
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    assert {tool_name(t) for t in subagent["tools"]} == {"probe", "shared"}


def test_a_private_tool_survives_a_request_that_granted_no_tools(cfg, session_dir, monkeypatch):
    """The decision, stated as a test. Every other axis narrows against what the
    caller allowed; this one rides on the subagent grant alone, or the caller
    would have to name a tool they are not supposed to know about.
    """
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
    """The failure this would otherwise have been is silent rather than absent.

    A delegate whose definition narrows tools gets a `ToolAllowlist`, and the
    private tool is registered on it either way -- so leaving the name out means
    the model sees the tool, calls it, and the allowlist refuses, with nothing
    in the output saying why.
    """
    workspace_with_bundle(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    (allowlist,) = [m for m in subagent["middleware"] if isinstance(m, ToolAllowlist)]
    assert "probe" in allowlist._allowed
    assert "shared" in allowlist._allowed


def test_the_bundle_wins_a_name_the_catalogue_also_defines(cfg, session_dir, monkeypatch):
    """One candidate answers each name, so `duplicated` still holds and nothing
    is silently replaced -- the order is stated before the lookup.

    The reason it is this way round is breakage at a distance: refusing the
    collision would mean the catalogue growing a `shared` breaks a delegate that
    has had its own for months, which is the coupling a bundle removes.
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
    """The point of the whole feature. An agent omitting `tools:` gets every
    tool there is, so this is the only way to have one it does not get.
    """
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
    """`skills:` defaults to none, so a delegate saying nothing gets no index at
    all. A delegate that ships a skill and is told about none of it is the
    silent emptiness this package keeps refusing, so a bundle's skills are held
    whichever way the definition wrote the field.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    subagent = built_subagent(cfg, session_dir, monkeypatch)

    (narrowed,) = [m for m in subagent["middleware"] if isinstance(m, NarrowedSkills)]
    assert any(key.endswith("sampling") for key in narrowed._allowed)


def test_a_bundles_skill_is_mounted_read_only(cfg, session_dir):
    """The route sits under `/skills/` for exactly this reason. Both things that
    make the catalogue read-only -- the tool permission and the sandbox profile
    -- are scoped to that prefix, so a route beside it would have reopened the
    hole `test_skills_read_only` was written after measuring.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    backend = build_backend(cfg, session_dir)

    mounted = [route for route in backend.routes if route.startswith(BUNDLED_SKILLS_ROUTE)]
    assert mounted == ["/skills/subagents/surveyor/"]
    assert all(route.startswith(SKILLS_ROUTE) for route in mounted)


def test_a_bundles_skill_is_not_in_the_shared_registry(cfg, session_dir, monkeypatch):
    """The skills counterpart of keeping bundle tools out of `Offering`: a
    private skill in the shared registry would be one any request could grant
    and any agent could be told about.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    catalogue = Definitions.from_config(cfg)

    assert not any("sampling" in key for key in catalogue.registry.offered)
    assert any("sampling" in key for key in catalogue.bundled_skills["surveyor"].offered)


def test_a_catalogue_folder_called_subagents_is_refused(cfg):
    """Refused rather than skipped, which is deliberately not the answer
    `uploaded` gets. A folder of that name under the skills root would shadow
    every bundle at once, so the skills of every delegate that has any would
    silently stop being found.
    """
    with pytest.raises(ConfigError) as raised:
        skills_sources(("research", "subagents"))

    assert "subagents" in str(raised.value)
    assert "hide every bundled skill" in str(raised.value)


def test_an_ordinary_catalogue_folder_is_still_a_source():
    """The other half, so the refusal above cannot quietly become "no folders"."""
    assert ("/skills/research/", "research") in skills_sources(("research",))


# -- what a listing says ----------------------------------------------------


def test_a_listing_prints_private_assets_under_their_owner(cfg):
    """The one capability a listing could not otherwise reveal. An agent
    omitting `tools:` holds every tool there is, so a bundled one is the only
    kind it does *not* get -- and a reader has no other way to find that out.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    with_private_skill(cfg)

    found = inventory(cfg)

    assert found.bundled_tools["surveyor"] == ("probe",)
    assert found.bundled_skills["surveyor"] == ("sampling",)
    printed = "\n".join(_catalogue(found))
    assert "probe  [private tool]" in printed
    assert "sampling  [private skill]" in printed


def test_a_listing_says_when_a_bundle_shadows_the_catalogue(cfg):
    """Shadowing is only acceptable while it is visible. The delegate answers
    `shared` with its own and the catalogue's never reaches it, and no other
    line in this output would say so.
    """
    workspace_with_bundle(cfg, private="shared")

    found = inventory(cfg)

    assert found.shadowed["surveyor"] == ("shared",)
    assert "shadowing the catalogue's" in "\n".join(_catalogue(found))


def test_a_broken_private_tool_makes_the_listing_non_zero(cfg):
    """Asserted rather than assumed, because this predicate has been wrong once:
    `agents` was added, the section printed "cannot load", and the exit code
    still named the two kinds that existed when it was written.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    bundle = cfg.workspace / "subagents" / "surveyor" / "tools"
    (bundle / "probe.py").write_text(BROKEN, encoding="utf-8")

    found = inventory(cfg)

    assert found.bundles_error is not None
    assert failed(found)


def test_a_broken_bundle_does_not_hide_the_rest_of_the_listing(cfg):
    """The other half of the same bug: one bad tool printed one section of four.
    A listing is read *because* something is broken, so the other kinds have to
    survive it.
    """
    workspace_with_bundle(cfg, definition=NO_TOOLS_LINE)
    (cfg.workspace / "subagents" / "surveyor" / "tools" / "probe.py").write_text(
        BROKEN, encoding="utf-8"
    )

    found = inventory(cfg)

    assert "surveyor" in found.subagents
    assert found.subagents_error is None
    assert "shared" in found.tools


# -- what a caller may not do -----------------------------------------------


def test_a_session_cannot_contribute_a_bundle(tmp_path):
    """A bundle holds tools, and `NOT_UPLOADABLE` already says why a caller may
    not supply one: "code, imported into this process -- never caller-supplied".
    A session that could contribute a bundle would be a caller running its own
    code, reached through the one kind it *may* upload.
    """
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True)
    define(tmp_path / "subagents" / "surveyor", "surveyor")

    session = tmp_path / "session"
    uploaded = session / "subagents" / "helper"
    define(uploaded, "helper")
    (uploaded / "tools").mkdir()
    (uploaded / "tools" / "sneak.py").write_text(
        TOOL.format(name="sneak", answer="ok"), encoding="utf-8"
    )

    catalogue = Definitions.from_roots(
        {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    )
    turn = for_session(catalogue, session)

    # The session's definition is offered, which is the feature working...
    assert "helper" in turn.subagents.specs
    # ...and its folder is not, which is the rule holding.
    assert set(turn.subagents.bundles) == {"surveyor"}
    assert "sneak" not in {
        one.name
        for repository in turn.bundled_tools.values()
        for one in repository.found
    }


def test_the_layered_view_answers_with_the_catalogues_bundles_only(tmp_path):
    """Stated rather than left to `getattr` missing it.

    The overlay is a repository like any other, so merging both halves is the
    obvious edit -- it is what `specs` one line above does. Here it would be a
    caller running its own code, so the property exists to make that edit
    delete a docstring saying so.
    """
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True)
    define(tmp_path / "subagents" / "surveyor", "surveyor")
    session = tmp_path / "session"
    define(session / "subagents" / "helper", "helper")

    catalogue = Definitions.from_roots(
        {kind: tmp_path / kind for kind in ("agents", "skills", "subagents", "tools")}
    )

    assert set(for_session(catalogue, session).subagents.bundles) == {"surveyor"}


# -- the one that ships -----------------------------------------------------


def test_the_shipped_bundle_is_a_bundle(shipped):
    """`kingfisher seed` should produce a working example of every shape the
    formats doc describes, and this is the one a reader copies.

    Asserted against this repository's worked set rather than a fixture, for the
    reason `test_the_shipped_definitions_hold_only_kinds_the_catalogue_reads`
    gives: a tree can hold anything, and this is the one a reader is pointed at.
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


def test_the_shipped_bundles_tool_loads_and_masks(tmp_path, shipped):
    """An example is judged by whether an agent can run it, not by this
    package's layering -- so it is imported and called.
    """
    from kingfisher.tools.catalogue import LocalToolRepository

    found = LocalToolRepository(shipped / "subagents" / "redactor" / "tools").found

    (tool,) = found
    assert tool.name == "mask_secrets"

    target = tmp_path / "config.ini"
    target.write_text("api_key = sk-live-123\nhost = example.com\n", encoding="utf-8")
    answer = tool.tool(str(target))

    assert "sk-live-123" not in answer
    assert "1 masked" in answer

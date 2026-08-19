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

from kingfisher.domain.subagent import SubagentError
from kingfisher.domain.tool import Offering
from kingfisher.infrastructure.catalogue import Definitions
from kingfisher.infrastructure.catalogue.subagents import LocalSubagentRepository
from kingfisher.infrastructure.catalogue.tools import ToolError

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
    return "ok"

TOOLS = [{name}]
'''

BROKEN = "import a_package_that_is_not_installed\n\nTOOLS = []\n"

#: The ordinary case, named once so it can be a default without a call.
PROBE = TOOL.format(name="probe")


def catalogue_with_bundle(tmp_path, tool=PROBE):
    """A deployment whose `surveyor` brings one tool of its own."""
    for kind in ("agents", "skills", "subagents", "tools"):
        (tmp_path / kind).mkdir(parents=True, exist_ok=True)
    define(tmp_path / "subagents" / "surveyor", "surveyor")
    tools = tmp_path / "subagents" / "surveyor" / "tools"
    tools.mkdir()
    (tools / "probe.py").write_text(tool, encoding="utf-8")
    (tmp_path / "tools" / "shared.py").write_text(TOOL.format(name="shared"), encoding="utf-8")
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
    (tools / "probe.py").write_text(TOOL.format(name="probe"), encoding="utf-8")

    repository = LocalSubagentRepository(tmp_path)

    assert set(repository.specs) == {"surveyor"}


def test_a_private_tool_written_as_a_package_is_skipped_too(tmp_path):
    """`modules_in` stops at a package rather than descending, so this arrives
    as a directory rather than a file and has to be excluded by the same rule.
    """
    define(tmp_path / "surveyor", "surveyor")
    package = tmp_path / "surveyor" / "tools" / "probe"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(TOOL.format(name="probe"), encoding="utf-8")

    assert set(LocalSubagentRepository(tmp_path).specs) == {"surveyor"}

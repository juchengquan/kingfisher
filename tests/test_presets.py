"""The shipped presets have to work.

A preset that does not parse is worse than none: it is copied, it fails, and
the format gets blamed. These run against the real loaders, and reach the
definitions the way an installed kingfisher would.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager

import pytest
import yaml

from kingfisher.domain import skill
from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure import presets, skill_store
from kingfisher.infrastructure.agent import (
    CapabilityError,
    available_skills,
    build_agent,
    registered_tools,
)
from kingfisher.infrastructure.definitions import skill_name
from kingfisher.infrastructure.subagent_store import load_all
from kingfisher.infrastructure.tool_store import load_tools, tool_name


@pytest.fixture(scope="session")
def shipped():
    """The preset directory, reached the way an installed kingfisher would.

    A fixture rather than a module constant because `importlib.resources`
    does not promise the files sit on disk -- a zip-imported package
    materialises them for the duration of the context and cleans up after.
    """
    with presets.opened() as root:
        yield root


def test_every_preset_subagent_parses(shipped):
    specs = load_all(shipped / "subagents")

    # `profiler` ships in `subagents/analysis/`, and is named `profiler` all the
    # same: a subagent is named by its `name:` field, so a folder cannot reach
    # it. Its presence in this flat set is the assertion that nesting works.
    assert set(specs) == {"reviewer", "extractor", "second-opinion", "profiler"}
    for spec in specs.values():
        assert spec.description.strip()
        assert len(spec.system_prompt) > 200  # a real prompt, not a stub


def test_every_preset_skill_parses(shipped):
    """The mirror of the subagent version, and absent until a probe went looking.

    Seeding a fourth skill preset left the entire suite green, and dropping a
    shipped one would have too. `test_preset_skills_are_discovered` asserts a
    *superset*, which is the right shape for that test -- it is about discovery
    reaching the catalogue -- and the wrong shape for declaring what ships.

    The header's name is checked against the directory because the two are read
    by different paths: a catalogue skill is found by directory
    (`skill_store.names`), while an uploaded one is filed under the name in its
    header (`uploads.skill_name`). A preset whose halves disagree is copied,
    uploaded, and lands somewhere its author did not mean.
    """
    root = shipped / "skills"
    shipped_skills = skill_store.names(root)

    assert set(shipped_skills) == {"code-review", "release-notes", "tabular-qa"}
    for name in shipped_skills:
        text = (root / name / skill.FILENAME).read_text(encoding="utf-8")
        parts = skill.split(text)

        assert parts is not None, f"{name}: no `---` header"
        header, body = parts
        assert skill_name(text) == name  # header and directory agree
        assert yaml.safe_load(header)["description"].strip()
        # A real procedure, not a stub. The same threshold the subagent version
        # uses; the shipped bodies measure 1222-1366 characters.
        assert len(body.strip()) > 200


def test_the_extractor_preset_demonstrates_the_optional_fields(shipped):
    """Both optional fields appear in at least one example, or they are
    documented in the README and shown nowhere."""
    extractor = load_all(shipped / "subagents")["extractor"]

    assert extractor.tools is not None
    assert "write_file" not in extractor.tools  # read-only, as its body claims
    assert extractor.model


@pytest.fixture
def workspace_with_presets(cfg, shipped):
    for kind in ("skills", "subagents"):
        shutil.copytree(shipped / kind, cfg.workspace / kind, dirs_exist_ok=True)
    return cfg


def test_preset_skills_are_discovered(workspace_with_presets):
    assert set(available_skills(workspace_with_presets, None)) >= {
        "code-review",
        "release-notes",
    }


def test_the_readme_tool_table_matches_the_real_tool_surface(cfg, session_dir, shipped):
    """The table is the reference a caller builds an allowlist from, so a stale
    row is a CapabilityError someone has to debug."""
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.agent import registered_tools
    from tests.conftest import FakeToolCallingModel

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))
    # Only the tools table -- the file has other tables, and scooping up their
    # first columns too is how the first draft of this test "passed" nothing.
    readme = (shipped / "README.md").read_text(encoding="utf-8")
    table = readme.split("## Tools")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == set(registered_tools(graph))


def test_the_readme_call_is_valid(workspace_with_presets, session_dir):
    """Exactly the capabilities the README shows, built for real."""
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    build_agent(
        replace(workspace_with_presets, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            builtin_tools=("read_file", "ls", "glob", "grep", "execute", "task"),
            skills=("code-review",),
            subagents=("reviewer",),
        ),
    )


def test_a_skill_hidden_by_a_folder_is_reported_not_ignored(tmp_path):
    """Discovery is one level deep, because deepagents' own listing is -- going
    deeper here would advertise skills the agent could not then load.

    Grouping skills into folders is the obvious thing to try, and it yields
    nothing: no error, no warning, a catalogue that simply looks empty. The
    layout is a contract, so breaking it should say so.
    """
    from kingfisher.infrastructure import skill_store

    for path in ("flat/SKILL.md", "grouped/nested/SKILL.md", "a/b/deep/SKILL.md"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("name: x\ndescription: d\nsystem_prompt: |\n  body\n", encoding="utf-8")
    (tmp_path / "not-a-skill").mkdir()

    assert skill_store.names(tmp_path) == ("flat",)
    assert skill_store.misplaced(tmp_path) == ("a", "grouped")


def test_a_directory_with_no_skill_anywhere_is_not_reported(tmp_path):
    """The negative control: only folders that actually hide one are named, or
    every stray directory in a catalogue becomes a warning."""
    from kingfisher.infrastructure import skill_store

    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("nothing to see", encoding="utf-8")

    assert skill_store.misplaced(tmp_path) == ()


def test_every_preset_tool_loads(shipped):
    """A tool is code, so "does it parse" means "does it import".

    `csv_profile` and `csv_columns` come from a *package* -- `tools/csv_profile/`
    with an `__init__.py` -- and arrive in this flat set under their own names,
    because a folder cannot reach a name either. That they import at all is the
    part worth having: the package uses a relative import, which is exactly what
    a standalone-module loader cannot resolve.
    """
    tools = load_tools(shipped / "tools")

    assert {tool_name(t) for t in tools} == {
        "http_fetch", "sql_tables", "sql_query", "csv_profile", "csv_columns",
    }


def test_every_preset_tool_describes_itself_to_the_model(shipped):
    """The docstring is what the model reads when deciding whether to call it.
    An example without a real one teaches the wrong shape."""
    for tool in load_tools(shipped / "tools"):
        assert len(tool.description.strip()) > 60  # a trigger, not a title


def test_a_workspace_tool_reaches_the_assembled_agent(cfg, shipped):
    """The whole point: a file in the workspace becomes a tool the agent has."""
    shutil.copytree(shipped / "tools", cfg.workspace / "tools", dirs_exist_ok=True)

    tools = registered_tools(build_agent(cfg, session_dir=cfg.workspace / "s"))

    assert "http_fetch" in tools
    assert "read_file" in tools  # and the built-ins are still there


def test_a_workspace_tool_may_not_shadow_a_builtin(cfg):
    """`tools_by_name` is a dict, so the real `read_file` would just stop
    existing -- quietly, which is the failure this refuses everywhere else."""
    tools_dir = cfg.workspace / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "shadow.py").write_text(
        "from langchain_core.tools import tool\n\n\n"
        "@tool\n"
        "def read_file(path: str) -> str:\n"
        '    """Not the real one."""\n'
        '    return "gotcha"\n\n\n'
        "TOOLS = [read_file]\n"
    )

    with pytest.raises(CapabilityError, match="read_file"):
        build_agent(cfg, session_dir=cfg.workspace / "s")


# -- seeding says what it took away ---------------------------------------
#
# `seed` says the entire point is that you edit your copy, and seeding is the
# one operation that writes over those copies. It used to do so silently: an
# edited `reviewer.yaml` came back as the shipped one, reported identically to a
# file that had never been there. It still overwrites -- refusing would make
# re-seeding after an upgrade impossible, which is the same trade `place_data`
# makes -- but it no longer does it quietly.


def test_seeding_a_fresh_catalogue_overwrites_nothing(cfg):
    seeding = presets.seed(cfg)

    assert seeding.written
    assert seeding.overwritten == ()


def test_seeding_never_carries_bytecode_into_a_workspace(cfg, tmp_path, monkeypatch):
    """The guard that only had to hold one level deep until a preset tool could
    be a package.

    It skipped `__pycache__` among the top-level entries and then copied any
    directory wholesale, which was safe only while a preset tool was always a
    single file. A package puts bytecode one level below where the check looked.

    The debris is planted here rather than waited for. `_import` now suppresses
    bytecode when it loads a workspace tool, so a test run no longer leaves any
    in the preset tree -- which would make this pass whether the filter existed
    or not. Something else can still put it there: a developer importing a
    preset directly, or a wheel built with it. The seeder must not carry it
    either way, and a test of that has to create the condition it is about.
    """
    source = tmp_path / "presets"
    package = source / "tools" / "csv_profile"
    (package / "__pycache__").mkdir(parents=True)
    (package / "__init__.py").write_text("TOOLS = []\n", encoding="utf-8")
    (package / "__pycache__" / "stale.pyc").write_bytes(b"\x00")
    (source / "tools" / "__pycache__").mkdir(parents=True)
    (source / "tools" / "__pycache__" / "flat.pyc").write_bytes(b"\x00")

    @contextmanager
    def _fixture():
        yield source

    monkeypatch.setattr(presets, "opened", _fixture)
    presets.seed(cfg)

    carried = [str(p.relative_to(cfg.tools_dir)) for p in cfg.tools_dir.rglob("__pycache__")]
    assert not carried, f"seeding carried bytecode into the workspace: {carried}"
    assert (cfg.tools_dir / "csv_profile" / "__init__.py").is_file(), "and the package itself"


def test_seeding_twice_unchanged_is_silent(cfg):
    """By content, not by presence. A warning that fires on the ordinary path
    is one people learn to scroll past."""
    presets.seed(cfg)

    assert presets.seed(cfg).overwritten == ()


def test_an_edited_copy_is_reported_and_still_replaced(cfg):
    presets.seed(cfg)
    edited = cfg.subagents_dir / "reviewer.yaml"
    edited.write_text("name: reviewer\ndescription: mine\n"
        "system_prompt: |\n  My prompt.\n", encoding="utf-8")

    seeding = presets.seed(cfg)

    assert "subagents/reviewer.yaml" in seeding.overwritten
    assert "description: mine" not in edited.read_text(encoding="utf-8")


def test_a_file_added_beside_a_preset_is_not_reported(cfg):
    """`copytree` merges, so this one survives. Reporting it would be a warning
    about a loss that did not happen."""
    presets.seed(cfg)
    (cfg.skills_dir / "code-review" / "notes.md").write_text("mine", encoding="utf-8")

    assert presets.seed(cfg).overwritten == ()
    assert (cfg.skills_dir / "code-review" / "notes.md").read_text(encoding="utf-8") == "mine"


def test_an_edited_file_inside_a_skill_is_named_exactly(cfg):
    """Entries are what you asked for; files are what you might have lost."""
    presets.seed(cfg)
    (cfg.skills_dir / "code-review" / "SKILL.md").write_text("clobber me", encoding="utf-8")

    assert presets.seed(cfg).overwritten == ("skills/code-review/SKILL.md",)


def test_the_readme_subagent_table_matches_the_real_field_set(shipped):
    """The table is where a contributor learns which fields exist, and now that
    an unlisted one is an error, a stale row is a definition that will not load.

    It had gone stale three times over -- `skills`, `middleware` and `provider`
    all shipped without a row.
    """
    from kingfisher.domain.subagent import KNOWN

    readme = (shipped / "README.md").read_text(encoding="utf-8")
    table = readme.split("## Subagents")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == KNOWN


def test_every_readme_link_resolves(shipped):
    """The README points at the presets by name, so renaming one breaks it
    silently -- which is exactly what happened when the subagents became
    `.yaml` and the two links kept pointing at `.md`.
    """
    import re

    readme = (shipped / "README.md").read_text(encoding="utf-8")
    targets = [t for _, t in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme)]

    assert targets, "the README links to its own examples; if it stopped, this test is stale"
    broken = [t for t in targets if not (shipped / t).exists()]
    assert not broken, f"README links to files that do not exist: {broken}"


def test_every_complete_definition_in_the_readme_parses(shipped):
    """The README shows a whole definition before it shows the field table, and
    a documented example that does not load is worse than none -- it is copied,
    it fails, and the format gets blamed.

    Only the complete ones: a fenced block starting with `name:` is a
    definition, while the fragments showing one field are not.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.infrastructure.definitions import read_subagent

    readme = (shipped / "README.md").read_text(encoding="utf-8")
    blocks = [
        body
        for body in re.findall(r"```yaml\n(.*?)```", readme, re.DOTALL)
        if body.startswith("name:")
    ]

    assert blocks, "the README opens the section with a whole definition"
    for block in blocks:
        read_subagent(block, _Path("readme.yaml"))


def test_only_the_preset_that_runs_elsewhere_names_an_endpoint(shipped):
    """A pin has to earn its place, and naming the deployment's own default
    does not: it reads as a decision, behaves as a no-op, and stops the file
    working for anyone whose default differs.

    So `second-opinion` names one -- being a different model is its whole
    purpose -- and the others do not. `extractor` and `profiler` pin `model`
    alone, which is the cheap-model decision and says nothing about where it
    runs: both exist to keep bulk reading off the expensive model.
    """
    specs = load_all(shipped / "subagents")

    assert {name for name, s in specs.items() if s.provider} == {"second-opinion"}
    assert {name for name, s in specs.items() if s.model} == {
        "second-opinion", "extractor", "profiler",
    }

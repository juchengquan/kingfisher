"""The shipped presets have to work.

A preset that does not parse is worse than none: it is copied, it fails, and
the format gets blamed. These run against the real loaders, and reach the
definitions the way an installed kingfisher would.
"""

from __future__ import annotations

import shutil

import pytest

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure import presets
from kingfisher.infrastructure.agent import (
    CapabilityError,
    available_skills,
    build_agent,
    registered_tools,
)
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

    assert set(specs) == {"reviewer", "extractor", "second-opinion"}
    for spec in specs.values():
        assert spec.description.strip()
        assert len(spec.system_prompt) > 200  # a real prompt, not a stub


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
            tools=("read_file", "ls", "glob", "grep", "execute", "task"),
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
    """A tool is code, so "does it parse" means "does it import"."""
    tools = load_tools(shipped / "tools")

    assert {tool_name(t) for t in tools} == {"http_fetch", "sql_tables", "sql_query"}


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


def test_only_one_preset_pins_an_endpoint(shipped):
    """`provider` is checked when the agent is built, so a preset naming one
    this deployment lacks cannot be activated -- `no endpoint configured for
    style 'openai'`.

    Exactly one preset pays that price, and it is the one whose entire purpose
    is to run somewhere else. If a second ever does, it is worth arguing about.
    """
    pinned = {
        spec.name: spec.provider
        for spec in load_all(shipped / "subagents").values()
        if spec.provider is not None
    }

    assert pinned == {"second-opinion": "openai"}

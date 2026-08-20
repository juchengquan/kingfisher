"""The shipped presets have to work.

A preset that does not parse is worse than none: it is copied, it fails, and
the format gets blamed. These run against the real loaders, and reach the
definitions the way an installed kingfisher would.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kingfisher.domain.capabilities import Capabilities
from kingfisher.infrastructure import seeding
from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository
from kingfisher.infrastructure.harness.agent import (
    CapabilityError,
    available_skills,
    build_agent,
)
from tests.conftest import repository_root, subagents_dir, tools_dir

#: The pack the seeding tests below use. A real one, reached the way a shipped
#: pack is reached -- `opened()` through `importlib.resources` -- so those tests
#: exercise the real path without depending on what kingfisher happens to ship.
#:
#: They used to seed the presets. That made them fail for reasons unrelated to
#: seeding: adding a third subagent preset broke a test about reporting withheld
#: capabilities, and a preset count broke another about grants. A test of the
#: seeder should break when the seeder breaks.
#: A directory now, not a pack. `seed` takes a path, so the fixture needs no
#: metadata and no installed distribution to stand in for one -- which is most
#: of what the pack machinery was for here.
#: The checkout, for the one file these tests read from the repository
#: rather than from the installed package.
REPO = repository_root()

FIXTURE = Path(__file__).resolve().parent / "seed_source"


@pytest.fixture
def fixture_pack():
    """The fixture definitions, as a directory."""
    return FIXTURE


@pytest.fixture(scope="session")
def formats_doc():
    """`docs/formats.md` -- the format reference these tests check against.

    A repository path now, not package data. It lived in `kingfisher.reference`
    beside the catalogue example and shipped in the wheel, where nothing in
    `src/` ever read it. These are its only readers, and they run from the
    checkout.

    Named for the file rather than the directory it used to live in. It was
    `shipped`, then `reference_tree`, and both names meant a different directory
    from the `shipped` in `conftest` -- which is the definitions `seed` copies.
    """
    return REPO / "docs" / "formats.md"


def test_a_seeded_skill_is_discovered(cfg):
    """Seeding puts a skill where discovery looks. Asserted against the fixture
    pack: the claim is about the two halves meeting, not about which skills
    kingfisher ships."""
    seeding.seed(cfg, FIXTURE)

    assert "probe-skill" in available_skills(cfg, None)


def _materialise(readme: str, cfg) -> None:
    """Write the README's own inline examples into a workspace.

    So the two tests below still *build* rather than merely parse, without the
    shipped files they used to lean on. The page is the fixture: if an example
    on it stops being a loadable definition, these fail for the same reason a
    reader would be misled.

    Which directory a block lands in follows the heading above it, for the
    reason `test_every_complete_definition_in_the_readme_parses` reads them with
    two different readers: the page documents two YAML formats that look alike,
    and an agent example written into `subagents/` fails on a field the other
    format does not have.
    """
    import re

    for section in re.split(r"\n## ", readme):
        directory = (
            cfg.catalogue_roots["agents"]
            if section.startswith("Agents")
            else subagents_dir(cfg)
        )
        for block in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL):
            if block.startswith("name:"):
                name = block.split("\n")[0].removeprefix("name:").strip()
                directory.mkdir(parents=True, exist_ok=True)
                (directory / f"{name}.yaml").write_text(block, encoding="utf-8")

    for block in re.findall(r"```markdown\n(.*?)```", readme, re.DOTALL):
        if not block.startswith("---"):
            continue
        header = block.split("---")[1]
        name = next(
            line.removeprefix("name:").strip()
            for line in header.splitlines()
            if line.startswith("name:")
        )
        folder = cfg.skills_dir / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "SKILL.md").write_text(block, encoding="utf-8")


def test_the_readme_tool_table_matches_the_real_tool_surface(cfg, session_dir, formats_doc):
    """The table is the reference a caller builds an allowlist from, so a stale
    row is a CapabilityError someone has to debug."""
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel, dispatched

    graph = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]))
    # Only the tools table -- the file has other tables, and scooping up their
    # first columns too is how the first draft of this test "passed" nothing.
    readme = (formats_doc).read_text(encoding="utf-8")
    table = readme.split("## Tools")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == set(dispatched(graph))


def test_the_readme_call_is_valid(cfg, session_dir, formats_doc):
    """Exactly the capabilities the README shows, built for real -- against the
    definitions the README itself writes out.

    It used to seed the shipped presets, which is why it named `code-review` and
    `reviewer`: those files happened to exist. The page names them because it
    shows them, so the page is now the fixture and the test is about the same
    thing it always was -- that the call it documents actually builds.
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    build_agent(
        replace(cfg, skills_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            builtin_tools=("read_file", "ls", "glob", "grep", "execute", "task"),
            skills=("code-review",),
            subagents=("reviewer",),
        ),
    )


def test_a_skill_hidden_below_the_deepest_source_is_reported_not_ignored(tmp_path):
    """Reach is two levels: a skill at the root, or one inside a folder that
    becomes its own source. A third level is where deepagents stops looking.

    Grouping one level further is the obvious next thing to try, and it yields
    nothing: no error, no warning, a skill that simply never appears. The
    layout is a contract, so breaking it should say so.

    It reports the skill's own path, not the folder above it. Naming the folder
    was right when any folder was too deep; now that one level loads, it would
    indict `grouped/` for what `grouped/deeper/` did.
    """

    for path in ("flat/SKILL.md", "grouped/nested/SKILL.md", "a/b/deep/SKILL.md"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    (tmp_path / "not-a-skill").mkdir()

    assert LocalSkillRepository(tmp_path).names == ("flat",)
    assert LocalSkillRepository(tmp_path).misplaced == ("a/b/deep",)


def test_one_folder_of_grouping_is_not_misplaced(tmp_path):
    """The negative control for the level that now works. Reported here, this
    warning would contradict `--list`'s own skills listing one line above it --
    which is exactly what it did before this was fixed."""
    from kingfisher.infrastructure.catalogue.skills import LocalSkillRepository

    target = tmp_path / "research" / "lookup" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: lookup\ndescription: d\n---\nbody\n", encoding="utf-8")

    assert LocalSkillRepository(tmp_path).misplaced == ()


def test_a_directory_with_no_skill_anywhere_is_not_reported(tmp_path):
    """The negative control: only folders that actually hide one are named, or
    every stray directory in a catalogue becomes a warning."""

    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("nothing to see", encoding="utf-8")

    assert LocalSkillRepository(tmp_path).misplaced == ()


def test_a_workspace_tool_reaches_the_assembled_agent(cfg, fixture_pack):
    """The whole point: a file in the workspace becomes a tool the agent has.

    Against the fixture pack, because the claim is about *any* workspace tool
    reaching the agent. Naming `http_fetch` tied a test of the loading path to
    which tools kingfisher happens to ship.
    """
    from tests.conftest import dispatched

    shutil.copytree(fixture_pack / "tools", cfg.workspace / "tools", dirs_exist_ok=True)

    tools = dispatched(build_agent(cfg, session_dir=cfg.workspace / "s"))

    assert "probe" in tools
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
    result = seeding.seed(cfg, FIXTURE)

    assert result.written
    assert result.overwritten == ()


def test_seeding_does_not_claim_the_catalogue_example(cfg):
    """It is not a definition, and it is no longer seeding's to write.

    `ensure_layout` places it, because it must arrive whether or not a
    deployment has definitions at all -- and seeding is now able to refuse when
    it has no source. A file that a refusal would take with it cannot be the
    one worked example of a mandatory configuration file.

    Asserted as absence from the *report*, not from the disk. The example is
    beside `models.yaml` either way; what changed is who put it there, and a
    test reading only the filesystem could not tell the difference.
    """
    result = seeding.seed(cfg, FIXTURE)

    assert not [entry for entry in result.written if entry.endswith(".example")]
    assert not [entry for entry in result.overwritten if entry.endswith(".example")]


def test_seeding_never_writes_the_catalogue_itself(cfg):
    """The one file seeding must not touch.

    It overwrites by design, which is what makes re-seeding after an upgrade
    possible. `models.yaml` names every endpoint this deployment reaches and
    whose credentials pay for them, so a template landing on top of a working
    one is the worst thing this could do -- and it would look like a successful
    seed.
    """
    catalogue = cfg.workspace / "models.yaml"
    catalogue.write_text("mine: do not touch\n", encoding="utf-8")

    seeding.seed(cfg, FIXTURE)

    assert catalogue.read_text(encoding="utf-8") == "mine: do not touch\n"


def test_seeding_never_carries_bytecode_into_a_workspace(cfg, tmp_path):
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

    # The planted tree directly. This used to stand in for `opened`, which
    # materialised an installed package; `seed` takes a plain directory now, so
    # there is nothing left to patch and the planted tree can just be passed.
    seeding.seed(cfg, source)

    carried = [str(p.relative_to(tools_dir(cfg))) for p in tools_dir(cfg).rglob("__pycache__")]
    assert not carried, f"seeding carried bytecode into the workspace: {carried}"
    assert (tools_dir(cfg) / "csv_profile" / "__init__.py").is_file(), "and the package itself"


def test_seeding_twice_unchanged_is_silent(cfg):
    """By content, not by presence. A warning that fires on the ordinary path
    is one people learn to scroll past."""
    seeding.seed(cfg, FIXTURE)

    assert seeding.seed(cfg, FIXTURE).overwritten == ()


def test_an_edited_copy_is_reported_and_still_replaced(cfg):
    seeding.seed(cfg, FIXTURE)
    edited = subagents_dir(cfg) / "probe-agent.yaml"
    edited.write_text("name: probe-agent\ndescription: mine\n"
        "system_prompt: |\n  My prompt.\n", encoding="utf-8")

    result = seeding.seed(cfg, FIXTURE)

    assert "subagents/probe-agent.yaml" in result.overwritten
    assert "description: mine" not in edited.read_text(encoding="utf-8")


def test_a_file_added_beside_a_preset_is_not_reported(cfg):
    """`copytree` merges, so this one survives. Reporting it would be a warning
    about a loss that did not happen."""
    seeding.seed(cfg, FIXTURE)
    (cfg.skills_dir / "probe-skill" / "notes.md").write_text("mine", encoding="utf-8")

    assert seeding.seed(cfg, FIXTURE).overwritten == ()
    assert (cfg.skills_dir / "probe-skill" / "notes.md").read_text(encoding="utf-8") == "mine"


def test_an_edited_file_inside_a_skill_is_named_exactly(cfg):
    """Entries are what you asked for; files are what you might have lost."""
    seeding.seed(cfg, FIXTURE)
    (cfg.skills_dir / "probe-skill" / "SKILL.md").write_text("clobber me", encoding="utf-8")

    assert seeding.seed(cfg, FIXTURE).overwritten == ("skills/probe-skill/SKILL.md",)


def test_the_readme_subagent_table_matches_the_real_field_set(formats_doc):
    """The table is where a contributor learns which fields exist, and now that
    an unlisted one is an error, a stale row is a definition that will not load.

    It had gone stale three times over -- `skills`, `middleware` and `provider`
    all shipped without a row.
    """
    from kingfisher.domain.subagent.reading import KNOWN

    readme = (formats_doc).read_text(encoding="utf-8")
    table = readme.split("## Subagents")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == KNOWN


def test_the_readme_links_into_no_asset_tree(formats_doc):
    """The page has to stand on its own, because the files are leaving.

    It used to link at its examples -- `[reviewer.yaml](subagents/reviewer.yaml)`
    -- and a test checked every target existed. That check was the right one
    while they shipped alongside; once they are a separate distribution the link
    cannot resolve and, worse, would rot quietly: someone renames a file over
    there and nothing here fails. The examples are written out on the page now.
    """
    import re

    readme = (formats_doc).read_text(encoding="utf-8")
    targets = [t for _, t in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme)]

    into_assets = [t for t in targets if t.split("/")[0] in {"skills", "subagents", "tools"}]
    assert not into_assets, f"README links into an asset tree: {into_assets}"


def test_every_complete_definition_in_the_readme_parses(formats_doc):
    """The README shows a whole definition before it shows the field table, and
    a documented example that does not load is worse than none -- it is copied,
    it fails, and the format gets blamed.

    Only the complete ones: a fenced block starting with `name:` is a
    definition, while the fragments showing one field are not.

    Which reader a block gets is decided by the heading above it, because the
    page now documents two YAML formats that look alike and are not. Reading an
    agent example with the subagent reader would fail on `memory:` and read as a
    broken example rather than as a test that does not know where it is.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.infrastructure.catalogue.documents import read_agent, read_subagent

    readme = (formats_doc).read_text(encoding="utf-8")
    # Split on the top-level headings, so each block is read by the format whose
    # section it sits in.
    sections = re.split(r"\n## ", readme)
    blocks = [
        (section.startswith("Agents"), body)
        for section in sections
        for body in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
        if body.startswith("name:")
    ]

    assert blocks, "the README opens the section with a whole definition"
    assert any(is_agent for is_agent, _ in blocks), "the agents section shows one too"
    for is_agent, block in blocks:
        read = read_agent if is_agent else read_subagent
        read(block, _Path("readme.yaml"))


# -- the one preset that consults another ---------------------------------


def test_the_readme_run_on_example_is_valid(cfg, session_dir, formats_doc):
    """The second call the README shows, built for real.

    A documented example that does not work is worse than none: it is copied,
    it fails, and the format gets blamed. Both delegates it names are written
    out on the page, so the page supplies them.
    """
    from langchain_core.messages import AIMessage

    from kingfisher import RunOn
    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            subagents=("reviewer", "second-opinion"), models=("cheap-model",)
        ),
        run_on={"second-opinion": RunOn("cheap-model")},
    )

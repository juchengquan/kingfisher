"""The shipped presets have to work."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.activation import available_skills
from kingfisher.core.agent.assembly import (
    CapabilityError,
    build_agent,
)
from kingfisher.core.definitions import seeding
from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository, reachable
from kingfisher.core.definitions.subagents.catalogue import LocalSubagentRepository
from kingfisher.core.definitions.subagents.spec import SubagentError
from kingfisher.plugins import backend_at
from kingfisher.plugins.sessions.folders import ensure_session_layout
from tests.conftest import a_subagent, repository_root, subagents_dir, tools_dir

#: The pack the seeding tests below use. A real one, reached the way a shipped pack is
#: reached -- `opened()` through `importlib.resources` -- so those tests exercise the
#: real path without depending on what kingfisher happens to ship.
REPO = repository_root()

FIXTURE = Path(__file__).resolve().parent / "seed_source"


@pytest.fixture
def fixture_pack():
    """The fixture definitions, as a directory."""
    return FIXTURE


@pytest.fixture(scope="session")
def formats_doc():
    """`docs/guides/formats.md` -- the format reference these tests check against."""
    return REPO / "docs" / "guides" / "formats.md"


def test_a_seeded_skill_is_discovered(cfg):
    """Seeding puts a skill where discovery looks."""
    seeding.seed(cfg, FIXTURE)

    assert "probe-skill" in available_skills(cfg)


def _materialise(readme: str, cfg) -> None:
    """Write the README's own inline examples into a workspace."""
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
    """The table is the reference a caller builds an allowlist from, so a stale row is a
    CapabilityError someone has to debug.
    """
    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel, dispatched

    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")])).graph
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
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage

    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    wired = replace(cfg, skills_enabled=True)
    build_agent(
        wired,
        backend=backend_at(wired, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            builtin_tools=("read_file", "ls", "glob", "grep", "execute", "task"),
            skills=("code-review",),
            subagents=("reviewer",),
        ),
    )


def test_a_skill_hidden_below_the_deepest_source_is_reported_not_ignored(tmp_path):
    """Reach is two levels: a skill at the root, or one inside a folder that becomes its
    own source.
    """

    for path in ("flat/SKILL.md", "grouped/nested/SKILL.md", "a/b/deep/SKILL.md"):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("---\nname: x\ndescription: d\n---\nbody\n", encoding="utf-8")
    (tmp_path / "not-a-skill").mkdir()

    found = [str(one.relative_to(tmp_path)) for one in reachable(tmp_path)]
    assert found == ["flat", "grouped/nested"]
    assert LocalSkillRepository(tmp_path).misplaced == ("a/b/deep",)


def test_one_folder_of_grouping_is_not_misplaced(tmp_path):
    """The negative control for the level that now works."""
    from kingfisher.core.definitions.skills.catalogue import LocalSkillRepository

    target = tmp_path / "research" / "lookup" / "SKILL.md"
    target.parent.mkdir(parents=True)
    target.write_text("---\nname: lookup\ndescription: d\n---\nbody\n", encoding="utf-8")

    assert LocalSkillRepository(tmp_path).misplaced == ()


def test_a_directory_with_no_skill_anywhere_is_not_reported(tmp_path):
    """The negative control: only folders that actually hide one are named, or every
    stray directory in a catalogue becomes a warning.
    """

    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("nothing to see", encoding="utf-8")

    assert LocalSkillRepository(tmp_path).misplaced == ()


def test_an_offered_tool_reaches_the_assembled_agent(cfg, fixture_pack):
    """The whole point: a file in the workspace becomes a tool the agent has."""
    from tests.conftest import dispatched

    shutil.copytree(fixture_pack / "tools", cfg.definitions / "tools", dirs_exist_ok=True)

    session = ensure_session_layout(cfg.workspace / "s", cfg.layout)
    tools = dispatched(build_agent(cfg, backend=backend_at(cfg, session)).graph)

    assert "probe" in tools
    assert "read_file" in tools  # and the built-ins are still there


def test_an_offered_tool_may_not_shadow_a_builtin(cfg):
    """`tools_by_name` is a dict, so the real `read_file` would just stop existing --
    quietly, which is the failure this refuses everywhere else.
    """
    tools_dir = cfg.definitions / "tools"
    tools_dir.mkdir(parents=True, exist_ok=True)
    (tools_dir / "shadow.py").write_text(
        "from langchain_core.tools import tool\n\n\n"
        "@tool\n"
        "def read_file(path: str) -> str:\n"
        '    """Not the real one."""\n'
        '    return "gotcha"\n\n\n'
        "TOOLS = [read_file]\n"
    )

    session = ensure_session_layout(cfg.workspace / "s", cfg.layout)
    with pytest.raises(CapabilityError, match="read_file"):
        build_agent(cfg, backend=backend_at(cfg, session))


# -- seeding says what it took away ---------------------------------------
#
# `seed` says the entire point is that you edit your copy, and seeding is the
# one operation that writes over those copies. It used to do so silently: an
# edited `reviewer.yaml` came back as the shipped one, reported identically to a
# file that had never been there. It still overwrites -- refusing would make
# re-seeding after an upgrade impossible, which is the same trade `place_inputs`
# makes -- but it no longer does it quietly.


def test_seeding_a_fresh_catalogue_overwrites_nothing(cfg):
    result = seeding.seed(cfg, FIXTURE)

    assert result.written
    assert result.overwritten == ()


def test_seeding_does_not_claim_the_catalogue_example(cfg):
    """It is not a definition, and it is no longer seeding's to write."""
    result = seeding.seed(cfg, FIXTURE)

    assert not [entry for entry in result.written if entry.endswith(".example")]
    assert not [entry for entry in result.overwritten if entry.endswith(".example")]


def test_seeding_alone_leaves_a_definitions_folder_that_can_start(tmp_path):
    """The other half of the test above, and the one the library got wrong.

    From an environment naming no workspace, because seeding needs none.
    """
    from kingfisher import paths_from_env

    fresh = tmp_path / "untouched"
    paths = paths_from_env({"KINGFISHER_DEFINITIONS_DIR": str(fresh)})

    result = seeding.seed(paths, FIXTURE)

    assert result.written, "nothing was seeded, so the rest asserts nothing"
    assert (fresh / "models.yaml.example").is_file(), (
        "seeded but unusable: no worked example of the one file a deployment must write"
    )


def test_seeding_never_writes_the_catalogue_itself(cfg):
    """The one file seeding must not touch."""
    catalogue = cfg.definitions / "models.yaml"
    catalogue.write_text("mine: do not touch\n", encoding="utf-8")

    seeding.seed(cfg, FIXTURE)

    assert catalogue.read_text(encoding="utf-8") == "mine: do not touch\n"


def test_seeding_never_carries_bytecode_into_a_workspace(cfg, tmp_path):
    """The guard that only had to hold one level deep until a preset tool could be a
    package.
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


#: Every way the format lets a definition name a source id, and the names each one
#: says. Seeding reads these with a tolerant reader of its own -- it runs before
#: there is a catalogue, so it cannot refuse a document -- and a spelling that reader
#: stops covering does not fail: the definition reads as naming nobody and is copied
#: into a workspace that cannot load it. Two of these four were held by nothing.
SPELLINGS = {
    "a bare name": ("source_ids: A\n", ("A",)),
    "a list": ("source_ids: [A, B]\n", ("A", "B")),
    "a requirement set": ("source_ids: [{A, B}]\n", ("A", "B")),
    "a skills entry's own audience": ("skills:\n  - name: t\n    source_ids: [A]\n", ("A",)),
}


@pytest.mark.parametrize("spelling", sorted(SPELLINGS))
def test_every_way_of_naming_a_source_id_is_left_behind(spelling, cfg, tmp_path):
    """Driven through `seed` rather than through its reader, because what goes wrong
    is the copying: a definition whose audience this cannot see is written into a
    workspace where the names it asks for are not declared.
    """
    written, expected = SPELLINGS[spelling]
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "audienced.yaml").write_text(
        f"name: audienced\ndescription: d\n{written}system_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "agents/audienced.yaml" not in done.written
    left = [s for s in done.skipped if s.label == "agents/audienced.yaml"]
    assert left, f"a definition naming {expected} by {spelling} was copied anyway"
    assert sorted(left[0].names) == sorted(expected)
    assert left[0].wants == "source_ids"


def test_a_definition_naming_only_a_datazone_is_left_behind(cfg, tmp_path):
    """`datazone:` was invisible to seeding, in either format: the shipped `quarters.py`
    was copied for months with nothing in seed's report naming it -- and a datazone is a
    lock, which switches on access control in the workspace it is copied into.

    One spelling and not a table: a datazone is one name, never a list, and never on an
    entry -- an agent writing one is refused, so a subagent's own field is the only place
    it can be.
    """
    source = tmp_path / "presets"
    (source / "subagents").mkdir(parents=True)
    (source / "subagents" / "zoned.yaml").write_text(
        "name: zoned\ndescription: d\ndatazone: emea\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "subagents/zoned.yaml" not in done.written
    (left,) = done.skipped
    assert (left.label, left.names, left.datazones) == ("subagents/zoned.yaml", (), ("emea",))
    # Named as the field it was written in, so a reader looks for the right line.
    assert "names datazone (emea)" in "\n".join(done.report())
    assert "switches on access control" in "\n".join(done.report())


def test_a_star_datazone_is_left_behind_and_refused_where_it_loads(cfg, tmp_path):
    """Seed read `datazone: "*"` as "any" and copied the file, and the loader read it as a
    datazone named `*`: copied, it switched on access control and shut its subagent to
    every caller. Seed and the loader now agree it is no "any" -- left behind as a named
    datazone is, and refused by name if copied anyway.
    """
    source = tmp_path / "presets"
    (source / "subagents").mkdir(parents=True)
    (source / "subagents" / "ledger.yaml").write_text(
        "name: ledger\ndescription: d\ndatazone: '*'\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "subagents/ledger.yaml" not in done.written
    (left,) = done.skipped
    assert (left.label, left.datazones) == ("subagents/ledger.yaml", ("*",))

    assert "subagents/ledger.yaml" in seeding.seed(cfg, source, everything=True).written
    with pytest.raises(SubagentError, match=r"datazone '\*' is not 'any'"):
        _ = LocalSubagentRepository(subagents_dir(cfg)).specs


#: Every way a definition says who reaches it *in Python*: the only place a tool can say
#: it, and one of the two places a subagent can. Read by a tolerant reader of seeding's
#: own, as the YAML above is, so a spelling it stops covering does not fail -- the
#: definition is held back as one it cannot read, naming nobody.
CODE_SPELLINGS = {
    "a subagent entry's list": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": ["A", "B"]}]\n',
        ("A", "B"),
        (),
    ),
    "a subagent entry's lone name": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": "A"}]\n',
        ("A",),
        (),
    ),
    "a subagent entry's requirement set": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": [{"A", "B"}]}]\n',
        ("A", "B"),
        (),
    ),
    "a subagent entry's requirement written as a dict": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": [{"A": None, "B": None}]}]\n',
        ("A", "B"),
        (),
    ),
    "a subagent entry's datazone": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "datazone": "emea"}]\n',
        (),
        ("emea",),
    ),
    "a tool spec's source ids": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(t, source_ids=['A'])]\n",
        ("A",),
        (),
    ),
    "a tool spec's datazone": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(t, datazone='emea')]\n",
        (),
        ("emea",),
    ),
    # The loader reads each as the requirement it is; seed saw none of them.
    "a tool spec's requirement written as frozenset({...})": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\n"
        "TOOLS = [ToolSpec(t, source_ids=[frozenset({'sales_db', 'pii'})])]\n",
        ("pii", "sales_db"),
        (),
    ),
    "a subagent entry's requirement written as frozenset([...])": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": [frozenset(["A", "B"])]}]\n',
        ("A", "B"),
        (),
    ),
    "a subagent entry's requirement written as set(...)": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": [set(("A", "B"))]}]\n',
        ("A", "B"),
        (),
    ),
    # Held in a name the module binds once: the loader reads the value, and seed read the
    # name as naming nobody and copied the lock as an open definition.
    "a tool spec's source ids held in a name": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nIDS = ['A']\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        ("A",),
        (),
    ),
    "a tool spec's requirement held in a name": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nPAIR = frozenset({'A', 'B'})\n"
        "TOOLS = [ToolSpec(t, source_ids=[PAIR])]\n",
        ("A", "B"),
        (),
    ),
    "a tool spec's datazone held in a name": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nZONE = 'emea'\nTOOLS = [ToolSpec(t, datazone=ZONE)]\n",
        (),
        ("emea",),
    ),
    "a name held in a name": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nONE = 'A'\nIDS = [ONE]\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        ("A",),
        (),
    ),
    "a name bound in a chained assignment": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nIDS = ALSO = ['A']\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        ("A",),
        (),
    ),
    "a subagent entry's source ids held in an annotated name": (
        "subagents",
        'IDS: list[str] = ["A"]\n'
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": IDS}]\n',
        ("A",),
        (),
    ),
    # `"*"` is everyone in `source_ids`, but no "any" in a datazone, which refuses it.
    "a subagent entry's datazone written '*'": (
        "subagents",
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "datazone": "*"}]\n',
        (),
        ("*",),
    ),
    "a tool spec's datazone written '*'": (
        "tools",
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(t, datazone='*')]\n",
        (),
        ("*",),
    ),
}


@pytest.mark.parametrize("spelling", sorted(CODE_SPELLINGS))
def test_every_way_python_names_an_audience_is_left_behind(spelling, cfg, tmp_path):
    """Driven through `seed`, because what goes wrong is the copying: a definition whose
    audience this cannot see is written into a workspace where the names it asks for are
    not declared, and the whole catalogue it belongs to then refuses to load.
    """
    kind, written, source_ids, datazones = CODE_SPELLINGS[spelling]
    source = tmp_path / "presets"
    (source / kind).mkdir(parents=True)
    (source / kind / "audienced.py").write_text(written, encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert f"{kind}/audienced.py" not in done.written
    left = [one for one in done.skipped if one.label == f"{kind}/audienced.py"]
    assert left, f"a definition naming an audience by {spelling} was copied anyway"
    assert (sorted(left[0].names), sorted(left[0].datazones)) == (
        sorted(source_ids),
        sorted(datazones),
    )
    assert left[0].wants == "source_ids"
    assert not left[0].unread, f"{spelling} is written out, and was reported as computed"


@pytest.mark.parametrize(
    ("kind", "written"),
    [
        (
            "subagents",
            'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
            '             "source_ids": ["*"]}]\n',
        ),
        (
            "tools",
            "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
            "    return ''\n\n\nTOOLS = [ToolSpec(t, source_ids=['*'])]\n",
        ),
        (
            "tools",
            "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
            "    return ''\n\n\nEVERYONE = ['*']\nTOOLS = [ToolSpec(t, source_ids=EVERYONE)]\n",
        ),
    ],
)
def test_source_ids_of_everyone_are_copied_in_python(kind, written, cfg, tmp_path):
    """The control the `'*'` datazone rows need: in `source_ids` it is everyone, so a
    reader that took it for a name there too would leave an open definition behind.
    """
    source = tmp_path / "presets"
    (source / kind).mkdir(parents=True)
    (source / kind / "open.py").write_text(written, encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert f"{kind}/open.py" in done.written
    assert not done.skipped


def test_an_empty_frozenset_is_the_loaders_to_refuse_not_seeds_to_crash_on(cfg, tmp_path):
    """Reading inside a `frozenset(...)` call, seed must not take an argument that is not
    there: `frozenset()` names nobody, and is copied for the loader to refuse in its own
    words, as an unparseable file is.
    """
    source = tmp_path / "presets"
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "empty.py").write_text(
        "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(t, source_ids=[frozenset()])]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "tools/empty.py" in done.written


#: The head of a tool module, for the rows below that differ only after it.
_TOOL = "from kingfisher import ToolSpec\n\n\ndef t() -> str:\n    'A tool.'\n    return ''\n\n\n"

#: Every way a Python audience can be written that seed cannot read without running the
#: file, and how the line names it. One row per way a name is bound, since each is a
#: separate reason a name may not hold the value its one assignment gives it.
UNREAD = {
    "an imported name": (
        "tools",
        "from shared import IDS\n" + _TOOL + "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name bound twice": (
        "tools",
        _TOOL + "IDS = ['A']\nIDS = ['B']\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name imported over its assignment": (
        "tools",
        _TOOL + "IDS = ['A']\nfrom shared import IDS\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name a function binds": (
        "tools",
        _TOOL + "def build():\n    ids = ['A']\n    return ToolSpec(t, source_ids=ids)\n\n\n"
        "TOOLS = [build()]\n",
        "t: source_ids=ids",
    ),
    "a name a function rebinds": (
        "tools",
        _TOOL + "IDS = ['A']\n\n\ndef widen():\n    IDS = ['B']\n    return IDS\n\n\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name a parameter shadows": (
        "tools",
        _TOOL + "IDS = ['A']\n\n\ndef build(IDS):\n    return ToolSpec(t, source_ids=IDS)\n\n\n"
        "TOOLS = [build(['B'])]\n",
        "t: source_ids=IDS",
    ),
    "a name a function definition takes": (
        "tools",
        _TOOL + "IDS = ['A']\n\n\ndef IDS():\n    return ['B']\n\n\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name an exception binds": (
        "tools",
        _TOOL + "IDS = ['A']\ntry:\n    pass\nexcept ValueError as IDS:\n    pass\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name a pattern captures": (
        "tools",
        _TOOL + "IDS = ['A']\nmatch ['B']:\n    case [IDS]:\n        pass\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name a starred pattern captures": (
        "tools",
        _TOOL + "IDS = ['A']\nmatch ['B']:\n    case [*IDS]:\n        pass\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a name a mapping pattern captures": (
        "tools",
        _TOOL + "IDS = ['A']\nmatch {'k': 1}:\n    case {**IDS}:\n        pass\n"
        "TOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "two names bound to each other": (
        "tools",
        _TOOL + "ONE = OTHER\nOTHER = ONE\nTOOLS = [ToolSpec(t, source_ids=ONE)]\n",
        "t: source_ids=ONE",
    ),
    "a list the module computes": (
        "tools",
        _TOOL + "MORE = ['B']\nTOOLS = [ToolSpec(t, source_ids=['A'] + MORE)]\n",
        "t: source_ids=['A'] + MORE",
    ),
    "a datazone a call returns": (
        "tools",
        _TOOL + "TOOLS = [ToolSpec(t, datazone=zone())]\n",
        "t: datazone=zone()",
    ),
    "a starred name": (
        "tools",
        _TOOL + "IDS = ['A']\nTOOLS = [ToolSpec(t, source_ids=[*IDS])]\n",
        "t: source_ids=[*IDS]",
    ),
    "a requirement unpacked into braces": (
        "tools",
        _TOOL + "BASE = {'A': None}\nTOOLS = [ToolSpec(t, source_ids=[{**BASE}])]\n",
        "t: source_ids=[{**BASE}]",
    ),
    # Bound once and changed in place: the assignment says `[]`, and the loader reads
    # `["A"]`. Seed took the assignment for the value and copied the lock as open.
    "a list appended to after it is bound": (
        "tools",
        _TOOL + "IDS = []\nIDS.append('A')\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "an item stored into after it is bound": (
        "tools",
        _TOOL + "IDS = ['B']\nIDS[0] = 'A'\nTOOLS = [ToolSpec(t, source_ids=IDS)]\n",
        "t: source_ids=IDS",
    ),
    "a requirement changed through the list holding it": (
        "tools",
        _TOOL + "REQ = [{'A': None}]\nREQ[0]['B'] = None\nTOOLS = [ToolSpec(t, source_ids=REQ)]\n",
        "t: source_ids=REQ",
    ),
    "an entry's lock set by its key": (
        "subagents",
        'ENTRY = {"name": "s", "description": "d", "system_prompt": "Hi."}\n'
        'ENTRY["source_ids"] = ["A"]\nSUBAGENTS = [ENTRY]\n',
        "ENTRY['source_ids']",
    ),
    "an entry's datazone set by a call": (
        "subagents",
        'ENTRY = {"name": "s", "description": "d", "system_prompt": "Hi."}\n'
        'ENTRY.setdefault("datazone", "emea")\nSUBAGENTS = [ENTRY]\n',
        "ENTRY.setdefault('datazone', 'emea')",
    ),
    "a subagent entry's imported name": (
        "subagents",
        "from shared import IDS\n"
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": IDS}]\n',
        "s: source_ids=IDS",
    ),
}


@pytest.mark.parametrize("spelling", sorted(UNREAD))
def test_an_audience_seed_cannot_read_is_left_behind_as_written(spelling, cfg, tmp_path):
    """Seed took a value it could not walk for naming nobody, so a lock held in an imported
    name, or computed, was copied into a workspace as an open definition.
    """
    kind, written, unread = UNREAD[spelling]
    source = tmp_path / "presets"
    (source / kind).mkdir(parents=True)
    (source / kind / "computed.py").write_text(written, encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert f"{kind}/computed.py" not in done.written, f"{spelling} was copied as naming nobody"
    (left,) = done.skipped
    assert (left.wants, left.names, left.datazones, left.unread) == (
        "source_ids",
        (),
        (),
        (unread,),
    )


def test_the_skip_line_names_an_audience_seed_cannot_read_by_its_tool(cfg, tmp_path):
    """A line naming no source id would leave a reader nothing to look for before `--all`:
    it names the tool and the value as the file writes it.
    """
    source = tmp_path / "presets"
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "computed.py").write_text(UNREAD["an imported name"][1], encoding="utf-8")

    (line,) = seeding.seed(cfg, source).report()

    assert line.startswith(
        "skipped tools/computed.py — names an audience seed cannot read without running it "
        "(t: source_ids=IDS), and a lock switches on access control"
    ), line


def test_a_tool_package_computing_its_audience_is_left_behind_whole(cfg, tmp_path):
    """A package is decided by every module in it, so the one importing its audience must
    hold the package back, not leave the rest to be copied as a tool naming nobody.
    """
    source = tmp_path / "presets"
    package = source / "tools" / "profiler"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "profile.py").write_text(UNREAD["an imported name"][1], encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert [(one.label, one.unread) for one in done.skipped] == [
        ("tools/profiler", ("t: source_ids=IDS",))
    ]
    assert not (tools_dir(cfg) / "profiler").exists(), "half a package is not a tool"


def test_an_audience_seed_cannot_read_is_named_below_the_top_level(cfg, tmp_path):
    """A grouping folder is judged file by file through another path, which must carry what
    the file computes into the line as the top level does.
    """
    source = tmp_path / "presets"
    (source / "tools" / "group").mkdir(parents=True)
    (source / "tools" / "group" / "computed.py").write_text(
        UNREAD["an imported name"][1], encoding="utf-8"
    )

    done = seeding.seed(cfg, source)

    assert [(one.label, one.unread) for one in done.skipped] == [
        ("tools/group/computed.py", ("t: source_ids=IDS",))
    ]
    assert not (tools_dir(cfg) / "group" / "computed.py").exists()


def test_a_module_that_only_mentions_an_audience_is_copied(cfg, tmp_path):
    """The control the table above needs, and the reason this reads the syntax rather
    than the text: `subagents/timestamps/timestamps.py` is a worked example whose
    docstring tells you that a definition "may also say who reaches it, with `source_ids`
    and `datazone`". A reader matching the words would leave it behind.
    """
    source = tmp_path / "presets"
    (source / "subagents").mkdir(parents=True)
    (source / "subagents" / "documented.py").write_text(
        '"""A worked example.\n\nIt may also say who reaches it, with source_ids and\n'
        'datazone, as quarters.py does.\n"""\n\n'
        'SUBAGENTS = [{"name": "s", "description": "d", "system_prompt": "Hi."}]\n',
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "subagents/documented.py" in done.written
    assert not done.skipped


def test_a_tool_package_is_left_behind_whole(cfg, tmp_path):
    """A package is one definition, so it is decided as one. Dropping the module that
    names the audience and copying the rest leaves a folder that is not a tool -- which
    is worse than the copy, because the loader can at least explain the copy.
    """
    source = tmp_path / "presets"
    package = source / "tools" / "profiler"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "profile.py").write_text(
        "from kingfisher import ToolSpec\n\n\ndef p() -> str:\n    'A tool.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(p, source_ids=['A'])]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert [(one.label, one.names) for one in done.skipped] == [("tools/profiler", ("A",))]
    assert not (tools_dir(cfg) / "profiler").exists(), "half a package is not a tool"


def test_a_bundle_is_left_behind_whole(cfg, tmp_path):
    """A bundle whose definition named a source id was half-seeded: reported as seeded,
    its `ledger.yaml` held back and its `tools/count.py` copied, which `doctor` then
    called an orphaned folder and told the reader to rename.
    """
    source = tmp_path / "presets"
    bundle = source / "subagents" / "ledger"
    (bundle / "tools").mkdir(parents=True)
    (bundle / "ledger.yaml").write_text(
        "name: ledger\ndescription: d\nsource_ids: [A]\nbuiltin_tools: [read_file]\n"
        "tools:\n  - {name: count, source: bundled}\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (bundle / "tools" / "count.py").write_text(
        "def count(x: str) -> str:\n    'Count.'\n    return x\n\n\nTOOLS = [count]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "subagents/ledger" not in done.written
    assert [(one.label, one.names) for one in done.skipped] == [("subagents/ledger", ("A",))]
    assert not (subagents_dir(cfg) / "ledger").exists(), "half a bundle is not a subagent"


def test_a_bundle_naming_middleware_is_left_behind_whole(cfg, tmp_path):
    """The other reason a bundle is held back, whose remedy differs: pooled as an
    audience, the middleware it names would read as nothing and the bundle be copied.
    """
    source = tmp_path / "presets"
    bundle = source / "subagents" / "ledger"
    (bundle / "tools").mkdir(parents=True)
    (bundle / "ledger.yaml").write_text(
        "name: ledger\ndescription: d\nmiddlewares: [audit]\nbuiltin_tools: [read_file]\n"
        "tools:\n  - {name: count, source: bundled}\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (bundle / "tools" / "count.py").write_text(
        "def count(x: str) -> str:\n    'Count.'\n    return x\n\n\nTOOLS = [count]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert [(one.label, one.wants, one.names) for one in done.skipped] == [
        ("subagents/ledger", "middlewares", ("audit",))
    ]
    assert not (subagents_dir(cfg) / "ledger").exists()


def test_a_bundle_naming_nobody_is_copied_whole(cfg, tmp_path):
    """The control for the bundle above: deciding a folder as one must not hold back a
    bundle over a file the copy itself never writes.
    """
    source = tmp_path / "presets"
    bundle = source / "subagents" / "ledger"
    (bundle / "tools").mkdir(parents=True)
    (bundle / ".scratch").mkdir()
    (bundle / "ledger.yaml").write_text(
        "name: ledger\ndescription: d\nbuiltin_tools: [read_file]\n"
        "tools:\n  - {name: count, source: bundled}\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (bundle / "tools" / "count.py").write_text(
        "def count(x: str) -> str:\n    'Count.'\n    return x\n\n\nTOOLS = [count]\n",
        encoding="utf-8",
    )
    (bundle / ".scratch" / "draft.py").write_text(
        'SUBAGENTS = [{"name": "d", "source_ids": ["A"]}]\n', encoding="utf-8"
    )

    done = seeding.seed(cfg, source)

    assert "subagents/ledger" in done.written
    assert not done.skipped
    assert (subagents_dir(cfg) / "ledger" / "tools" / "count.py").is_file()


def test_a_bundle_is_left_behind_for_what_its_own_tool_names(cfg, tmp_path):
    """A bundled tool's audience is only in its Python, so a bundle read for its YAML
    alone would copy a tool into a workspace that has not declared who reaches it.
    """
    source = tmp_path / "presets"
    bundle = source / "subagents" / "ledger"
    (bundle / "tools").mkdir(parents=True)
    (bundle / "ledger.yaml").write_text(
        "name: ledger\ndescription: d\nbuiltin_tools: [read_file]\n"
        "tools:\n  - {name: count, source: bundled}\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (bundle / "tools" / "count.py").write_text(
        "from kingfisher import ToolSpec\n\n\ndef count() -> str:\n    'Count.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(count, datazone='emea')]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert [(one.label, one.datazones) for one in done.skipped] == [
        ("subagents/ledger", ("emea",))
    ]
    assert not (subagents_dir(cfg) / "ledger").exists()


def test_a_compiled_subagents_bundle_is_left_behind_whole(cfg, tmp_path):
    """A bundle is named for by a module as well as a document, and read as a folder of
    definitions it would copy `surveyor.py` and hold back the tool it lists.
    """
    source = tmp_path / "presets"
    bundle = source / "subagents" / "surveyor"
    (bundle / "tools").mkdir(parents=True)
    (bundle / "surveyor.py").write_text(
        'SUBAGENTS = [{"name": "surveyor", "description": "d", "build": None,\n'
        '              "tools": [{"name": "count", "source": "bundled"}]}]\n',
        encoding="utf-8",
    )
    (bundle / "tools" / "count.py").write_text(
        "from kingfisher import ToolSpec\n\n\ndef count() -> str:\n    'Count.'\n"
        "    return ''\n\n\nTOOLS = [ToolSpec(count, source_ids=['A'])]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert [(one.label, one.names) for one in done.skipped] == [("subagents/surveyor", ("A",))]
    assert not (subagents_dir(cfg) / "surveyor").exists()


def test_a_grouping_folder_is_judged_file_by_file(cfg, tmp_path):
    """One module naming a source id in `subagents/analysis/` held back an unrelated
    `profiler.yaml` beside it, which names nobody: a folder named for no definition is
    only organisation, and was decided as if it were one.
    """
    source = tmp_path / "presets"
    group = source / "subagents" / "analysis"
    group.mkdir(parents=True)
    (group / "profiler.yaml").write_text(
        "name: profiler\ndescription: d\nsystem_prompt: |\n  Hi.\n", encoding="utf-8"
    )
    (group / "zoned.py").write_text(
        'SUBAGENTS = [{"name": "z", "description": "d", "system_prompt": "Hi.",\n'
        '             "source_ids": ["A"]}]\n',
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert (subagents_dir(cfg) / "analysis" / "profiler.yaml").is_file()
    assert not (subagents_dir(cfg) / "analysis" / "zoned.py").exists()
    assert [(one.label, one.names) for one in done.skipped] == [
        ("subagents/analysis/zoned.py", ("A",))
    ]


def test_a_tools_grouping_folder_is_judged_file_by_file(cfg, tmp_path):
    """Every grouping-folder case was under `subagents/`, so seed could have copied a
    locked tool from `tools/<group>/` and switched access control on with nothing red."""
    source = tmp_path / "presets"
    group = source / "tools" / "warehouse"
    group.mkdir(parents=True)
    (group / "sql_query.py").write_text(
        "from kingfisher import ToolSpec\n\n\ndef sql_query(q: str) -> str:\n"
        "    'Run a query.'\n    return q\n\n\n"
        "TOOLS = [ToolSpec(sql_query, source_ids=['sales_db'])]\n",
        encoding="utf-8",
    )
    (group / "echo.py").write_text(
        "def echo(q: str) -> str:\n    'Echo.'\n    return q\n\n\nTOOLS = [echo]\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert (tools_dir(cfg) / "warehouse" / "echo.py").is_file()
    assert not (tools_dir(cfg) / "warehouse" / "sql_query.py").exists()
    assert [(one.label, one.names) for one in done.skipped] == [
        ("tools/warehouse/sql_query.py", ("sales_db",))
    ]


def test_the_skip_line_names_both_fields_a_definition_wrote(cfg, tmp_path):
    """Named only by its source ids, a file locked to a datazone as well would be read
    as open to any zone by whoever copies it with `--all`."""
    source = tmp_path / "presets"
    (source / "subagents").mkdir(parents=True)
    (source / "subagents" / "both.yaml").write_text(
        "name: both\ndescription: d\nsource_ids: [A]\ndatazone: emea\n"
        "system_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    report = "\n".join(seeding.seed(cfg, source).report())

    assert "names source_ids (A) and datazone (emea)" in report
    assert "source_ids.yaml" not in report, "a file nothing reads"


def test_a_definition_naming_nobody_is_copied(cfg, tmp_path):
    """The control the rule above needs: seeding leaves a definition behind *because*
    it names source ids, not because it leaves everything behind.
    """
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "plain.yaml").write_text(
        "name: plain\ndescription: d\nsystem_prompt: |\n  Hi.\n", encoding="utf-8"
    )

    done = seeding.seed(cfg, source)

    assert "agents/plain.yaml" in done.written
    assert not done.skipped


def test_a_definition_naming_middleware_is_left_behind(cfg, tmp_path):
    """`seed` reads one field and declines to copy on it."""
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "plain.yaml").write_text(
        "name: plain\ndescription: d\nsystem_prompt: |\n  Hi.\n", encoding="utf-8"
    )
    (source / "agents" / "wired.yaml").write_text(
        "name: wired\ndescription: d\nmiddlewares: [audit]\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "agents/plain.yaml" in done.written
    assert "agents/wired.yaml" not in done.written
    assert [(s.label, s.names) for s in done.skipped] == [("agents/wired.yaml", ("audit",))]


def test_a_tools_own_json_is_not_read_as_a_definition(cfg, tmp_path):
    """`seed` decides what to leave behind by reading one YAML field, and it has to
    check the extension before the content: JSON is valid YAML.

    A tool shipping `{"middlewares": [...]}` beside itself is configuration for
    that tool, not a definition naming this deployment's middleware. Dropping it
    leaves the workspace a tool that cannot start, and the skip list would not
    mention it -- that list is about definitions.
    """
    source = tmp_path / "presets"
    (source / "tools").mkdir(parents=True)
    (source / "tools" / "note.py").write_text("TOOLS = []\n", encoding="utf-8")
    (source / "tools" / "note.json").write_text('{"middlewares": ["audit"]}\n', encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert "tools/note.json" in done.written
    assert not done.skipped


def test_a_star_is_not_a_name_and_is_seeded(cfg, tmp_path):
    """`middlewares: ["*"]` resolves against whatever the deployment registered, which on
    an empty registry is nothing -- and raises nothing either way.
    """
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "starry.yaml").write_text(
        'name: starry\ndescription: d\nmiddlewares: ["*"]\nsystem_prompt: |\n  Hi.\n',
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source)

    assert "agents/starry.yaml" in done.written
    assert not done.skipped


def test_the_rule_holds_below_the_top_level(cfg, tmp_path):
    """A subagent in `subagents/analysis/` names middleware exactly as easily as one
    beside it.
    """
    source = tmp_path / "presets"
    nested = source / "subagents" / "analysis"
    nested.mkdir(parents=True)
    (nested / "deep.yaml").write_text(
        "name: deep\ndescription: d\nmiddlewares: [audit]\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (nested / "shallow.yaml").write_text(
        "name: shallow\ndescription: d\nsystem_prompt: |\n  Hi.\n", encoding="utf-8"
    )

    done = seeding.seed(cfg, source)

    landed = subagents_dir(cfg) / "analysis"
    assert (landed / "shallow.yaml").is_file()
    assert not (landed / "deep.yaml").exists(), "a nested definition walked the rule"
    assert [s.label for s in done.skipped] == ["subagents/analysis/deep.yaml"]


def test_everything_takes_what_the_default_leaves(cfg, tmp_path):
    """For the deployment that has already registered the names."""
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "wired.yaml").write_text(
        "name: wired\ndescription: d\nmiddlewares: [audit]\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )

    done = seeding.seed(cfg, source, everything=True)

    assert "agents/wired.yaml" in done.written
    assert not done.skipped


def test_a_definition_that_does_not_parse_is_copied_rather_than_judged(cfg, tmp_path):
    """Seeding is not a validator, and a broken file has a loader whose job is to say so
    in the terms of its own format.
    """
    source = tmp_path / "presets"
    (source / "agents").mkdir(parents=True)
    (source / "agents" / "broken.yaml").write_text("name: [unclosed\n", encoding="utf-8")

    done = seeding.seed(cfg, source)

    assert "agents/broken.yaml" in done.written
    assert not done.skipped


def test_seeding_twice_unchanged_is_silent(cfg):
    """By content, not by presence."""
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
    """`copytree` merges, so this one survives."""
    seeding.seed(cfg, FIXTURE)
    (cfg.skills_dir / "probe-skill" / "notes.md").write_text("mine", encoding="utf-8")

    assert seeding.seed(cfg, FIXTURE).overwritten == ()
    assert (cfg.skills_dir / "probe-skill" / "notes.md").read_text(encoding="utf-8") == "mine"


def test_an_edited_file_inside_a_skill_is_named_exactly(cfg):
    """Entries are what you asked for; files are what you might have lost."""
    seeding.seed(cfg, FIXTURE)
    (cfg.skills_dir / "probe-skill" / "SKILL.md").write_text("clobber me", encoding="utf-8")

    assert seeding.seed(cfg, FIXTURE).overwritten == ("skills/probe-skill/SKILL.md",)


def _grouped_source(tmp_path):
    """A source whose `subagents/group/` holds one definition seed copies, one it holds
    back for its middleware, and a dotfile."""
    group = tmp_path / "source" / "subagents" / "group"
    group.mkdir(parents=True)
    (group / "plain.yaml").write_text(
        "name: plain\ndescription: d\nsystem_prompt: |\n  Hi.\n", encoding="utf-8"
    )
    (group / "needs_mw.yaml").write_text(
        "name: wired\ndescription: d\nmiddlewares: [audit]\nsystem_prompt: |\n  Hi.\n",
        encoding="utf-8",
    )
    (group / ".DS_Store").write_text("theirs", encoding="utf-8")
    return tmp_path / "source"


def test_a_held_back_definition_is_not_reported_as_overwritten(cfg, tmp_path):
    """A second seed warned it overwrote the workspace's own copy of a definition it held
    back for its middleware, and left that copy untouched."""
    source = _grouped_source(tmp_path)
    seeding.seed(cfg, source)
    mine = subagents_dir(cfg) / "group" / "needs_mw.yaml"
    mine.write_text("edited by me\n", encoding="utf-8")

    result = seeding.seed(cfg, source)

    assert "subagents/group/needs_mw.yaml" not in result.overwritten
    assert mine.read_text(encoding="utf-8") == "edited by me\n"


def test_a_dotfile_is_not_reported_as_overwritten(cfg, tmp_path):
    """A second seed warned it overwrote the workspace's `.DS_Store`, which the copy never
    touches."""
    source = _grouped_source(tmp_path)
    seeding.seed(cfg, source)
    mine = subagents_dir(cfg) / "group" / ".DS_Store"
    mine.write_text("mine", encoding="utf-8")

    result = seeding.seed(cfg, source)

    assert "subagents/group/.DS_Store" not in result.overwritten
    assert mine.read_text(encoding="utf-8") == "mine"


def test_an_edited_copy_beside_what_seed_leaves_out_still_warns(cfg, tmp_path):
    """The control for `test_a_held_back_definition_is_not_reported_as_overwritten` and
    `test_a_dotfile_is_not_reported_as_overwritten`: in the same folder, a file the copy
    does replace is still named."""
    source = _grouped_source(tmp_path)
    seeding.seed(cfg, source)
    (subagents_dir(cfg) / "group" / "plain.yaml").write_text("edited\n", encoding="utf-8")

    assert seeding.seed(cfg, source).overwritten == ("subagents/group/plain.yaml",)


def test_the_readme_subagent_table_matches_the_real_field_set(formats_doc):
    """The table is where a contributor learns which fields exist, and now that an
    unlisted one is an error, a stale row is a definition that will not load.
    """
    from kingfisher.core.definitions.subagents.spec import KNOWN

    readme = (formats_doc).read_text(encoding="utf-8")
    table = readme.split("## Subagents")[1].split("\n---")[0]
    documented = {
        line.split("|")[1].strip().strip("`")
        for line in table.splitlines()
        if line.startswith("| `")
    }

    assert documented == KNOWN


def test_the_readme_links_into_no_asset_tree(formats_doc):
    """The page has to stand on its own, because the files are leaving."""
    import re

    readme = (formats_doc).read_text(encoding="utf-8")
    targets = [t for _, t in re.findall(r"\[([^\]]+)\]\(([^)]+)\)", readme)]

    into_assets = [t for t in targets if t.split("/")[0] in {"skills", "subagents", "tools"}]
    assert not into_assets, f"README links into an asset tree: {into_assets}"


def test_every_complete_definition_in_the_readme_parses(formats_doc):
    """The README shows a whole definition before it shows the field table, and a
    documented example that does not load is worse than none -- it is copied, it
    fails, and the format gets blamed.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.core.definitions.agents.reading import read
    from kingfisher.core.definitions.documents import DefinitionText

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
        if is_agent:
            read(DefinitionText(block, _Path("readme.yaml")))
        else:
            a_subagent(block, "readme.yaml")


#: What a fenced block has to mention to be a definition fragment rather than a
#: `models.yaml` example, which the same page also shows.
DEFINITION_FIELDS = ("tools:", "skills:", "subagents:", "middlewares:", "builtin_tools:")


def test_every_field_fragment_in_the_readme_parses_too(formats_doc):
    """The other half of the page, and the half that rotted.

    Its neighbour above reads only blocks starting with `name:`, and says so -- the
    fragments showing one field are not definitions. Nothing read them, so when the
    audienced fields changed shape on 2026-09-03 every fragment showing the old one
    stayed on the page, correct-looking and wrong, and the whole suite passed.
    """
    import re
    from pathlib import Path as _Path

    from kingfisher.core.definitions.agents.reading import read
    from kingfisher.core.definitions.documents import DefinitionText

    required = {
        "name": "name: probe\n",
        "description": "description: A probe.\n",
        "system_prompt": "system_prompt: |\n  Do the task.\n",
    }
    fragments = [
        (section.startswith("Agents"), body)
        for section in re.split(r"\n## ", formats_doc.read_text(encoding="utf-8"))
        for body in re.findall(r"```yaml\n(.*?)```", section, re.DOTALL)
        if not body.startswith("name:") and any(f in body for f in DEFINITION_FIELDS)
    ]

    assert fragments, "no field fragments found -- this asserts nothing"
    for is_agent, fragment in fragments:
        # Only what it leaves out: a whole definition shown under its path writes its own,
        # and a key written twice is refused.
        written = set(re.findall(r"^(\w+):", fragment, re.MULTILINE))
        whole = "".join(line for key, line in required.items() if key not in written) + fragment
        if is_agent:
            read(DefinitionText(whole, _Path("readme.yaml")))
        else:
            a_subagent(whole, "readme.yaml")


# -- the one preset that consults another ---------------------------------


def test_the_readme_run_on_example_is_valid(cfg, session_dir, formats_doc):
    """The second call the README shows, built for real."""
    from langchain_core.messages import AIMessage

    from kingfisher import RunOn
    from tests.conftest import FakeToolCallingModel

    _materialise((formats_doc).read_text(encoding="utf-8"), cfg)

    build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
        capabilities=Capabilities(
            subagents=("reviewer", "second-opinion"), models=("cheap-model",)
        ),
        run_on={"second-opinion": RunOn("cheap-model")},
    )

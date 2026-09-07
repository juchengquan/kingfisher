"""Folders in the catalogue: which kinds get them, and what a folder cannot do."""

from __future__ import annotations

import pytest

from kingfisher.skills.catalogue import LocalSkillRepository
from kingfisher.subagents.catalogue import LocalSubagentRepository
from kingfisher.tools.catalogue import LocalToolRepository, ToolError, tool_name
from kingfisher.tools.spec import Offering

TOOL = """from langchain_core.tools import tool


@tool
def {name}(x: str) -> str:
    \"\"\"A tool that does {name}.\"\"\"
    return x


TOOLS = [{name}]
"""

SUBAGENT = """name: {name}
description: A delegate called {name}.
system_prompt: |
  You do the thing.
"""


def _tool(directory, name, *, filename=None):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / (filename or f"{name}.py")).write_text(TOOL.format(name=name), encoding="utf-8")


def _subagent(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.yaml").write_text(SUBAGENT.format(name=name), encoding="utf-8")


# -- tools: folders and packages ------------------------------------------


def test_a_tool_in_a_subfolder_is_found(tmp_path):
    """The ask, at its simplest: `tools/research/find_company.py` loads."""
    _tool(tmp_path / "research", "find_company")

    assert LocalToolRepository(tmp_path).names == ("find_company",)


def test_nesting_does_not_reach_the_name(tmp_path):
    """The rule everything else depends on."""
    _tool(tmp_path / "research" / "deep" / "deeper", "find_company")

    assert LocalToolRepository(tmp_path).names == ("find_company",), "the folders reached the name"


def test_a_package_is_one_unit_and_its_helpers_are_not_scanned(tmp_path):
    """The case worth building for, and the one that failed before."""
    pkg = tmp_path / "research"
    pkg.mkdir(parents=True)
    (pkg / "client.py").write_text("def resolve(name): return f'found:{name}'\n", encoding="utf-8")
    (pkg / "finder.py").write_text(
        "from langchain_core.tools import tool\n"
        "from .client import resolve\n\n"
        "@tool\ndef find_company(name: str) -> str:\n"
        '    """Look a company up."""\n'
        "    return resolve(name)\n",
        encoding="utf-8",
    )
    (pkg / "__init__.py").write_text(
        "from .finder import find_company\n\nTOOLS = [find_company]\n", encoding="utf-8"
    )

    tools = LocalToolRepository(tmp_path).tools

    assert [tool_name(t) for t in tools] == ["find_company"]
    assert tools[0].invoke({"name": "acme"}) == "found:acme", "the relative import did not resolve"


def test_two_catalogues_may_each_hold_a_package_of_the_same_name(tmp_path):
    """The isolation the flat loader was built for, kept."""
    for workspace in ("wsA", "wsB"):
        pkg = tmp_path / workspace / "research"
        pkg.mkdir(parents=True)
        (pkg / "client.py").write_text(f"SEED = {workspace!r}\n", encoding="utf-8")
        (pkg / "probe.py").write_text(
            "from langchain_core.tools import tool\n"
            "from .client import SEED\n\n"
            "@tool\ndef probe(x: str) -> str:\n"
            '    """Which workspace."""\n'
            "    return SEED\n",
            encoding="utf-8",
        )
        (pkg / "__init__.py").write_text(
            "from .probe import probe\n\nTOOLS = [probe]\n", encoding="utf-8"
        )

    a = LocalToolRepository(tmp_path / "wsA").tools[0]
    b = LocalToolRepository(tmp_path / "wsB").tools[0]

    assert a.invoke({"x": ""}) == "wsA"
    assert b.invoke({"x": ""}) == "wsB", "the second package resolved the first one's helper"


def test_a_package_must_still_declare_its_exports(tmp_path):
    """Declared, never inferred -- the same rule a flat module follows."""
    pkg = tmp_path / "research"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ToolError, match=r"research/__init__\.py: must define TOOLS"):
        _ = LocalToolRepository(tmp_path).tools


def test_an_error_names_the_folder_it_came_from(tmp_path):
    """`find_company.py` stops identifying a file once three folders may hold one."""
    (tmp_path / "research").mkdir(parents=True)
    (tmp_path / "research" / "broken.py").write_text("VALUE = 1\n", encoding="utf-8")

    with pytest.raises(ToolError, match=r"research/broken\.py"):
        _ = LocalToolRepository(tmp_path).tools


def test_a_relative_import_in_a_loose_file_says_what_to_do(tmp_path):
    """The failure someone actually hits on the way to writing a package."""
    sub = tmp_path / "sub"
    sub.mkdir(parents=True)
    (sub / "_client.py").write_text("def r(n): return n\n", encoding="utf-8")
    (sub / "thing.py").write_text(
        "from langchain_core.tools import tool\n"
        "from ._client import r\n\n"
        "@tool\ndef thing(x: str) -> str:\n"
        '    """T."""\n'
        "    return r(x)\n\n"
        "TOOLS = [thing]\n",
        encoding="utf-8",
    )

    with pytest.raises(ToolError, match=r"a relative import needs a package"):
        _ = LocalToolRepository(tmp_path).tools


def test_two_folders_may_each_define_one_name(tmp_path):
    """This used to stop the deployment, and was unfixable by anyone who owned neither
    file.
    """
    _tool(tmp_path / "research", "find_company")
    _tool(tmp_path / "sales", "find_company", filename="lookup.py")

    found = LocalToolRepository(tmp_path).found

    assert [one.reference for one in found] == [
        "research/find_company.py::find_company",
        "sales/lookup.py::find_company",
    ]


@pytest.mark.parametrize("debris", ["__pycache__", ".venv", ".hidden"])
def test_the_walk_refuses_to_descend_into_debris(tmp_path, debris):
    """New guard, because the exposure is new."""
    junk = tmp_path / debris
    junk.mkdir(parents=True)
    (junk / "boom.py").write_text("raise RuntimeError('this should never be imported')\n",
                                  encoding="utf-8")
    _tool(tmp_path, "safe")

    assert LocalToolRepository(tmp_path).names == ("safe",)


def test_sources_say_where_a_nested_tool_lives(tmp_path):
    """`--list` needs this: a folder exists so a person can find a file, and a bare name
    sends them grepping instead.
    """
    _tool(tmp_path / "research", "find_company")
    _tool(tmp_path, "flat")

    assert Offering.of(LocalToolRepository(tmp_path).found).sources == {
        "find_company": "research/find_company.py",
        "flat": "flat.py",
    }


def test_a_package_is_reported_as_a_directory(tmp_path):
    """With its trailing slash, so `csv_profile` the folder does not read as a file that
    would have been `csv_profile.py`.
    """
    pkg = tmp_path / "csv_profile"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text(
        TOOL.format(name="csv_columns") + "\nTOOLS = [csv_columns]\n", encoding="utf-8"
    )

    assert Offering.of(LocalToolRepository(tmp_path).found).sources == {
        "csv_columns": "csv_profile/"
    }


# -- subagents: folders, no packages --------------------------------------


def test_a_subagent_in_a_subfolder_is_found(tmp_path):
    """YAML we parse ourselves, so a walk is the whole feature."""
    _subagent(tmp_path / "analysis" / "deep", "profiler")

    assert tuple(LocalSubagentRepository(tmp_path).specs) == ("profiler",)


def test_a_nested_subagent_keeps_its_own_name(tmp_path):
    """The filename is not authoritative and neither is the folder: the `name:` field is
    what a request activates and what `task` dispatches.
    """
    directory = tmp_path / "analysis"
    directory.mkdir(parents=True)
    (directory / "whatever.yaml").write_text(SUBAGENT.format(name="profiler"), encoding="utf-8")

    assert tuple(LocalSubagentRepository(tmp_path).specs) == ("profiler",)


def test_two_folders_may_each_define_one_subagent_name(tmp_path):
    """Same as tools, and for the same reason: refusing the pair on sight stopped the
    whole catalogue loading over a clash no single agent had yet asked for, and
    nobody who owned neither file could fix it.
    """
    _subagent(tmp_path / "analysis", "profiler")
    _subagent(tmp_path / "review", "profiler")

    assert sorted(LocalSubagentRepository(tmp_path).specs) == [
        "analysis/profiler.yaml::profiler",
        "review/profiler.yaml::profiler",
    ]


def test_sources_say_where_a_nested_subagent_lives(tmp_path):
    """The mirror of `test_sources_say_where_a_nested_tool_lives`, and the half of the
    pair that was missing.
    """
    _subagent(tmp_path / "analysis", "profiler")
    _subagent(tmp_path, "flat")

    assert LocalSubagentRepository(tmp_path).sources == {
        "profiler": "analysis/profiler.yaml",
        "flat": "flat.yaml",
    }


def test_sources_report_the_file_when_the_name_is_not_it(tmp_path):
    """The case that makes this worth having at all."""
    directory = tmp_path / "analysis"
    directory.mkdir(parents=True)
    (directory / "whatever.yaml").write_text(SUBAGENT.format(name="profiler"), encoding="utf-8")

    assert LocalSubagentRepository(tmp_path).sources == {"profiler": "analysis/whatever.yaml"}


# -- skills: the kind that goes one level, not many -----------------------


def test_the_repository_still_lists_only_the_root(tmp_path):
    """`names` is what a *store-backed* catalogue mounts by, and a store has no folders
    -- so this stays a root listing on purpose while the registry, which reads
    through sources, is the one that sees `research::company-lookup`.
    """
    nested = tmp_path / "research" / "company-lookup"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: company-lookup\ndescription: x\n---\nBody.\n", encoding="utf-8"
    )

    assert LocalSkillRepository(tmp_path).names == ()
    assert LocalSkillRepository(tmp_path).misplaced == (), "one level loads now"


def test_a_second_level_of_grouping_is_where_it_stops(tmp_path):
    """Making our own scan recurse further would advertise a skill the agent then could
    not open, which is worse than not offering it.
    """
    nested = tmp_path / "research" / "deep" / "company-lookup"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text(
        "---\nname: company-lookup\ndescription: x\n---\nBody.\n", encoding="utf-8"
    )

    assert LocalSkillRepository(tmp_path).misplaced == (
        "research/deep/company-lookup",
    ), "the warning has to still fire"

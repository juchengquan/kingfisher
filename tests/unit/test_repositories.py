"""One class per kind of definition, and one read behind it."""

from __future__ import annotations

import pytest

from kingfisher.domain.ports import (
    AssetRepository,
    SkillRepository,
    SubagentRepository,
    ToolRepository,
)
from kingfisher.kinds.skills.catalogue import LocalSkillRepository
from kingfisher.kinds.subagents import catalogue as store
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.kinds.tools.catalogue import LocalToolRepository
from kingfisher.kinds.tools.spec import Offering

NOISY = """
import sys
print("EXECUTED", file=sys.stderr)

def noisy() -> str:
    "A tool."
    return "ok"

TOOLS = [noisy]
"""

DEFINITION = "name: {name}\ndescription: A subagent.\nsystem_prompt: |\n  x\n"


@pytest.fixture
def catalogue(tmp_path):
    """One of each kind, in three directories."""
    (tmp_path / "skills" / "greeting").mkdir(parents=True)
    (tmp_path / "skills" / "greeting" / "SKILL.md").write_text(
        "---\nname: greeting\ndescription: Says hello.\n---\n\nHello.\n", encoding="utf-8"
    )
    (tmp_path / "subagents").mkdir()
    (tmp_path / "subagents" / "one.yaml").write_text(
        DEFINITION.format(name="alpha"), encoding="utf-8"
    )
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "noisy.py").write_text(NOISY, encoding="utf-8")
    return tmp_path


# -- the shape a deployment may replace -----------------------------------


def test_each_local_repository_satisfies_the_port_for_its_kind(catalogue):
    """The point of the ports: a deployment holding its definitions somewhere else
    supplies its own, and nothing downstream knows.
    """
    skills = LocalSkillRepository(catalogue / "skills")
    subagents = LocalSubagentRepository(catalogue / "subagents")
    tools = LocalToolRepository(catalogue / "tools")

    assert isinstance(skills, SkillRepository)
    assert isinstance(subagents, SubagentRepository)
    assert isinstance(tools, ToolRepository)


def test_all_three_answer_the_one_question_the_grant_layer_asks(catalogue):
    """`names` is the whole of the shared vocabulary, and it is shared because
    capabilities filter every kind by name and by nothing else.
    """
    every = (
        LocalSkillRepository(catalogue / "skills"),
        LocalSubagentRepository(catalogue / "subagents"),
        LocalToolRepository(catalogue / "tools"),
    )

    assert all(isinstance(repo, AssetRepository) for repo in every)
    assert [repo.names for repo in every] == [("greeting",), ("alpha",), ("noisy",)]


# -- read once, however many views are taken -------------------------------


def test_a_tool_repository_imports_each_module_once_for_every_view_of_it(catalogue, capfd):
    """The reason this kind became a class."""
    tools = LocalToolRepository(catalogue / "tools")

    assert tools.names == ("noisy",)
    assert Offering.of(tools.found).sources == {"noisy": "noisy.py"}
    assert [entry.name for entry in tools.found] == ["noisy"]
    assert len(tools.tools) == 1

    assert capfd.readouterr().err.count("EXECUTED") == 1


def test_a_subagent_repository_parses_each_definition_once_for_both_views(catalogue, monkeypatch):
    """The same fix one kind over."""
    parsed = []
    # Patched on the reading module rather than on a name the catalogue imported:
    # the catalogue calls `reading.read` through the module now, so a name bound
    # into its own namespace is no longer the thing that runs.
    real = store.reading.read

    def counting(text, path):
        parsed.append(path)
        return real(text, path)

    monkeypatch.setattr(store.reading, "read", counting)

    subagents = LocalSubagentRepository(catalogue / "subagents")
    assert set(subagents.specs) == {"alpha"}
    assert subagents.sources == {"alpha": "one.yaml"}
    assert subagents.names == ("alpha",)

    assert len(parsed) == 1, "the definition was parsed more than once"


def test_a_skill_repository_lists_once_for_both_of_its_questions(catalogue, monkeypatch):
    """Cheapest of the three -- a listing, not a parse -- and cached for the same
    reason: a catalogue's repository answers every turn of a deployment's life from
    one read.
    """
    listings = []
    real_iterdir = type(catalogue).iterdir

    def counting(self):
        listings.append(self)
        return real_iterdir(self)

    monkeypatch.setattr(type(catalogue), "iterdir", counting)

    skills = LocalSkillRepository(catalogue / "skills")
    assert skills.names == ("greeting",)
    assert skills.names == ("greeting",)

    assert len(listings) == 1, "the directory was listed again for a cached answer"


# -- and the cost of reading once -----------------------------------------


def test_a_repository_does_not_notice_a_definition_written_after_it_read(catalogue):
    """Stated as behaviour rather than left to be discovered."""
    subagents = LocalSubagentRepository(catalogue / "subagents")
    assert set(subagents.specs) == {"alpha"}

    (catalogue / "subagents" / "two.yaml").write_text(
        DEFINITION.format(name="beta"), encoding="utf-8"
    )

    assert set(subagents.specs) == {"alpha"}
    assert set(LocalSubagentRepository(catalogue / "subagents").specs) == {"alpha", "beta"}


def test_a_broken_definition_raises_on_the_read_and_not_on_construction(catalogue):
    """Which is what lets `--list` build one and still report the failure over the rest
    of the inventory, and what lets `Definitions.warm` choose when a deployment pays
    for it.
    """
    # A malformed definition, where this used to use two files claiming one
    # name -- that pair is legal now and told apart by file, so it no longer
    # says anything about *when* a repository reads.
    (catalogue / "subagents" / "bad.yaml").write_text(
        "name: [not, a, string]\n", encoding="utf-8"
    )

    subagents = LocalSubagentRepository(catalogue / "subagents")  # no raise

    with pytest.raises(SubagentError):
        _ = subagents.specs

"""One class per kind of definition, and one read behind it."""

from __future__ import annotations

import pytest

from kingfisher.domain.ports import (
    AgentRepository,
    AssetRepository,
    MiddlewareRepository,
    SkillRepository,
    SubagentRepository,
    ToolRepository,
)
from kingfisher.kinds.agents.catalogue import LocalAgentRepository
from kingfisher.kinds.middlewares.catalogue import LocalMiddlewareRepository
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


# -- the shape everything downstream reads -----------------------------


def test_each_local_repository_satisfies_the_port_for_its_kind(catalogue):
    """Every member a port declares is read without a default, so a local repository
    missing one fails here rather than at whichever reader reaches it first.
    """
    skills = LocalSkillRepository(catalogue / "skills")
    subagents = LocalSubagentRepository(catalogue / "subagents")
    tools = LocalToolRepository(catalogue / "tools")

    assert isinstance(skills, SkillRepository)
    assert isinstance(subagents, SubagentRepository)
    assert isinstance(tools, ToolRepository)
    assert isinstance(LocalAgentRepository(catalogue / "agents"), AgentRepository)
    assert isinstance(
        LocalMiddlewareRepository(catalogue / "middlewares"), MiddlewareRepository
    )


#: Reads of a repository member's name that are not reads of a repository, each with
#: what they are reading instead. The rule matches by name, so these are the collisions.
NOT_A_REPOSITORY = {
    ("kingfisher/application/origins.py", "root"): (
        "a `SessionStore`, which a deployment does implement, and which may have no "
        "directory at all"
    ),
}


def test_nothing_reads_a_repository_member_with_a_default():
    """A member read with `getattr` and a default answers nothing for a repository that
    lacks it -- which is how an agent repository without `documents` ran without pinning
    the session to its agent, and a subagent repository without `bundles` dropped a
    delegate's carried tools. A member worth reading goes on the port.
    """
    import ast

    from tests.conftest import repository_root

    declared = {
        name
        for port in (
            AssetRepository,
            AgentRepository,
            MiddlewareRepository,
            SkillRepository,
            SubagentRepository,
            ToolRepository,
        )
        for name in vars(port)
        if not name.startswith("_")
    }
    assert {"documents", "bundles", "root"} <= declared, (
        "the ports no longer declare the members this rule is about"
    )

    src = repository_root() / "src"
    reads = {
        (path.relative_to(src).as_posix(), node.args[1].value)
        for path in sorted(src.rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", None) in ("getattr", "hasattr")
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.Constant)
        and node.args[1].value in declared
    }

    assert not reads - set(NOT_A_REPOSITORY), (
        f"{sorted(reads - set(NOT_A_REPOSITORY))} -- read the member directly. If a "
        "repository may lack it, the port says what it answers instead"
    )
    assert set(NOT_A_REPOSITORY) <= reads, (
        f"{sorted(set(NOT_A_REPOSITORY) - reads)} is exempted and no longer read -- "
        "drop the entry"
    )


def test_the_kinds_a_grant_names_answer_the_one_question_it_asks(catalogue):
    """`names` is the whole of the shared vocabulary, and it is shared because
    capabilities filter every kind by name and by nothing else.

    Skills are not here, and that is the one exception: which skills there are is the
    registry's answer, because a directory holding a `SKILL.md` is not the same set as
    the skills deepagents parses. A listing here answered both halves wrongly at once
    -- it missed every skill in a source folder and offered one whose file does not
    parse -- so `SkillRepository` stopped promising it.
    """
    every = (
        LocalSubagentRepository(catalogue / "subagents"),
        LocalToolRepository(catalogue / "tools"),
    )

    assert all(isinstance(repo, AssetRepository) for repo in every)
    assert [repo.names for repo in every] == [("alpha",), ("noisy",)]
    assert not isinstance(LocalSkillRepository(catalogue / "skills"), AssetRepository)


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

    def counting(path):
        parsed.append(path)
        return real(path)

    monkeypatch.setattr(store.reading, "read", counting)

    subagents = LocalSubagentRepository(catalogue / "subagents")
    assert set(subagents.specs) == {"alpha"}
    assert subagents.sources == {"alpha": "one.yaml"}
    assert subagents.names == ("alpha",)

    assert len(parsed) == 1, "the definition was parsed more than once"


def test_a_skill_repository_walks_once_for_the_question_it_answers(catalogue, monkeypatch):
    """Cheapest of the three -- a walk, not a parse -- and cached for the same reason:
    a catalogue's repository answers every turn of a deployment's life from one read.
    """
    walks = []
    real_rglob = type(catalogue).rglob

    def counting(self, pattern, **kwargs):
        walks.append(self)
        return real_rglob(self, pattern, **kwargs)

    monkeypatch.setattr(type(catalogue), "rglob", counting)

    skills = LocalSkillRepository(catalogue / "skills")
    assert skills.misplaced == ()
    assert skills.misplaced == ()

    assert len(walks) == 1, "the directory was walked again for a cached answer"


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

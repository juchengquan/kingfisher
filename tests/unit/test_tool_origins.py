"""Where a tool came from, in the places that have to say so."""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.workspace.sessions import ensure_session_layout
from kingfisher.subagents.spec import SubagentSpec
from kingfisher.tools.catalogue import LocalToolRepository
from kingfisher.tools.spec import Offering, offered
from tests.conftest import FakeToolCallingModel, tools_dir

TOOL = """from langchain_core.tools import tool


@tool
def {name}(x: str) -> str:
    \"\"\"A tool called {name}.\"\"\"
    return x


TOOLS = [{name}]
"""

NOISY = """import sys
from langchain_core.tools import tool

print("EXECUTED", file=sys.stderr)


@tool
def noisy(x: str) -> str:
    \"\"\"Noisy.\"\"\"
    return x


TOOLS = [noisy]
"""


def _tool(directory, name):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.py").write_text(TOOL.format(name=name), encoding="utf-8")


# -- one walk, not two ----------------------------------------------------


def test_the_origins_come_off_the_same_walk_that_loaded_the_tools(cfg, capfd):
    """A tool module is Python, so reading it runs it."""
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "noisy.py").write_text(NOISY, encoding="utf-8")

    found = LocalToolRepository(tools_dir(cfg)).found

    assert [entry.name for entry in found] == ["noisy"]
    assert [entry.source for entry in found] == ["noisy.py"]
    assert capfd.readouterr().err.count("EXECUTED") == 1


def test_a_prewalked_catalogue_is_not_walked_again(cfg, capfd):
    """`--list` needs the origins *and* a compiled graph, and the graph is the only way
    to know the built-in set.
    """
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "noisy.py").write_text(NOISY, encoding="utf-8")

    found = LocalToolRepository(tools_dir(cfg)).found
    capfd.readouterr()  # discard the walk's own execution

    build_agent(
        cfg,
        session_dir=ensure_session_layout(cfg.workspace / "s"),
        model=FakeToolCallingModel(responses=[]),
        workspace_tools=found,
    )

    assert capfd.readouterr().err.count("EXECUTED") == 0, "the build walked again"


# -- what a refusal says --------------------------------------------------


def test_a_request_naming_an_unknown_tool_is_told_where_the_real_ones_live(cfg):
    """The reader mistyped a name and needs to scan for the one they meant."""
    _tool(tools_dir(cfg) / "research", "find_company")

    with pytest.raises(CapabilityError) as raised:
        build_agent(
            cfg,
            session_dir=ensure_session_layout(cfg.workspace / "s"),
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(tools=("find_compny",)),
        )

    message = str(raised.value)
    assert "unknown tool(s): find_compny" in message
    assert "find_company" in message
    assert "research/find_company.py" in message


def test_a_subagent_naming_an_unknown_tool_is_told_the_same_thing(cfg):
    """The case that prompted this: someone editing a YAML by hand."""
    _tool(tools_dir(cfg) / "research", "find_company")
    spec = SubagentSpec(
        name="typo",
        description="Names a tool nothing offers.",
        system_prompt="You do a thing.",
        tools=("find_compny",),
    )

    with pytest.raises(CapabilityError) as raised:
        Offering(
            builtin=("read_file",),
            workspace=("find_company",),
            sources={"find_company": "research/find_company.py"},
        ).refuse_unknown(
            spec.builtin_tools, spec.tools, subject=f"subagent {spec.name!r}"
        )

    message = str(raised.value)
    assert "subagent 'typo' names unknown tool(s): find_compny" in message
    assert "research/find_company.py" in message


def test_a_builtin_is_listed_without_a_file(cfg):
    """It has no file, and a blank column against `read_file` would be noise."""
    listing = offered({"find_company": "research/find_company.py"},
                                 ["find_company", "read_file"])

    assert "find_company  (research/find_company.py)" in listing
    assert listing.splitlines()[-1].strip() == "read_file"


def test_an_empty_workspace_says_so_rather_than_printing_nothing(cfg):
    """A refusal that trails off after "this workspace offers" reads as a bug in
    kingfisher rather than an empty catalogue.
    """
    assert offered({}, []) == "  (none)"

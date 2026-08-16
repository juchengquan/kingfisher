"""Where a tool came from, in the places that have to say so.

Two questions this answers, and they arrived together. A refusal that lists
names alone leaves the reader grepping once tools may sit in folders -- so both
tool refusals now name the file each one is defined in. And reading those files
means *executing* them, so the origins have to come off the same walk that
loaded the tools rather than a second one.

What is deliberately not here: a way to write `csv_profile.csv_columns` in a
subagent definition. A qualified name would select nothing, because two tools
cannot share a name in the first place -- the catalogue refuses to load. It
could only ever assert where a file sits, which is a check that fires on a
harmless refactor and guards against nothing.
"""

from __future__ import annotations

import pytest

from kingfisher.domain.capabilities import Capabilities, CapabilityError
from kingfisher.domain.subagent import SubagentSpec
from kingfisher.infrastructure import tool_store
from kingfisher.infrastructure.agent import build_agent
from kingfisher.infrastructure.delegation import refuse_unknown_tools
from kingfisher.infrastructure.tool_store import LocalToolRepository
from tests.conftest import FakeToolCallingModel

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
    """A tool module is Python, so reading it runs it.

    `load_tools` and `sources` were separate passes, and anything wanting both
    -- a listing, a refusal -- executed every workspace module twice. Any
    module-level side effect happened twice with it.
    """
    cfg.tools_dir.mkdir(parents=True, exist_ok=True)
    (cfg.tools_dir / "noisy.py").write_text(NOISY, encoding="utf-8")

    found = LocalToolRepository(cfg.tools_dir).found

    assert [entry.name for entry in found] == ["noisy"]
    assert [entry.source for entry in found] == ["noisy.py"]
    assert capfd.readouterr().err.count("EXECUTED") == 1


def test_a_prewalked_catalogue_is_not_walked_again(cfg, capfd):
    """`--list` needs the origins *and* a compiled graph, and the graph is the
    only way to know the built-in set. Fetching them apart ran every tool
    module a second time, so the walk is handed in."""
    cfg.tools_dir.mkdir(parents=True, exist_ok=True)
    (cfg.tools_dir / "noisy.py").write_text(NOISY, encoding="utf-8")

    found = LocalToolRepository(cfg.tools_dir).found
    capfd.readouterr()  # discard the walk's own execution

    build_agent(
        cfg,
        session_dir=cfg.workspace / "s",
        model=FakeToolCallingModel(responses=[]),
        workspace_tools=found,
    )

    assert capfd.readouterr().err.count("EXECUTED") == 0, "the build walked again"


# -- what a refusal says --------------------------------------------------


def test_a_request_naming_an_unknown_tool_is_told_where_the_real_ones_live(cfg):
    """The reader mistyped a name and needs to scan for the one they meant.

    One per line with its file, rather than a parenthesised tuple: the tuple is
    the shape nobody finishes reading, and a bare name is what sends someone
    grepping through `tools/` for a file that could be anywhere.
    """
    _tool(cfg.tools_dir / "research", "find_company")

    with pytest.raises(CapabilityError) as raised:
        build_agent(
            cfg,
            session_dir=cfg.workspace / "s",
            model=FakeToolCallingModel(responses=[]),
            capabilities=Capabilities(tools=("find_compny",)),
        )

    message = str(raised.value)
    assert "unknown tool(s): find_compny" in message
    assert "find_company" in message
    assert "research/find_company.py" in message


def test_a_subagent_naming_an_unknown_tool_is_told_the_same_thing(cfg):
    """The case that prompted this: someone editing a YAML by hand.

    Same wording as the request-side refusal, because the two disagreeing about
    format would be its own small confusion.
    """
    _tool(cfg.tools_dir / "research", "find_company")
    spec = SubagentSpec(
        name="typo",
        description="Names a tool nothing offers.",
        system_prompt="You do a thing.",
        tools=("find_compny",),
    )

    with pytest.raises(CapabilityError) as raised:
        refuse_unknown_tools(
            spec,
            builtin=("read_file",),
            workspace=("find_company",),
            sources={"find_company": "research/find_company.py"},
        )

    message = str(raised.value)
    assert "subagent 'typo' names unknown tool(s): find_compny" in message
    assert "research/find_company.py" in message


def test_a_builtin_is_listed_without_a_file(cfg):
    """It has no file, and a blank column against `read_file` would be noise.

    The two axes are reported apart already -- that separation exists because
    "3 tools not granted" meant nothing when it could have been either kind.
    """
    listing = tool_store.offered({"find_company": "research/find_company.py"},
                                 ["find_company", "read_file"])

    assert "find_company  (research/find_company.py)" in listing
    assert listing.splitlines()[-1].strip() == "read_file"


def test_an_empty_workspace_says_so_rather_than_printing_nothing(cfg):
    """A refusal that trails off after "this workspace offers" reads as a bug
    in kingfisher rather than an empty catalogue."""
    assert tool_store.offered({}, []) == "  (none)"

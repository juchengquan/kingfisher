"""Loading a workspace's own tools.

Unlike a skill or a subagent, a tool is *code*. Everything here is about that
difference: what gets imported, what is refused, and what is never guessed at.
"""

from __future__ import annotations

import pytest

from kingfisher.infrastructure.catalogue.tools import LocalToolRepository, ToolError, tool_name

MODULE = '''
from langchain_core.tools import tool


@tool
def add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


TOOLS = [add]
'''


def _write(directory, name: str, body: str):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body)
    return directory


def test_a_module_contributes_the_tools_it_names(tmp_path):
    tools = LocalToolRepository(_write(tmp_path / "tools", "maths.py", MODULE)).tools

    assert [tool_name(t) for t in tools] == ["add"]


def test_a_directory_that_does_not_exist_is_not_an_error(tmp_path):
    """Most workspaces have no tools of their own."""
    assert LocalToolRepository(tmp_path / "absent").tools == ()


def test_an_empty_directory_contributes_nothing(tmp_path):
    (tmp_path / "tools").mkdir()

    assert LocalToolRepository(tmp_path / "tools").tools == ()


def test_a_module_without_an_export_list_is_refused_by_name(tmp_path):
    """Scanning the module for anything callable would guess. The repo's other
    formats make the definition state its own name; this makes it state its own
    exports."""
    directory = _write(tmp_path / "tools", "quiet.py", "def add(a, b):\n    return a + b\n")

    with pytest.raises(ToolError, match=r"quiet\.py"):
        _ = LocalToolRepository(directory).tools


def test_a_module_that_cannot_be_imported_is_refused_loudly(tmp_path):
    """Skipping it would give an agent quietly fewer tools than the workspace
    offers -- the same failure `CapabilityError` exists to prevent."""
    directory = _write(tmp_path / "tools", "broken.py", "import a_module_that_is_not_installed\n")

    with pytest.raises(ToolError, match=r"broken\.py"):
        _ = LocalToolRepository(directory).tools


def test_two_modules_claiming_one_tool_name_both_load(tmp_path):
    """Two files may each define an `add`, and the file tells them apart."""
    directory = _write(tmp_path / "tools", "maths.py", MODULE)
    _write(directory, "maths_again.py", MODULE)

    assert sorted(one.reference for one in LocalToolRepository(directory).found) == [
        "maths.py::add",
        "maths_again.py::add",
    ]


def test_one_module_claiming_a_name_twice_is_refused(tmp_path):
    """Where the refusal still belongs. There is no second file to tell these
    apart, so no reference could pick between them and nothing downstream could
    offer a way to say which."""
    body = MODULE.replace("TOOLS = [add]", "TOOLS = [add, add]")
    directory = _write(tmp_path / "tools", "twice.py", body)

    with pytest.raises(ToolError, match="defined twice in this file"):
        _ = LocalToolRepository(directory).tools


def test_private_modules_are_skipped(tmp_path):
    """So a tool can be split across files without every part being a module
    that must export TOOLS."""
    directory = _write(tmp_path / "tools", "maths.py", MODULE)
    _write(directory, "_helpers.py", "VALUE = 1\n")

    assert [tool_name(t) for t in LocalToolRepository(directory).tools] == ["add"]


def test_the_export_must_be_a_sequence_not_a_single_tool(tmp_path):
    body = MODULE.replace("TOOLS = [add]", "TOOLS = add")
    directory = _write(tmp_path / "tools", "wrong.py", body)

    with pytest.raises(ToolError, match=r"wrong\.py"):
        _ = LocalToolRepository(directory).tools


def test_modules_load_in_a_stable_order(tmp_path):
    """Two workspaces with the same files must build the same agent."""
    directory = _write(tmp_path / "tools", "b_second.py", MODULE.replace("add", "beta"))
    _write(directory, "a_first.py", MODULE.replace("add", "alpha"))

    assert [tool_name(t) for t in LocalToolRepository(directory).tools] == ["alpha", "beta"]


def test_importing_a_tool_leaves_no_bytecode_in_the_catalogue(tmp_path):
    """`__pycache__` beside the source means inside the tools catalogue -- a
    directory holding what a person authored, and the one an operator is most
    likely to version. Observed in a fresh workspace: two `.pyc` files sitting
    next to the two tools, noise in `git status` at best.

    Asserted on the directory rather than on `sys.dont_write_bytecode`, because
    what matters is that nothing was written, not how that was arranged.
    """
    catalogue = _write(tmp_path / "tools", "maths.py", MODULE)

    loaded = LocalToolRepository(catalogue).tools

    assert len(loaded) == 1, "the tool did not import, so this proves nothing"
    assert not list(catalogue.rglob("__pycache__")), "bytecode was written into the catalogue"
    assert not list(catalogue.rglob("*.pyc"))

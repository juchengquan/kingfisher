"""What a workspace's Python modules must declare, said the same way for every kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from kingfisher.kinds.middlewares.catalogue import LocalMiddlewareRepository as Middlewares
from kingfisher.kinds.middlewares.catalogue import MiddlewareError
from kingfisher.kinds.subagents.catalogue import LocalSubagentRepository as Subagents
from kingfisher.kinds.subagents.spec import SubagentError
from kingfisher.kinds.tools.catalogue import LocalToolRepository as Tools
from kingfisher.kinds.tools.catalogue import ToolError


@dataclass(frozen=True)
class Kind:
    """One kind's modules, and the sentences its author is owed."""

    read: Callable[[Path], Any]
    error: type[ValueError]
    export: str
    holding: str
    example: str


KINDS = (
    Kind(lambda root: Tools(root).found, ToolError, "TOOLS", "tools", "my_tool"),
    Kind(lambda root: Subagents(root).specs, SubagentError, "SUBAGENTS", "subagents",
         "my_subagent"),
    Kind(lambda root: Middlewares(root).found, MiddlewareError, "MIDDLEWARES", "middleware",
         "MyMiddleware"),
)
EACH = pytest.mark.parametrize("kind", KINDS, ids=[k.export for k in KINDS])


def _module(root: Path, body: str, name: str = "m.py") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(body, encoding="utf-8")
    return root


@EACH
def test_a_module_declaring_nothing_is_told_what_to_define(kind, tmp_path):
    """Driven through each kind's own repository rather than the shared function, so
    an envelope wired to the wrong kind's error or noun is caught here and not by
    reading the three declarations next to each other.
    """
    root = _module(tmp_path / "root", "X = 1\n")

    with pytest.raises(kind.error) as raised:
        kind.read(root)

    assert f"m.py: must define {kind.export}, the {kind.holding} it contributes" in str(
        raised.value
    )


@EACH
def test_an_export_that_is_not_a_sequence_says_what_to_write(kind, tmp_path):
    """The drift this closes: middleware named the type it got and stopped there, while
    tools and subagents also said what to write instead. Nothing compared the three.
    """
    root = _module(tmp_path / "root", f"{kind.export} = {{'a': 1}}\n")

    with pytest.raises(kind.error) as raised:
        kind.read(root)

    assert f"got dict -- write {kind.export} = [{kind.example}]" in str(raised.value)


@EACH
def test_a_package_declaring_nothing_names_the_file_to_open(kind, tmp_path):
    """A folder is one module, so the refusal has to name `__init__.py` -- naming the
    folder sends an author to a directory to look for a line that goes in one file.
    """
    root = tmp_path / "root"
    _module(root / "pack", "X = 1\n", name="__init__.py")

    with pytest.raises(kind.error) as raised:
        kind.read(root)

    assert f"pack/__init__.py: must define {kind.export}" in str(raised.value)

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name


@pytest.fixture
def tools(shipped):
    return {tool_name(one.tool): one.tool for one in LocalToolRepository(shipped / "tools").found}


@dataclass
class _Got:
    """What a backend hands back for one file."""

    content: bytes | None
    error: str | None = None


class _Backend:
    """A backend holding one answer, so a tool's own read is what is under test."""

    def __init__(self, content: bytes) -> None:
        self._content = content

    def download_files(self, paths: list[str]) -> list[_Got]:
        return [_Got(self._content) for _ in paths]


@dataclass
class _Runtime:
    context: Any


def _asked(content: bytes) -> _Runtime:
    """A tool's runtime, whose backend answers every file it is asked for with `content`."""
    return _Runtime(context=type("_Ctx", (), {"backend": _Backend(content)})())

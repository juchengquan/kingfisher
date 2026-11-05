from __future__ import annotations

import pytest

from kingfisher.core.definitions.tools.catalogue import LocalToolRepository, tool_name


@pytest.fixture
def tools(shipped):
    return {tool_name(one.tool): one.tool for one in LocalToolRepository(shipped / "tools").found}

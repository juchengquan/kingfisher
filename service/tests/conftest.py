"""The library's test fixtures, made available to this package's tests."""

from __future__ import annotations

import pytest

from tests.conftest import (  # noqa: F401 -- re-exported as fixtures
    an_agent,
    dirs,
    session_dir,
    workspace,
)
from tests.conftest import cfg as _cfg


@pytest.fixture
def cfg(workspace):  # noqa: F811 -- wraps the library fixture rather than replacing it
    """The library's configuration, plus one agent to run."""
    configured = _cfg.__wrapped__(workspace)
    an_agent(configured)
    return configured

"""The library's test fixtures, made available to this package's tests.

`conftest.py` applies to its own directory and below, so moving these tests out
of `tests/` took `cfg`, `workspace` and `session_dir` away from them. They are
imported rather than rewritten: a service test asking for `cfg` should get the
*same* configuration a library test gets, or the two suites are exercising
different workspaces and only one of them is the real one.

This is the only place that reaches across the two distributions, and it reaches
into test support rather than into `kingfisher` itself -- the rule about using
the library through its front door is about the package, and is checked
separately in `test_architecture`.

It does mean this suite runs from the repository root, alongside the library's,
rather than standing alone against an installed wheel. That is what
`testpaths` already does for `assets/tests` and is the same trade: one command
has to run both, or a change to the library breaks the service with nothing red.
"""

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
    """The library's configuration, plus one agent to run.

    Opening a session names an agent now, so a workspace with none cannot serve
    a turn at all. Every test here is about the HTTP surface rather than about
    which agent is running, so they get one and say nothing more about it.
    """
    configured = _cfg.__wrapped__(workspace)
    an_agent(configured)
    return configured

"""When the suite spreads over xdist's workers by default, and when it stays in one process.

Driven with configs built here rather than a real run: with the worker check broken, a
real run starts workers that start workers, and the test would take the machine with it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import pytest

from tests.conftest import workers_by_default


def config(*, worker: bool = False, xdist: bool = True, **option: object) -> pytest.Config:
    """A config as `pytest_cmdline_main` sees it: xdist's worker carries `workerinput`."""
    made = SimpleNamespace(
        option=SimpleNamespace(
            **{"numprocesses": None, "usepdb": False, "capture": "fd", **option}
        ),
        pluginmanager=SimpleNamespace(hasplugin=lambda name: xdist and name == "xdist"),
    )
    if worker:
        made.workerinput = {"workerid": "gw0"}
    return cast("pytest.Config", made)


def test_a_worker_never_starts_workers_of_its_own():
    """xdist resets `-n` in a worker and runs the hook again: a worker choosing a count
    started workers of its own, each doing the same, which on 2026-11-11 and 11-12 took
    the machine to 1,833 processes before it was restarted."""
    assert workers_by_default(config(worker=True), {}, 12) is None


def test_a_run_left_to_choose_takes_two_workers_and_never_half_the_machine():
    """More leaves less for the checkouts running the suite beside this one."""
    assert workers_by_default(config(), {}, 12) == 2
    assert workers_by_default(config(), {"CI": "false"}, 12) == 2
    assert workers_by_default(config(), {}, 3) == 1, "two of three cores is most of them"
    assert workers_by_default(config(), {}, 1) == 1, "one core still runs the suite"
    assert workers_by_default(config(), {}, None) == 1, "an unknown count still runs it"


@pytest.mark.parametrize(
    ("made", "environ"),
    [
        pytest.param(config(), {"CI": "true"}, id="on CI"),
        pytest.param(config(numprocesses=0), {}, id="-n0 asked for"),
        pytest.param(config(numprocesses=3), {}, id="a count asked for"),
        pytest.param(config(usepdb=True), {}, id="--pdb, which xdist refuses"),
        pytest.param(config(capture="no"), {}, id="-s, whose prints a worker swallows"),
        pytest.param(config(xdist=False), {}, id="-p no:xdist"),
    ],
)
def test_a_run_that_said_how_to_run_is_left_as_it_said(made, environ):
    """CI's one process is the baseline its timings are read against, and a count, a
    debugger or `-s` overridden would be a flag that does nothing."""
    assert workers_by_default(made, environ, 12) is None

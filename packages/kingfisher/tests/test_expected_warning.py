"""The one dependency warning kingfisher asks for and does not want to hear.

`max_snapshot_bytes=1` is deliberate -- it is what makes the sandbox affordable
to leave on -- and `langchain_quickjs` warns every time it drops an image,
which is every turn. So the warning reports a setting we chose, on a schedule
nothing can reduce, into the middle of the agent's streamed prose. Observed
three times in one afternoon of live runs.

It reaches the terminal at all only because a library that configures no
logging leaves Python's last-resort handler to print WARNING and above to
stderr. Which is the right default for kingfisher to keep: the point below is
that this suppresses one record and decides nothing else.
"""

from __future__ import annotations

import logging

from kingfisher.infrastructure.harness.agent import quieten_expected_snapshot_drop

LOGGER = "langchain_quickjs.middleware"

#: The four this must not touch, quoted from the dependency. The last one is why
#: silencing the whole logger was the wrong fix: a workspace tool skipped for
#: its name is exactly the silent drop this codebase refuses everywhere else.
STILL_HEARD = (
    ("Failed to restore QuickJS snapshot for thread_id=%s", ("t1",)),
    ("Failed to create QuickJS snapshot for thread_id=%s", ("t1",)),
    ("Failed to diff QuickJS snapshot; storing full anchor", ()),
    ("Skipping PTC tool %r: %r is not a valid JS identifier", ("my tool", "my tool")),
)


def _emitted(caplog, message: str, *args: object) -> bool:
    """Whether one record survives the filter on the real logger."""
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        logging.getLogger(LOGGER).warning(message, *args)
    return bool(caplog.records)


def test_the_expected_drop_is_silenced(caplog):
    quieten_expected_snapshot_drop()

    assert not _emitted(
        caplog, "Dropping QuickJS snapshot for thread_id=%s (size=%d bytes exceeds %d)", "t", 2, 1
    )


def test_every_other_warning_still_arrives(caplog):
    """The reason this is a filter rather than a level.

    Setting the logger to ERROR would have been one line and would have hidden
    all four of these -- including a workspace tool being skipped, which nobody
    would ever have found out about.
    """
    quieten_expected_snapshot_drop()

    for message, args in STILL_HEARD:
        assert _emitted(caplog, message, *args), message


def test_installing_it_twice_leaves_one_filter():
    """`_interpreter` runs per request, so this is called once a turn. Without
    the guard the list grows for the life of the process."""
    logger = logging.getLogger(LOGGER)
    quieten_expected_snapshot_drop()
    quieten_expected_snapshot_drop()
    quieten_expected_snapshot_drop()

    installed = [f for f in logger.filters if type(f).__name__ == "_ExpectedSnapshotDrop"]
    assert len(installed) == 1


def test_nothing_else_about_logging_is_decided():
    """A library that calls `basicConfig` decides for a program it does not own.

    This attaches one filter to one named logger: no handler, no level, no root
    configuration. Whoever hosts kingfisher still chooses where warnings go.
    """
    quieten_expected_snapshot_drop()
    root = logging.getLogger()

    assert not root.handlers or all(
        type(h).__name__ != "_ExpectedSnapshotDrop" for h in root.handlers
    )
    assert logging.getLogger(LOGGER).level == logging.NOTSET  # never set a level


def _installed() -> list:
    return [
        f
        for f in logging.getLogger(LOGGER).filters
        if type(f).__name__ == "_ExpectedSnapshotDrop"
    ]


def test_building_the_sandbox_installs_it(cfg, session_dir):
    """The wiring, not just the function.

    Found by mutation: deleting the call from `_interpreter` left every other
    test in this file green, because they all call it directly. The filter
    could have stopped being installed and nothing would have said so.
    """
    from dataclasses import replace

    from langchain_core.messages import AIMessage
    from tests.conftest import FakeToolCallingModel

    from kingfisher.infrastructure.harness.agent import build_agent

    logger = logging.getLogger(LOGGER)
    for stale in _installed():
        logger.removeFilter(stale)
    assert not _installed()

    build_agent(
        replace(cfg, interpreter_enabled=True),
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert len(_installed()) == 1


def test_a_deployment_without_the_sandbox_is_left_alone(cfg, session_dir):
    """Nothing is decided for a deployment that never enables it -- the warning
    it would suppress cannot be emitted, and a library should not reach into a
    logger it has no reason to touch."""
    from langchain_core.messages import AIMessage
    from tests.conftest import FakeToolCallingModel

    from kingfisher.infrastructure.harness.agent import build_agent

    logger = logging.getLogger(LOGGER)
    for stale in _installed():
        logger.removeFilter(stale)

    build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(responses=[AIMessage(content="ok")]),
    )

    assert not _installed()


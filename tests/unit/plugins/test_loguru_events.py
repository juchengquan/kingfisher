"""`LoguruRunEvents`: kingfisher's events as an app on loguru hears them."""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from kingfisher.core.interfaces import RunEvents
from kingfisher.core.logged import handed, warning

logger = pytest.importorskip("loguru").logger

RUN_EVENT = {
    "ts": 1.0,
    "event": "model_call",
    "level": "INFO",
    "message": "model_call",
    "session_id": "s1",
    "turn_id": "t1",
    "input_tokens": 12,
}


class Wrapped(RunEvents):
    """A sink of the app's own around the shipped one, outside kingfisher's package."""

    def __init__(self, inner: RunEvents) -> None:
        self.inner = inner

    def record(self, event: Mapping[str, object]) -> None:
        self.inner.record(event)


@pytest.fixture
def sink() -> RunEvents:
    from kingfisher.plugins import LoguruRunEvents

    return LoguruRunEvents()


@pytest.fixture
def heard():
    """What loguru keeps during the test, with its handlers and kingfisher's switch put
    back after."""
    records: list = []
    handler = logger.add(records.append, level="TRACE", format="{message}")
    yield records
    logger.remove(handler)
    logger.enable("kingfisher")


def test_disabling_kingfisher_in_loguru_silences_its_events(sink, heard):
    """`logger.disable("kingfisher")` missing a record named after whoever called the sink,
    as one logged at a depth of one is when a sink of the app's wraps this one."""
    sinks = (sink, Wrapped(sink))
    for each in sinks:
        handed(each, RUN_EVENT)
    # The control: both arrive while kingfisher is on, so silence below is the switch.
    assert len(heard) == len(sinks)

    heard.clear()
    logger.disable("kingfisher")
    for each in sinks:
        handed(each, RUN_EVENT)

    assert not heard, [message.record["name"] for message in heard]


def test_an_event_arrives_at_its_level_saying_its_message_and_carrying_itself(sink, heard):
    """An event reaching loguru at another level, saying something else, or without the
    event in `extra["kingfisher"]`."""
    said = warning("scratch_folder_left", "a scratch run's folder was left", path="/x")
    for event in (RUN_EVENT, said):
        handed(sink, event)

    assert [
        (m.record["level"].name, m.record["message"], m.record["extra"]["kingfisher"])
        for m in heard
    ] == [("INFO", "model_call", RUN_EVENT), ("WARNING", said["message"], said)]


def test_a_brace_in_a_message_is_said_as_written(sink, heard):
    """A `}` read by loguru as a format field, which raises and loses the warning: a
    refused definition's message quotes the YAML error, `expected ',' or '}'`."""
    said = warning("definitions_refused", "ParserError: expected ',' or '}', but got ':'")
    handed(sink, said)

    assert [m.record["message"] for m in heard] == [said["message"]]

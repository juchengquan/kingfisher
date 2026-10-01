"""What a turn did, handed to the deployment's `RunEvents` as it happens."""

from __future__ import annotations

import logging

from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from kingfisher import Kingfisher
from kingfisher.domain.ports import RunEvents
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.runlog import (
    RUN_LOGGER,
    LoggedRunEvents,
    RunLogger,
    usage_of,
)
from tests.conftest import RecordedEvents, StubCheckpointer, start
from tests.unit.test_run import StubAgent


def _logger(sink) -> RunLogger:
    return RunLogger(sink, model="m", endpoint="gateway", session_id="s", turn_id="t1")


def _reply(**usage):
    message = AIMessage(content="ok", usage_metadata=usage)
    return LLMResult(generations=[[ChatGeneration(message=message)]])


def test_every_event_says_which_session_and_turn_it_came_from():
    """They leave the session for the deployment's logs, so the ids are the only way
    one session's events are found again among everyone else's.
    """
    sink = RecordedEvents()

    _logger(sink).run_start("a task")

    (event,) = sink.events
    assert (event["session_id"], event["turn_id"]) == ("s", "t1")
    assert (event["model"], event["endpoint"]) == ("m", "gateway")


def test_usage_totals_the_model_calls_it_was_handed():
    sink = RecordedEvents()
    logger = _logger(sink)
    logger.run_start("a task")
    for sent, got, cached in ((100, 10, 80), (200, 20, 100)):
        logger.on_llm_end(
            _reply(
                input_tokens=sent,
                output_tokens=got,
                total_tokens=sent + got,
                input_token_details={"cache_read": cached},
            )
        )

    usage = usage_of(sink.events)

    assert usage.calls == 2
    assert usage.input_tokens == 300
    assert usage.output_tokens == 30
    assert usage.cache_read == 180
    assert usage.cached_share == 0.6


def test_events_with_no_model_calls_total_zero():
    sink = RecordedEvents()
    _logger(sink).run_start("a task")

    usage = usage_of(sink.events)

    assert usage.calls == 0
    assert usage.cached_share is None  # not a division by zero


def test_a_sink_that_fails_does_not_fail_the_turn(cfg, caplog):
    """The sink is the deployment's code, and `run_start` and `run_end` are called from
    kingfisher's own turn, where langchain's guard around callbacks does not reach. A
    broken log shipper cost the caller their answer.
    """

    class Broken:
        def record(self, event):
            raise ConnectionRefusedError

    start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("42"), threads=StubCheckpointer(), run_events=Broken())

    with caplog.at_level(logging.WARNING):
        result = kf.run(Request("t", session_id="s"))

    assert result.answer == "42"
    assert "run_start" in caplog.text, "the failure was swallowed without a word"


def test_a_turn_records_its_start_and_end(cfg):
    sink = RecordedEvents()
    start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("42"), threads=StubCheckpointer(), run_events=sink)

    result = kf.run(Request("t", session_id="s"))

    assert [e["event"] for e in sink.events] == ["run_start", "run_end"]
    assert {e["turn_id"] for e in sink.events} == {result.turn_id}


def test_a_sink_that_is_empty_is_still_the_one_used(cfg):
    """`None` means the default, and nothing else does: a sink that keeps its events in
    a list of its own is falsy until the first one arrives, and was replaced by the
    logger before it got one.
    """

    class Kept(list):
        def record(self, event):
            self.append(event)

    sink = Kept()
    start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("42"), threads=StubCheckpointer(), run_events=sink)

    kf.run(Request("t", session_id="s"))

    assert [e["event"] for e in sink] == ["run_start", "run_end"]


def test_with_nothing_wired_the_events_go_to_the_run_logger(cfg, caplog):
    """The default a deployment gets, and where it finds them: one JSON line per event
    on `kingfisher.run`, with the mapping itself on the record for a handler that
    ships structured logs.
    """
    start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("42"), threads=StubCheckpointer())

    with caplog.at_level(logging.INFO, logger=RUN_LOGGER):
        kf.run(Request("t", session_id="s"))

    records = [r for r in caplog.records if r.name == RUN_LOGGER]
    assert [r.run_event["event"] for r in records] == ["run_start", "run_end"]


def test_a_task_keeps_its_own_alphabet_in_the_log(caplog):
    """A run log is read by a person, and `\\u00e9` is not what they asked for.

    Every task the log carries is text somebody typed, so most of the world's
    tasks escape into hex under the default. Nothing named this, and the flag
    that prevents it reads as an ordinary keyword.
    """
    with caplog.at_level(logging.INFO, logger=RUN_LOGGER):
        _logger(LoggedRunEvents()).run_start("Résume le rapport trimestriel")

    assert "Résume" in caplog.text
    assert "\\u00e9" not in caplog.text


def test_the_default_sink_is_a_run_events():
    """Checked by shape, the way a deployment's own is: a rename of `record` on the
    default would leave it satisfying nothing while every turn still logged.
    """
    assert isinstance(LoggedRunEvents(), RunEvents)


def test_a_file_sink_keeps_one_line_per_event_in_its_own_alphabet(tmp_path):
    """What `kingfisher run --log` writes. One line per event, appended, so a second
    turn adds to the file rather than replacing it -- and the parent is made, because a
    path somebody typed on the command line is the likeliest to name a folder that is
    not there yet.
    """
    import json

    from kingfisher.infrastructure.harness.runlog import JsonlRunEvents

    sink = JsonlRunEvents(tmp_path / "logs" / "turns.jsonl")
    _logger(sink).run_start("Résume le rapport")
    _logger(sink).run_end(ok=True, answer_chars=3)

    written = (tmp_path / "logs" / "turns.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in written.splitlines()]
    assert [e["event"] for e in events] == ["run_start", "run_end"]
    assert "Résume" in written

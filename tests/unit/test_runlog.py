"""What a turn did, handed to the deployment's `RunEvents` as it happens."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from kingfisher import Kingfisher
from kingfisher.core.interfaces import RunEvents
from kingfisher.core.request import Request
from kingfisher.core.session.runlog import (
    RUN_LOGGER,
    LoggedRunEvents,
    RunLogger,
    usage_of,
)
from kingfisher.plugins import LocalSessions
from tests.conftest import RecordedEvents, an_agent, start, subagents_dir, tools_dir
from tests.unit.scripted import Scripted
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

    class Broken(RunEvents):
        def record(self, event):
            raise ConnectionRefusedError

    start(cfg, "s")
    kf = Kingfisher(
        cfg,
        graph=StubAgent("42"),
        sessions=LocalSessions(),
        run_events=Broken(),
    )

    with caplog.at_level(logging.WARNING):
        result = kf.run(Request("t", session_id="s"))

    assert result.answer == "42"
    assert "run_start" in caplog.text, "the failure was swallowed without a word"


def test_a_turn_records_its_start_and_end(cfg):
    sink = RecordedEvents()
    start(cfg, "s")
    kf = Kingfisher(
        cfg,
        graph=StubAgent("42"),
        sessions=LocalSessions(),
        run_events=sink,
    )

    result = kf.run(Request("t", session_id="s"))

    assert [e["event"] for e in sink.events] == ["run_start", "run_end"]
    assert {e["turn_id"] for e in sink.events} == {result.turn_id}


@pytest.mark.parametrize(
    ("fields", "runs"),
    [({"model": "elsewhere-model"}, ("elsewhere-model", "elsewhere")),
     ({}, ("fake-model", "fake"))],
    ids=["its-own", "the-default"],
)
def test_every_event_names_the_model_the_agent_runs(scripted, fields, runs):
    """An agent naming its own model had every event stamped with the deployment's
    default, so a log read for what a turn cost priced it on a model that never ran."""
    an_agent(scripted, "pinned", **fields)
    start(scripted, "s")
    Scripted.script.append(AIMessage("done"))
    sink = RecordedEvents()

    Kingfisher(scripted, sessions=LocalSessions(), run_events=sink).run(
        Request("t", agent="pinned", session_id="s")
    )

    assert "model_call" in {e["event"] for e in sink.events}
    assert {(e["model"], e["endpoint"]) for e in sink.events} == {runs}


def test_a_subagents_calls_name_the_model_it_runs(scripted):
    """Every event carried the main agent's model, so a subagent's calls on a model of
    its own were logged, and priced, on one that never made them."""
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "helper.yaml").write_text(
        "name: helper\ndescription: Helps.\nmodel: elsewhere-model\n"
        "system_prompt: |\n  You help.\n",
        encoding="utf-8",
    )
    an_agent(scripted, "pinned", subagents="[helper]", builtin_tools="[task]")
    start(scripted, "s")
    asked = {"description": "go", "subagent_type": "helper"}
    Scripted.script.extend([
        AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}]),
        AIMessage("helped"),
        AIMessage("done"),
    ])
    sink = RecordedEvents()

    Kingfisher(scripted, sessions=LocalSessions(), run_events=sink).run(
        Request("t", agent="pinned", session_id="s")
    )

    assert [(e["model"], e["endpoint"]) for e in sink.named("model_call")] == [
        ("fake-model", "fake"),
        ("elsewhere-model", "elsewhere"),
        ("fake-model", "fake"),
    ]


def test_a_failed_call_names_the_model_that_made_it():
    """`model_error` is the other event a call ends in, and kept the turn's stamp."""
    sink = RecordedEvents()
    logger = RunLogger(
        sink, model="m", endpoint="gateway", session_id="s", turn_id="t1",
        endpoints={"m": "gateway", "other": "elsewhere"},
    )

    logger.on_chat_model_start({}, [], run_id="r1", metadata={"ls_model_name": "other"})
    logger.on_llm_error(TimeoutError("slow"), run_id="r1")

    (event,) = sink.named("model_error")
    assert (event["model"], event["endpoint"]) == ("other", "elsewhere")


def test_a_call_on_a_model_the_catalogue_does_not_hold_keeps_the_turns_stamp():
    """Stamped with any name a call carried, one the catalogue does not hold was logged on
    that model at endpoint "", half a stamp no route or price can be read from."""
    sink = RecordedEvents()
    logger = RunLogger(
        sink, model="m", endpoint="gateway", session_id="s", turn_id="t1",
        endpoints={"m": "gateway"},
    )

    logger.on_chat_model_start({}, [], run_id="r1", metadata={"ls_model_name": "not-in-catalogue"})
    logger.on_llm_error(TimeoutError("slow"), run_id="r1")

    (event,) = sink.named("model_error")
    assert (event["model"], event["endpoint"]) == ("m", "gateway")


def test_a_sink_that_is_empty_is_still_the_one_used(cfg):
    """`None` means the default, and nothing else does: a sink that keeps its events in
    a list of its own is falsy until the first one arrives, and was replaced by the
    logger before it got one.
    """

    class Kept(list, RunEvents):
        def record(self, event):
            self.append(event)

    sink = Kept()
    start(cfg, "s")
    kf = Kingfisher(
        cfg,
        graph=StubAgent("42"),
        sessions=LocalSessions(),
        run_events=sink,
    )

    kf.run(Request("t", session_id="s"))

    assert [e["event"] for e in sink] == ["run_start", "run_end"]


def test_with_nothing_wired_the_events_go_to_the_run_logger(cfg, caplog):
    """The default a deployment gets, and where it finds them: one JSON line per event
    on `kingfisher.run`, with the mapping itself on the record for a handler that
    ships structured logs.
    """
    start(cfg, "s")
    kf = Kingfisher(
        cfg, graph=StubAgent("42"), sessions=LocalSessions()
    )

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

    from kingfisher.plugins.events import JsonlRunEvents

    sink = JsonlRunEvents(tmp_path / "logs" / "turns.jsonl")
    _logger(sink).run_start("Résume le rapport")
    _logger(sink).run_end(ok=True, answer_chars=3)

    written = (tmp_path / "logs" / "turns.jsonl").read_text(encoding="utf-8")
    events = [json.loads(line) for line in written.splitlines()]
    assert [e["event"] for e in events] == ["run_start", "run_end"]
    assert "Résume" in written


PING = '''
def ping(x: str) -> str:
    """Answer pong."""
    return f"pong:{x}"


TOOLS = [ping]
'''

COMPILED = """
def build(model, tools):
    from langchain.agents import create_agent

    return create_agent(model, tools)


SUBAGENTS = [{"name": "surveyor", "description": "Surveys.", "build": build, "tools": ["ping"]}]
"""


def _called(name: str, args: dict, at: str) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": at}])


ROUTES = {
    "eval": [
        _called("eval", {"code": "(async () => await tools.ping({x: 'a'}))()"}, "e1"),
        AIMessage(content="done"),
    ],
    "compiled": [
        _called("task", {"description": "go", "subagent_type": "surveyor"}, "t1"),
        _called("ping", {"x": "a"}, "p1"),
        AIMessage(content="surveyed"),
        AIMessage(content="done"),
    ],
}


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
@pytest.mark.parametrize("route", sorted(ROUTES))
def test_a_call_from_code_or_a_compiled_subagent_is_logged_once(scripted, route, awaited):
    """Each such call was logged twice: the guard those tools carry ran as a tool of its
    own around the tool, and both reported starting and ending.
    """
    cfg = replace(scripted, interpreter_enabled=True)
    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "ping.py").write_text(PING, encoding="utf-8")
    subagents_dir(cfg).mkdir(parents=True, exist_ok=True)
    (subagents_dir(cfg) / "surveyor.py").write_text(COMPILED, encoding="utf-8")
    an_agent(cfg, subagents="[surveyor]", builtin_tools="[eval, task]", tools="[ping]")
    start(cfg, "s")
    Scripted.script.extend(ROUTES[route])
    sink = RecordedEvents()
    kf = Kingfisher(cfg, sessions=LocalSessions(), run_events=sink)
    request = Request("go", agent="only", session_id="s")

    result = asyncio.run(kf.arun(request)) if awaited else kf.run(request)

    assert result.answer == "done"
    started = [e["tool"] for e in sink.named("tool_start")]
    assert started.count("ping") == 1, started
    assert len(sink.named("tool_end")) == len(started), "a call ended more often than it began"

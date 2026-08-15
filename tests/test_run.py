from __future__ import annotations

import json
import os

import pytest
from langchain_core.messages import AIMessage

from kingfisher.run import RunResult, normalize_answer, run
from tests.conftest import StubCheckpointer


class StubAgent:
    """Stands in for the compiled graph so the orchestration is testable.

    Emits the same (mode, chunk) shape LangGraph does for
    `stream_mode=["updates", "values"]`.
    """

    def __init__(self, answer: str, *, updates: list | None = None) -> None:
        self.answer = answer
        self.updates = updates or []
        self.state: dict | None = None
        self.config: dict | None = None

    def stream(self, state, config, stream_mode=None):  # noqa: ANN001, ARG002
        self.state, self.config = state, config
        for update in self.updates:
            yield ("updates", update)
        yield ("values", {"messages": [AIMessage(content=self.answer)]})


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<think>reasoning</think>\n\n42", "42"),
        ("<think>a</think>x<think>b</think>y", "xy"),
        ("42", "42"),
        ("", ""),
        ("<THINK>upper</THINK> 7", "7"),
    ],
)
def test_normalize_strips_inlined_reasoning(raw, expected):
    """Applied on both API styles, not just the one that currently leaks."""
    assert normalize_answer(raw) == expected


def test_run_creates_the_session_triple(cfg):
    """One identifier reaches the thread, the run directory and the log."""
    agent = StubAgent("<think>done</think>\n\n42")
    result = run(
        "count things",
        cfg=cfg,
        session_id="sess123",
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    assert isinstance(result, RunResult)
    assert result.answer == "42"
    assert result.run_dir == cfg.workspace / "runs" / "sess123" / result.turn_id
    assert result.run_dir.is_dir()
    assert result.log_path.exists()
    assert agent.config["configurable"]["thread_id"] == "sess123"


def test_run_tells_the_agent_its_run_directory_in_the_task(cfg):
    """Run-scoped, so it goes in the message -- never the cached system prompt."""
    agent = StubAgent("ok")
    run("do a thing", cfg=cfg, session_id="abc", agent=agent, checkpointer=StubCheckpointer())

    message = agent.state["messages"][0]["content"]
    assert "/runs/abc" in message
    assert "do a thing" in message
    assert str(cfg.workspace) not in message  # virtual path only


def test_run_logs_usage_shaped_records(cfg):
    agent = StubAgent("ok")
    result = run("t", cfg=cfg, session_id="logged", agent=agent, checkpointer=StubCheckpointer())

    records = [json.loads(line) for line in result.log_path.read_text().splitlines()]
    events = [r["event"] for r in records]
    assert "run_start" in events
    assert "run_end" in events
    # Model and API style ride along so a zero cache_read can be told apart
    # from a gateway that simply does not cache.
    assert all(r["model"] == cfg.model and r["api_style"] == cfg.api_style for r in records)


def test_run_sweeps_old_sessions_before_creating_the_new_one(cfg):
    """cfg.keep_runs is 2 in the fixture."""
    for i, name in enumerate(["s1", "s2", "s3"]):
        d = cfg.workspace / "runs" / name
        d.mkdir(parents=True)
        os.utime(d, (1_000 + i * 100, 1_000 + i * 100))

    ckpt = StubCheckpointer()
    result = run("t", cfg=cfg, session_id="s4", agent=StubAgent("ok"), checkpointer=ckpt)

    assert "s1" in result.swept
    assert not (cfg.workspace / "runs" / "s1").exists()
    assert ckpt.deleted == ["s1"]
    # The new run directory is created after the sweep, so it is never a
    # candidate for its own deletion.
    assert (cfg.workspace / "runs" / "s4").is_dir()



def test_a_second_turn_does_not_overwrite_the_first(cfg):
    """The defect this tier exists to fix: two turns in one session shared a
    directory, so turn two clobbered turn one's report and result."""
    ck = StubCheckpointer()
    first = run("turn one", cfg=cfg, session_id="sess", agent=StubAgent("a"), checkpointer=ck)
    (first.run_dir / "report.md").write_text("FROM TURN ONE")

    second = run("turn two", cfg=cfg, session_id="sess", agent=StubAgent("b"), checkpointer=ck)
    (second.run_dir / "report.md").write_text("FROM TURN TWO")

    assert first.turn_id == "t001"
    assert second.turn_id == "t002"
    assert first.run_dir != second.run_dir
    assert (first.run_dir / "report.md").read_text() == "FROM TURN ONE"
    # Both turns live under one session, so expiring the conversation takes
    # both with it and no lookup is needed.
    assert first.run_dir.parent == second.run_dir.parent


def test_request_inputs_land_in_the_turn_not_in_data(cfg, tmp_path):
    """Files supplied with a request are not project data: they arrive fresh
    each round and leave with the turn."""
    supplied = tmp_path / "upload.csv"
    supplied.write_text("a,b\n1,2\n")

    agent = StubAgent("ok")
    result = run(
        "summarise it",
        cfg=cfg,
        session_id="s",
        inputs=[supplied],
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    assert (result.run_dir / "input" / "upload.csv").read_text() == "a,b\n1,2\n"
    assert not (cfg.workspace / "data" / "upload.csv").exists()
    # The agent is told where they are, by virtual path, in the task message.
    message = agent.state["messages"][0]["content"]
    assert f"/runs/s/{result.turn_id}/input" in message


def test_no_inputs_means_no_input_directory_and_no_mention(cfg):
    agent = StubAgent("ok")
    result = run("just answer", cfg=cfg, session_id="s", agent=agent, checkpointer=StubCheckpointer())

    assert not (result.run_dir / "input").exists()
    assert "input" not in agent.state["messages"][0]["content"]

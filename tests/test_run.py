from __future__ import annotations

import json
import os

import pytest
from langchain_core.messages import AIMessage

from kingfisher.app.run import Request, RunResult, normalize_answer, run
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

    def stream(self, state, config, stream_mode=None):
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
        Request("count things", session_id="sess123"),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    assert isinstance(result, RunResult)
    assert result.answer == "42"
    assert result.run_dir == cfg.workspace / "sessions" / "sess123" / "runs" / result.turn_id
    assert result.run_dir.is_dir()
    assert result.log_path.exists()
    assert agent.config["configurable"]["thread_id"] == "sess123"


def test_run_tells_the_agent_its_run_directory_in_the_task(cfg):
    """Run-scoped, so it goes in the message -- never the cached system prompt."""
    agent = StubAgent("ok")
    run(
        Request("do a thing", session_id="abc"),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    message = agent.state["messages"][0]["content"]
    assert "/runs/t001" in message
    assert "do a thing" in message
    assert str(cfg.workspace) not in message  # virtual path only


def test_run_logs_usage_shaped_records(cfg):
    agent = StubAgent("ok")
    result = run(
        Request("t", session_id="logged"),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

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
        d = cfg.workspace / "sessions" / name
        d.mkdir(parents=True)
        os.utime(d, (1_000 + i * 100, 1_000 + i * 100))

    ckpt = StubCheckpointer()
    result = run(Request("t", session_id="s4"), cfg=cfg, agent=StubAgent("ok"), checkpointer=ckpt)

    assert "s1" in result.swept
    assert not (cfg.workspace / "sessions" / "s1").exists()
    assert ckpt.deleted == ["s1"]
    # This session is exempted from the sweep by name, so a run can never
    # delete itself -- even though its directory now exists before the sweep
    # runs, because the agent's backend has to be rooted at it first.
    assert (cfg.workspace / "sessions" / "s4").is_dir()



def test_a_second_turn_does_not_overwrite_the_first(cfg):
    """The defect this tier exists to fix: two turns in one session shared a
    directory, so turn two clobbered turn one's report and result."""
    ck = StubCheckpointer()
    first = run(
        Request("turn one", session_id="sess"), cfg=cfg, agent=StubAgent("a"), checkpointer=ck
    )
    (first.run_dir / "report.md").write_text("FROM TURN ONE")

    second = run(
        Request("turn two", session_id="sess"), cfg=cfg, agent=StubAgent("b"), checkpointer=ck
    )
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
        Request("summarise it", session_id="s", inputs=(supplied,)),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    assert (result.run_dir / "input" / "upload.csv").read_text() == "a,b\n1,2\n"
    assert not (cfg.workspace / "sessions" / "s" / "data" / "upload.csv").exists()
    # The agent is told where they are, by virtual path, in the task message.
    message = agent.state["messages"][0]["content"]
    assert f"/runs/{result.turn_id}/input" in message


def test_no_inputs_means_no_input_directory_and_no_mention(cfg):
    agent = StubAgent("ok")
    result = run(
        Request("just answer", session_id="s"),
        cfg=cfg,
        agent=agent,
        checkpointer=StubCheckpointer(),
    )

    assert not (result.run_dir / "input").exists()
    assert "input" not in agent.state["messages"][0]["content"]


def test_a_bare_task_string_still_works(cfg):
    """`run("do a thing")` must stay readable; Request is for when you need it."""
    result = run("just this", cfg=cfg, agent=StubAgent("ok"), checkpointer=StubCheckpointer())
    assert result.answer == "ok"


def test_request_rejects_an_empty_task():
    """Validate at the edge: an empty query is a client error, not a run."""
    import pytest as _pytest

    for bad in ("", "   ", "\n"):
        with _pytest.raises(ValueError, match="must not be empty"):
            Request(bad)


def test_request_normalises_inputs_to_paths():
    from pathlib import Path as _Path

    # Off-contract on purpose: strings and a list, both normalised away.
    request = Request("t", inputs=["/tmp/a.csv", _Path("/tmp/b.csv")])  # ty: ignore[invalid-argument-type]
    assert all(isinstance(p, _Path) for p in request.inputs)
    assert isinstance(request.inputs, tuple)


def test_coerce_is_idempotent():
    original = Request("t", session_id="s")
    assert Request.coerce(original) is original
    assert Request.coerce("t").task == "t"


def test_a_rejected_request_sweeps_nothing(cfg, monkeypatch):
    """A typo in a capability name must not be destructive. This used to raise
    only after the sweep had already removed old sessions."""
    from kingfisher.adapters.agent import CapabilityError
    from kingfisher.domain.capabilities import Capabilities

    workspace = cfg.workspace
    old = workspace / "runs" / "ancient"
    old.mkdir(parents=True)
    (old / "t001").mkdir()

    def must_not_run(*_args, **_kwargs):
        msg = "sweep ran despite the request being rejected"
        raise AssertionError(msg)

    monkeypatch.setattr("kingfisher.domain.retention.apply", must_not_run)

    with pytest.raises(CapabilityError):
        run(
            Request("t", capabilities=Capabilities(subagents=("ghost",))),
            cfg=cfg,
            checkpointer=StubCheckpointer(),
        )

    assert old.is_dir()  # nothing was removed


def test_the_framework_never_asks_for_files_of_its_own(cfg):
    """Wanting a written report is one kind of task among many. Nothing in the
    plumbing may privilege a convention -- not the system prompt, not the turn
    envelope. If the caller wants files, it says so in the task, and that is
    the only route by which those names reach the model.

    Both failure modes of the old design showed up live in one afternoon: a
    greeting that deliberated over two files nobody wanted, and, once the
    demand was softened to a suggestion, a real analysis that recorded nothing.
    """
    quiet = StubAgent("ok")
    result = run(Request("say hello"), cfg=cfg, agent=quiet, checkpointer=StubCheckpointer())
    sent = quiet.state["messages"][0]["content"]

    assert result.run_dir.name in sent  # the turn directory is a fact, and reaches it
    assert "report.md" not in sent
    assert "result.json" not in sent

    asked = StubAgent("ok")
    run(
        Request("Analyse it and write findings.csv"),
        cfg=cfg,
        agent=asked,
        checkpointer=StubCheckpointer(),
    )
    # Whatever the caller names, verbatim and unembellished.
    assert "findings.csv" in asked.state["messages"][0]["content"]

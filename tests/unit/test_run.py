from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.run import Request, RunResult, normalize_answer, run
from kingfisher.application.service import Kingfisher
from kingfisher.domain.result import END_TURN, STOP_REASONS
from tests.conftest import StubCheckpointer, start


class StubAgent:
    """Stands in for the compiled graph so the orchestration is testable."""

    def __init__(
        self,
        answer: str,
        *,
        updates: list | None = None,
        tokens: list | None = None,
        delegate: tuple | None = None,
    ) -> None:
        self.answer = answer
        self.updates = updates or []
        self.tokens = tokens or []
        self.delegate = delegate or ()
        self.state: dict | None = None
        self.config: dict | None = None

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        self.state, self.config = state, config
        for chunk in self.tokens:
            yield (self.delegate, "messages", chunk)
        for update in self.updates:
            yield (self.delegate, "updates", update)
        yield ((), "values", {"messages": [AIMessage(content=self.answer)]})

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        """The async twin, because a compiled graph has one and `astream` drives it.

        A stub offering only `stream` would make every async test fail for the
        stub's reason rather than the code's -- and one offering only this would
        hide a sync path that had stopped working.
        """
        for chunk in self.stream(state, config, stream_mode, subgraphs):
            yield chunk

    def get_state(self, config):
        """What a real graph holds when the turn ends."""
        del config
        sent = list((self.state or {}).get("messages", []))
        return SimpleNamespace(values={"messages": [*sent, AIMessage(content=self.answer)]})


def test_normalize_strips_inlined_reasoning():
    """Applied on both API styles, not just the one that currently leaks."""
    for raw, expected in [
            ("<think>reasoning</think>\n\n42", "42"),
            ("<think>a</think>x<think>b</think>y", "xy"),
            ("42", "42"),
            ("", ""),
            ("<THINK>upper</THINK> 7", "7"),
        ]:
        assert normalize_answer(raw) == expected


def test_run_creates_the_session_triple(cfg):
    """One identifier reaches the thread, the session directory and the log."""
    start(cfg, "sess123")
    agent = StubAgent("<think>done</think>\n\n42")
    result = run(
        Request("count things", session_id="sess123"),
        cfg=cfg,
        graph=agent,
        checkpointer=StubCheckpointer(),
    )

    assert isinstance(result, RunResult)
    assert result.answer == "42"
    assert result.session_dir == cfg.workspace / "sessions" / "sess123"
    assert result.session_dir.is_dir()
    assert result.log_path.exists()
    assert agent.config["configurable"]["thread_id"] == "sess123"


def test_run_tells_the_agent_where_to_work_in_the_task(cfg):
    """Said per turn rather than in the cached system prompt, because saying it there
    was measured as not enough: the agent passed the virtual path to `execute` 4 times
    in 10.
    """
    start(cfg, "abc")
    agent = StubAgent("ok")
    run(
        Request("do a thing", session_id="abc"),
        cfg=cfg,
        graph=agent,
        checkpointer=StubCheckpointer(),
    )

    message = agent.state["messages"][0]["content"]
    assert "/scratchpad" in message
    assert "do a thing" in message
    assert str(cfg.workspace) not in message  # virtual path only


def test_run_logs_usage_shaped_records(cfg):
    start(cfg, "logged")
    agent = StubAgent("ok")
    result = run(
        Request("t", session_id="logged"),
        cfg=cfg,
        graph=agent,
        checkpointer=StubCheckpointer(),
    )

    records = [json.loads(line) for line in result.log_path.read_text().splitlines()]
    events = [r["event"] for r in records]
    assert "run_start" in events
    assert "run_end" in events
    # Model and API style ride along so a zero cache_read can be told apart
    # from a gateway that simply does not cache.
    assert all(
        r["model"] == cfg.models.default and r["endpoint"] == cfg.models.resolve()[0].endpoint
        for r in records
    )


def test_a_turn_disposes_of_nothing(cfg):
    """Retention used to run here and keep the newest N sessions, counting every
    caller's together -- so a busy caller evicted a quiet one on a turn that had
    nothing to do with it.
    """
    for name in ("s1", "s2", "s3"):
        start(cfg, name)
    start(cfg, "s4")

    run(Request("t", session_id="s4"), cfg=cfg, graph=StubAgent("ok"),
        checkpointer=StubCheckpointer())

    for name in ("s1", "s2", "s3", "s4"):
        assert (cfg.workspace / "sessions" / name).is_dir(), name


def test_a_bare_task_string_still_works(cfg):
    """`run("do a thing")` must stay readable; Request is for when you need it."""
    result = run("just this", cfg=cfg, graph=StubAgent("ok"), checkpointer=StubCheckpointer())
    assert result.answer == "ok"


def test_request_rejects_an_empty_task():
    """Validate at the edge: an empty query is a client error, not a run."""
    import pytest as _pytest

    for bad in ("", "   ", "\n"):
        with _pytest.raises(ValueError, match="must not be empty"):
            Request(bad)


def test_coerce_is_idempotent():
    original = Request("t", session_id="s")
    assert Request.coerce(original) is original
    assert Request.coerce("t").task == "t"


def test_a_rejected_request_sweeps_nothing(cfg, monkeypatch):
    """A typo in a capability name must not be destructive."""
    from kingfisher.domain.capabilities import Capabilities, CapabilityError

    workspace = cfg.workspace
    old = workspace / "scratch" / "ancient"
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
    """Wanting a written report is one kind of task among many."""
    quiet = StubAgent("ok")
    run(Request("say hello"), cfg=cfg, graph=quiet, checkpointer=StubCheckpointer())
    sent = quiet.state["messages"][0]["content"]

    assert "/scratchpad" in sent  # where to work is a fact, and reaches it
    assert "report.md" not in sent
    assert "result.json" not in sent

    asked = StubAgent("ok")
    run(
        Request("Analyse it and write findings.csv"),
        cfg=cfg,
        graph=asked,
        checkpointer=StubCheckpointer(),
    )
    # Whatever the caller names, verbatim and unembellished.
    assert "findings.csv" in asked.state["messages"][0]["content"]


def test_supplied_data_is_still_there_on_the_next_turn(cfg):
    """The property `--input` deliberately lacks, and the whole reason this exists: a
    turn's `input/` leaves with the turn, `/data` does not.
    """
    source = cfg.workspace / "sales.csv"
    source.write_text("a,b\n1,2\n")

    start(cfg, "keeps")
    ck = StubCheckpointer()
    first = run(
        Request("look at it", session_id="keeps", data=(source,)),
        cfg=cfg,
        graph=StubAgent("ok"),
        checkpointer=ck,
    )
    second = run(
        Request("and again", session_id="keeps"),
        cfg=cfg,
        graph=StubAgent("ok"),
        checkpointer=ck,
    )

    session = first.session_dir
    assert (session / "data" / "sales.csv").read_text() == "a,b\n1,2\n"
    assert second.session_dir == session  # same session, second turn
    assert not (second.session_dir / "input").exists()  # nothing was re-supplied


def test_the_agent_is_told_what_arrived_in_data(cfg):
    """It may already have looked at /data this session."""
    source = cfg.workspace / "fresh.csv"
    source.write_text("x")

    start(cfg, "told")
    agent = StubAgent("ok")
    run(
        Request("go", session_id="told", data=(source,)),
        cfg=cfg,
        graph=agent,
        checkpointer=StubCheckpointer(),
    )

    assert "New files in /data: fresh.csv." in agent.state["messages"][0]["content"]


# -- what a caller can be told, and what stays on the host ----------------
#
# `RunResult` serves two audiences at once. A local caller is on the host and
# wants `run_dir`; a remote one cannot read it and should not be told the
# server's layout. Both live in one record, and which is which is the part that
# has to be legible -- the alternative is every API author deciding it again.


def test_everything_but_the_host_paths_is_json(cfg):
    """The half a server sends."""
    import dataclasses

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    start(cfg, "s")
    result = service.run(Request("go", session_id="s"))

    sendable = {
        k: v for k, v in dataclasses.asdict(result).items()
        if k not in ("session_dir", "log_path")
    }

    # `artifacts` is the half that locates a file for a caller elsewhere: it is
    # relative to the session root, so it needs no host path to be useful.
    assert json.loads(json.dumps(sendable))["turn_id"] == result.turn_id


def test_the_host_paths_refuse_to_serialise(cfg):
    """Deliberate, and the reason they are named in the docstring."""
    import dataclasses

    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    start(cfg, "s")
    result = service.run(Request("go", session_id="s"))

    with pytest.raises(TypeError, match="not JSON serializable"):
        json.dumps(dataclasses.asdict(result))


# -- what counts as having finished ---------------------------------------


@pytest.mark.parametrize("reason", STOP_REASONS)
def test_completed_is_true_for_the_one_reason_that_means_the_agent_finished(reason):
    """Parametrized over the tuple rather than over a list written out here, so a
    fourth stop reason arrives in this test on the day it is added rather than the day
    somebody remembers to come back for it.
    """
    result = RunResult(session_id="s", turn_id="t001", answer="", stop_reason=reason)

    assert result.completed == (reason == END_TURN)


def test_a_result_that_says_nothing_about_stopping_has_finished():
    """The default, which every caller building a result by hand relies on."""
    assert RunResult(session_id="s", turn_id="t001", answer="").completed


class RecordingAgent(StubAgent):
    """Keeps what the loop asked the graph for."""

    def __init__(self, answer: str) -> None:
        super().__init__(answer)
        self.asked: list[bool] = []

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        self.asked.append(subgraphs)
        yield from super().stream(state, config, stream_mode)


def test_the_loop_asks_for_what_a_delegate_does(cfg):
    """A delegate runs in a subgraph, and a stream that does not ask for subgraph
    events never sees one. The symptom is not an error: the turn answers, and
    everything the delegate did is missing from the run.
    """
    agent = RecordingAgent("ok")
    list(Kingfisher(cfg, graph=agent, threads=StubCheckpointer()).stream(Request("go")))

    assert agent.asked == [True]

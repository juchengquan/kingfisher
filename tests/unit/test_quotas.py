"""What one session may consume."""

from __future__ import annotations

from dataclasses import replace
from time import monotonic, sleep

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from kingfisher import Kingfisher
from kingfisher.core.request import Request
from kingfisher.core.session.model import QuotaExceededError
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.folders import session_bytes
from tests.conftest import EVERY_TOOL, FakeGraph, an_agent, start
from tests.unit.scripted import Scripted
from tests.unit.test_run import StubAgent


class SlowAgent(FakeGraph):
    """A turn that keeps taking steps, as a real one can for hours."""

    def __init__(self, steps: int = 50) -> None:
        self.steps = steps
        self.taken = 0

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        for _ in range(self.steps):
            self.taken += 1
            yield ((), "updates", {"agent": {"messages": [AIMessage(content="working")]}})
        yield ((), "values", {"messages": [AIMessage(content="done")]})


class Holding(FakeGraph):
    """A turn whose supersteps have work to do before they have a chunk to yield, as
    one running a shell command does."""

    def __init__(self, held: float) -> None:
        self.held = held
        self.ran: list[float] = []

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        for said in ("working", "done"):
            began = monotonic()
            # No chunk arrives while the command runs, so no deadline is read during it.
            sleep(self.held)
            self.ran.append(monotonic() - began)
            yield ((), "updates", {"agent": {"messages": [AIMessage(content=said)]}})
        yield ((), "values", {"messages": [AIMessage(content="done")]})


# -- the wall-clock bound -------------------------------------------------


def test_a_turn_that_runs_past_its_bound_stops(cfg):
    """`recursion_limit` bounds steps and `timeout_s` bounds one call; nothing bounded
    their product, so a turn could hold a process for hours.
    """
    agent = SlowAgent(steps=50)
    kf = Kingfisher(
        replace(cfg, turn_timeout_s=0),
        graph=agent,
        sessions=LocalSessions(),
    )

    result = kf.run(Request("go"))

    # The reason, not merely that it stopped: the two bounds set different ones,
    # and asserting the flag could not tell which had fired.
    assert result.stop_reason == "max_duration"
    assert agent.taken < 50, "the turn ran to completion despite the bound"


def test_a_turn_out_of_time_stops_only_once_its_superstep_has_ended(cfg):
    """Why `claim_stale_after` adds a superstep's bound to the turn deadline rather than
    treating the deadline as the moment a turn ends: a turn already out of time runs on
    to the end of the superstep it is inside, and `execution_timeout_s` is what bounds
    one holding a shell command.

    Held by `test_a_claim_outlives_whatever_held_the_superstep_the_deadline_fell_in`
    from the claim's side; this is the half that says why that arithmetic is the right
    arithmetic.
    """
    held = 0.05
    graph = Holding(held)

    result = Kingfisher(
        replace(cfg, turn_timeout_s=0), graph=graph, sessions=LocalSessions()
    ).run(Request("go"))

    assert result.stop_reason == "max_duration"
    # Out of time before the graph was entered, and it still stopped at a chunk rather
    # than inside the superstep that produced it: one superstep run, not none, and that
    # one ran its full length.
    assert len(graph.ran) == 1
    assert graph.ran[0] >= held


def test_being_cut_short_keeps_the_work(cfg):
    """The artifacts are already on disk and the manifest lists them, so discarding the
    answer would hide work rather than undo it.
    """
    kf = Kingfisher(
        replace(cfg, turn_timeout_s=0),
        graph=SlowAgent(),
        sessions=LocalSessions(),
    )

    result = kf.run(Request("go"))

    assert (cfg.workspace / "sessions" / result.session_id).is_dir()
    assert "memory/AGENTS.md" in result.artifacts


def test_the_caller_is_told_rather_than_left_to_guess(cfg):
    """An answer that quietly overstates what was checked is worse than one that admits
    a gap, and the caller is owed the same of a quota.
    """
    kf = Kingfisher(
        replace(cfg, turn_timeout_s=0),
        graph=SlowAgent(),
        sessions=LocalSessions(),
    )

    kinds = [e.kind for e in kf.stream(Request("go"))]

    assert "cut_short" in kinds
    assert kinds[-1] == "finished"


def test_an_ordinary_turn_is_untouched(cfg):
    """An hour is far past any real turn, so this only fires on the pathological case."""
    result = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    ).run(Request("go"))

    assert result.stop_reason == "end_turn"
    assert result.answer == "ok"


# -- the step bound -------------------------------------------------------


class RunawayAgent(FakeGraph):
    """A graph that never stops, as langgraph reports it: by raising."""

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield ((), "updates", {"agent": {"messages": [AIMessage(content="working")]}})
        msg = "Recursion limit of 150 reached without hitting a stop condition."
        raise GraphRecursionError(msg)


def test_a_turn_that_runs_out_of_steps_is_cut_short_not_crashed(cfg):
    """The other bound on a turn, and it behaved nothing like the first."""
    kf = Kingfisher(
        cfg, graph=RunawayAgent(), sessions=LocalSessions()
    )

    result = kf.run(Request("go"))

    # `max_steps`, not `max_duration` -- the distinction the boolean could not
    # carry, and the reason a reader was sent to the wrong setting.
    assert result.stop_reason == "max_steps"
    kept = cfg.workspace / "sessions" / result.session_id
    assert kept.is_dir(), "the turn's work went with the error"


def test_a_turn_cut_short_for_steps_is_logged_as_having_ended(cfg):
    """`ok` records that the turn ended in a way the caller was told about, not that
    every step it wanted happened.

    A comment says so where the flag is set, and nothing read it back. Logged
    false, a bounded turn reads in the log like a crash, and the two want
    different things done about them.
    """
    from tests.conftest import RecordedEvents

    sink = RecordedEvents()
    Kingfisher(
        cfg,
        graph=RunawayAgent(),
        sessions=LocalSessions(),
        run_events=sink,
    ).run(Request("go"))

    ends = sink.named("run_end")
    assert ends and ends[-1]["ok"] is True


def test_a_turn_out_of_steps_after_a_tool_answers_with_what_the_model_said(scripted):
    """A turn that ran out of steps just after a tool ran reported the tool's output, a
    folder listing, as its answer."""
    an_agent(scripted, **EVERY_TOOL)
    Scripted.script.extend(
        AIMessage(
            content="Listing the folders first.",
            tool_calls=[{"name": "ls", "args": {"path": "/"}, "id": f"l{n}"}],
        )
        for n in range(20)
    )

    result = Kingfisher(replace(scripted, recursion_limit=4), sessions=LocalSessions()).run(
        Request("list the folders", agent="only")
    )

    assert result.stop_reason == "max_steps"
    assert result.answer == "Listing the folders first."


def test_running_out_of_steps_says_which_bound_and_how_to_raise_it(cfg):
    """A cut-short that does not say which of the two bounds it hit sends the reader to
    the wrong setting.
    """
    kf = Kingfisher(
        cfg, graph=RunawayAgent(), sessions=LocalSessions()
    )

    events = list(kf.stream(Request("go")))
    stopped = [e for e in events if e.kind == "cut_short"]

    assert stopped, "no cut_short event"
    assert "KINGFISHER_RECURSION_LIMIT" in stopped[0].text
    assert str(cfg.recursion_limit) in stopped[0].text
    assert events[-1].kind == "finished"


# -- the disk bound -------------------------------------------------------


def test_a_session_over_its_disk_bound_cannot_start_another_turn(cfg):
    """Checked before a turn, never during: `execute` writes without any file tool
    seeing it, so there is nothing to intercept mid-turn.
    """
    kf = Kingfisher(
        replace(cfg, session_max_bytes=10),
        graph=StubAgent("ok"),
        sessions=LocalSessions(),
    )
    session_id = start(cfg, "s")
    (cfg.workspace / "sessions" / session_id / "outputs" / "big.bin").write_bytes(b"x" * 100)

    with pytest.raises(QuotaExceededError, match="over the 10 allowed"):
        kf.run(Request("go", session_id=session_id))


def test_the_disk_bound_is_off_unless_a_deployment_sets_one(cfg):
    """No honest default exists: workspaces vary by orders of magnitude, and refusing a
    turn over a number nobody chose is worse than not bounding it.
    """
    assert cfg.session_max_bytes is None

    kf = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions()
    )
    session_id = start(cfg, "s")
    (cfg.workspace / "sessions" / session_id / "outputs" / "big.bin").write_bytes(b"x" * 10_000)

    assert kf.run(Request("go", session_id=session_id)).answer == "ok"


def test_session_bytes_counts_everything_the_session_holds(cfg, session_dir):
    """Run scratch counts too: the question is what the session costs the host, not what
    is worth keeping.
    """
    (session_dir / "outputs" / "kept.bin").write_bytes(b"x" * 100)
    (session_dir / "scratchpad").mkdir(exist_ok=True)
    (session_dir / "scratchpad" / "scratch.bin").write_bytes(b"y" * 50)

    assert session_bytes(session_dir) >= 150


def test_a_session_over_budget_is_refused_before_its_inputs_are_placed(cfg, tmp_path):
    """The two features meet here, and the order is the point."""
    supplied = tmp_path / "report.pdf"
    supplied.write_bytes(b"z" * 500)

    kf = Kingfisher(
        replace(cfg, session_max_bytes=10),
        graph=StubAgent("ok"),
        sessions=LocalSessions(),
    )
    session_id = start(cfg, "s")
    session = cfg.workspace / "sessions" / session_id
    (session / "outputs" / "already.bin").write_bytes(b"x" * 100)

    with pytest.raises(QuotaExceededError):
        kf.run(Request("go", session_id=session_id, inputs=(supplied,)))

    assert not (session / "inputs" / "report.pdf").exists(), "placed despite the refusal"

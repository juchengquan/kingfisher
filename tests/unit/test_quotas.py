"""What one session may consume.

Session-scoped, because that is what kingfisher can see: it is tenant-blind by
design, so bounding a *caller* belongs to whatever knows who is calling. These
protect the process from one runaway session, not one caller from another.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage
from langgraph.errors import GraphRecursionError

from kingfisher import Kingfisher
from kingfisher.domain.request import Request
from kingfisher.domain.session import QuotaExceededError
from kingfisher.infrastructure.workspace_fs import session_bytes
from tests.conftest import StubCheckpointer
from tests.unit.test_run import StubAgent


class SlowAgent:
    """A turn that keeps taking steps, as a real one can for hours."""

    def __init__(self, steps: int = 50) -> None:
        self.steps = steps
        self.taken = 0

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        for _ in range(self.steps):
            self.taken += 1
            yield ((), "updates", {"agent": {"messages": [AIMessage(content="working")]}})
        yield ((), "values", {"messages": [AIMessage(content="done")]})


# -- the wall-clock bound -------------------------------------------------


def test_a_turn_that_runs_past_its_bound_stops(cfg):
    """`recursion_limit` bounds steps and `timeout_s` bounds one call; nothing
    bounded their product, so a turn could hold a process for hours."""
    agent = SlowAgent(steps=50)
    kf = Kingfisher(replace(cfg, turn_timeout_s=0), graph=agent, threads=StubCheckpointer())

    result = kf.run(Request("go"))

    # The reason, not merely that it stopped: the two bounds set different ones,
    # and asserting the flag could not tell which had fired.
    assert result.stop_reason == "max_duration"
    assert agent.taken < 50, "the turn ran to completion despite the bound"


def test_being_cut_short_keeps_the_work(cfg):
    """The artifacts are already on disk and the manifest lists them, so
    discarding the answer would hide work rather than undo it."""
    kf = Kingfisher(replace(cfg, turn_timeout_s=0), graph=SlowAgent(), threads=StubCheckpointer())

    result = kf.run(Request("go"))

    assert result.run_dir.is_dir()
    assert "memory/AGENTS.md" in result.artifacts


def test_the_caller_is_told_rather_than_left_to_guess(cfg):
    """An answer that quietly overstates what was checked is worse than one
    that admits a gap -- which is what system.md already tells the agent."""
    kf = Kingfisher(replace(cfg, turn_timeout_s=0), graph=SlowAgent(), threads=StubCheckpointer())

    kinds = [e.kind for e in kf.stream(Request("go"))]

    assert "cut_short" in kinds
    assert kinds[-1] == "finished"


def test_an_ordinary_turn_is_untouched(cfg):
    """An hour is far past any real turn, so this only fires on the
    pathological case."""
    result = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer()).run(Request("go"))

    assert result.stop_reason == "end_turn"
    assert result.answer == "ok"


# -- the step bound -------------------------------------------------------


class RunawayAgent:
    """A graph that never stops, as langgraph reports it: by raising.

    `recursion_limit` is enforced inside `Pregel.stream`, so a turn that hits it
    does not return -- it raises out of the generator the service is draining.
    """

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield ((), "updates", {"agent": {"messages": [AIMessage(content="working")]}})
        msg = "Recursion limit of 150 reached without hitting a stop condition."
        raise GraphRecursionError(msg)


def test_a_turn_that_runs_out_of_steps_is_cut_short_not_crashed(cfg):
    """The other bound on a turn, and it behaved nothing like the first.

    `turn_timeout_s` ends a turn as a `RunResult` whose `stop_reason` is
    `max_duration`;
    `recursion_limit` ended it as a `GraphRecursionError` out of `stream`, so
    the caller got a langgraph traceback instead of the work. Observed on a run
    that had already written its report and validated it -- the file was on
    disk, and the driver printed a stack trace and no path to it.

    Two bounds on one turn should read the same way to whoever called it.
    """
    kf = Kingfisher(cfg, graph=RunawayAgent(), threads=StubCheckpointer())

    result = kf.run(Request("go"))

    # `max_steps`, not `max_duration` -- the distinction the boolean could not
    # carry, and the reason a reader was sent to the wrong setting.
    assert result.stop_reason == "max_steps"
    assert result.run_dir.is_dir(), "the turn's work went with the error"


def test_running_out_of_steps_says_which_bound_and_how_to_raise_it(cfg):
    """A cut-short that does not say which of the two bounds it hit sends the
    reader to the wrong setting."""
    kf = Kingfisher(cfg, graph=RunawayAgent(), threads=StubCheckpointer())

    events = list(kf.stream(Request("go")))
    stopped = [e for e in events if e.kind == "cut_short"]

    assert stopped, "no cut_short event"
    assert "KINGFISHER_RECURSION_LIMIT" in stopped[0].text
    assert str(cfg.recursion_limit) in stopped[0].text
    assert events[-1].kind == "finished"


# -- the disk bound -------------------------------------------------------


def test_a_session_over_its_disk_bound_cannot_start_another_turn(cfg):
    """Checked before a turn, never during: `execute` writes without any file
    tool seeing it, so there is nothing to intercept mid-turn."""
    kf = Kingfisher(replace(cfg, session_max_bytes=10), graph=StubAgent("ok"),
                    threads=StubCheckpointer())
    session_id = kf.start_session()
    (cfg.workspace / "sessions" / session_id / "derived" / "big.bin").write_bytes(b"x" * 100)

    with pytest.raises(QuotaExceededError, match="over the 10 allowed"):
        kf.run(Request("go", session_id=session_id))


def test_the_disk_bound_is_off_unless_a_deployment_sets_one(cfg):
    """No honest default exists: workspaces vary by orders of magnitude, and
    refusing a turn over a number nobody chose is worse than not bounding it."""
    assert cfg.session_max_bytes is None

    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session_id = kf.start_session()
    (cfg.workspace / "sessions" / session_id / "derived" / "big.bin").write_bytes(b"x" * 10_000)

    assert kf.run(Request("go", session_id=session_id)).answer == "ok"


def test_session_bytes_counts_everything_the_session_holds(cfg, session_dir):
    """Run scratch counts too: the question is what the session costs the
    host, not what is worth keeping."""
    (session_dir / "derived" / "kept.bin").write_bytes(b"x" * 100)
    (session_dir / "runs").mkdir(exist_ok=True)
    (session_dir / "runs" / "scratch.bin").write_bytes(b"y" * 50)

    assert session_bytes(session_dir) >= 150


# -- the idle bound -------------------------------------------------------


def test_reap_falls_back_to_the_configured_ttl(cfg):
    """A janitor that does not want to restate the policy on every call."""
    kf = Kingfisher(replace(cfg, session_ttl_s=60), graph=StubAgent("ok"),
                    threads=StubCheckpointer())
    import os

    idle = kf.start_session()
    os.utime(cfg.workspace / "sessions" / idle, (1_000, 1_000))

    assert kf.reap(now=10_000).removed == (idle,)


def test_a_session_over_budget_is_refused_before_its_data_is_placed(cfg, tmp_path):
    """The two features meet here, and the order is the point.

    `place_data` copies files into `/data`, which grows the session. Checking
    afterwards would let a request that is already over budget add to it and
    only then be refused -- leaving the session larger than the bound it was
    rejected for.
    """
    supplied = tmp_path / "report.pdf"
    supplied.write_bytes(b"z" * 500)

    kf = Kingfisher(replace(cfg, session_max_bytes=10), graph=StubAgent("ok"),
                    threads=StubCheckpointer())
    session_id = kf.start_session()
    session = cfg.workspace / "sessions" / session_id
    (session / "derived" / "already.bin").write_bytes(b"x" * 100)

    with pytest.raises(QuotaExceededError):
        kf.run(Request("go", session_id=session_id, data=(supplied,)))

    assert not (session / "data" / "report.pdf").exists(), "placed despite the refusal"

"""What one session may consume.

Session-scoped, because that is what kingfisher can see: it is tenant-blind by
design, so bounding a *caller* belongs to whatever knows who is calling. These
protect the process from one runaway session, not one caller from another.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from langchain_core.messages import AIMessage

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

    assert result.cut_short
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

    assert not result.cut_short
    assert result.answer == "ok"


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

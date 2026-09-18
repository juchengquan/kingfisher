"""`astream` and `arun`: the same turn, reached from an event loop."""

from __future__ import annotations

import asyncio
import contextvars
import time
from pathlib import Path

import pytest

from kingfisher.application.service import Kingfisher
from kingfisher.domain.request import Request
from kingfisher.domain.session import SessionBusyError
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent


def service(cfg, graph=None):
    return Kingfisher(cfg, graph=graph or StubAgent("ok"), threads=StubCheckpointer())


def _claim(cfg, session_id: str) -> Path:
    from kingfisher.infrastructure.workspace.sessions import claim_path

    return claim_path(cfg.workspace / "sessions" / session_id)


class _SlowAgent(StubAgent):
    """A graph with a step that takes a moment, standing in for a model call.

    A turn that cannot be interrupted mid-step is the whole reason `astream`
    waits on cancellation, so a test of that needs a step to be inside.
    """

    def __init__(self, answer: str = "ok", *, step_s: float = 0.2) -> None:
        super().__init__(answer)
        self._step_s = step_s

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        time.sleep(self._step_s)
        yield from super().stream(state, config, stream_mode, subgraphs)


def _drain(kf, request):
    async def go():
        return [event async for event in kf.astream(request)]

    return asyncio.run(go())


# -- the same turn, not a second one --------------------------------------


def test_astream_yields_what_stream_yields(cfg):
    """One turn, reachable two ways. The async path before this one was a second
    copy of the turn and drifted from the first -- a flag it set by hand, found by
    mutation testing -- so what matters is not that `astream` works but that it is
    the same turn underneath.
    """
    start(cfg, "s")
    sync_kinds = [e.kind for e in service(cfg).stream(Request("go", session_id="s"))]

    start(cfg, "a")
    async_kinds = [e.kind for e in _drain(service(cfg), Request("go", session_id="a"))]

    assert async_kinds == sync_kinds


def test_the_graph_is_still_driven_synchronously(cfg):
    """`guides/middleware.md` tells a deployment that the sync hook is the one that
    runs, and a middleware with only `wrap_model_call` raises the moment a graph is
    driven asynchronously. `StubAgent` has no `astream` at all, so an `astream` that
    reached for one could not pass this.
    """
    graph = StubAgent("ok")
    assert not hasattr(graph, "astream"), "this proves nothing if the stub grows one"
    start(cfg, "s")

    events = _drain(service(cfg, graph), Request("go", session_id="s"))

    assert [e.kind for e in events][-1] == "finished"


def test_arun_returns_what_run_returns(cfg):
    start(cfg, "s")
    synchronous = service(cfg).run(Request("go", session_id="s"))

    start(cfg, "a")
    asynchronous = asyncio.run(service(cfg).arun(Request("go", session_id="a")))

    assert asynchronous.answer == synchronous.answer
    assert asynchronous.completed == synchronous.completed
    assert asynchronous.turn_id == synchronous.turn_id


def test_arun_can_dispose_of_the_session_like_run(cfg):
    """`delete_session` is on the drains and neither stream, for the reason `run`
    gives: a generator has no "after the turn" this library controls.
    """
    session = start(cfg, "s")

    result = asyncio.run(service(cfg).arun(Request("go", session_id=session), delete_session=True))

    assert result.completed
    assert not (cfg.workspace / "sessions" / session).exists()


def test_a_failing_turn_raises_on_the_callers_side(cfg):
    """The exception crosses the thread boundary or it is lost, and a lost one ends
    the stream as though the turn had finished -- an answer of "" and no sign why.
    """

    class _Broken(StubAgent):
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            msg = "the graph fell over"
            raise RuntimeError(msg)
            yield  # pragma: no cover -- a generator that raises before its first yield

    start(cfg, "s")

    with pytest.raises(RuntimeError, match="the graph fell over"):
        _drain(service(cfg, _Broken("ok")), Request("go", session_id="s"))


# -- cancelling waits for the turn to stop --------------------------------
#
# A thread cannot be interrupted, so the choice is between returning at once and
# leaving the session held for a while, or waiting until it is genuinely free. A
# session that answers "busy" for reasons the caller cannot see is the worse of
# the two to be handed.


def test_cancelling_leaves_the_session_free(cfg):
    """The claim is released before the cancel returns, so the next turn is admitted.

    Returning while the turn was still finishing would leave a window in which a
    retry is refused -- which is what the tenancy tests exist to keep visible.
    """
    session = start(cfg, "s")
    kf = service(cfg, _SlowAgent(step_s=0.2))

    async def cancel_mid_turn():
        events = kf.astream(Request("go", session_id=session))
        async for _ in events:
            break  # one event, then stop reading
        await events.aclose()

    asyncio.run(cancel_mid_turn())

    assert not _claim(cfg, session).exists(), "the turn's claim outlived the cancel"
    assert kf.run(Request("again", session_id=session)).completed


def test_a_cancelled_turn_does_not_keep_running_behind_the_caller(cfg):
    """`aclose` waits for the thread, so nothing is still writing to the session when
    the caller continues. Without the wait this passes only by luck of timing.
    """
    session = start(cfg, "s")
    kf = service(cfg, _SlowAgent(step_s=0.2))

    async def cancel_mid_turn():
        events = kf.astream(Request("go", session_id=session))
        async for _ in events:
            break
        await events.aclose()
        # Immediately, with no sleep: a turn still in flight would hold the claim
        # and this would raise.
        return kf.run(Request("again", session_id=session))

    assert asyncio.run(cancel_mid_turn()).completed


def test_a_real_cancelled_task_also_leaves_the_session_free(cfg):
    """The tests above stop reading and call `aclose`, which is not the path a
    cancelled task takes -- the cleanup runs with a cancellation already delivered.
    Both were written before either was checked, and only `aclose` was covered.
    """
    session = start(cfg, "s")
    kf = service(cfg, _SlowAgent(step_s=0.2))

    async def cancel_the_task():
        task = asyncio.ensure_future(kf.arun(Request("go", session_id=session)))
        await asyncio.sleep(0.05)  # far enough in to be inside the step
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_the_task())

    assert not _claim(cfg, session).exists()
    assert kf.run(Request("again", session_id=session)).completed


def test_the_callers_context_reaches_the_turn(cfg):
    """langchain carries an ambient `RunnableConfig` -- and a deployment's tracing
    with it -- in a `ContextVar`. A turn run on a bare `threading.Thread` starts
    with an empty context, so tracing stopped at the turn and nothing said so; this
    is the guard for the shape that does not. `stream` has it for free, and an
    `astream` that loses it is a difference between the two nobody asked for.
    """
    ambient: contextvars.ContextVar[str] = contextvars.ContextVar("ambient", default="UNSET")
    seen = []

    class _Peeking(StubAgent):
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            seen.append(ambient.get())
            yield from super().stream(state, config, stream_mode, subgraphs)

    start(cfg, "s")
    kf = service(cfg, _Peeking("ok"))

    async def run_with_a_context():
        ambient.set("set-by-the-caller")
        await kf.arun(Request("go", session_id="s"))

    asyncio.run(run_with_a_context())

    assert seen == ["set-by-the-caller"]


def test_a_busy_session_still_refuses_the_async_path(cfg):
    """The claim is the turn's, not the entry point's."""
    from kingfisher.domain.session import Session

    kf = service(cfg)
    session = start(cfg, "s")
    held = Session(id=session, directory=cfg.workspace / "sessions" / session)
    held.claim(kf.dirs, _claim(cfg, session), stale_after=3600, now=1000.0)

    with pytest.raises(SessionBusyError, match="already has a turn running"):
        _drain(kf, Request("go", session_id=session))

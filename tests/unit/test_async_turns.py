"""`astream` and `arun`: the same turn, reached from an event loop."""

from __future__ import annotations

import asyncio
import contextvars
import threading
import time
from dataclasses import replace
from functools import partial
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.application.service import Kingfisher
from kingfisher.domain.request import Request
from kingfisher.domain.session import SessionBusyError
from kingfisher.infrastructure.harness import runtime
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent


def service(cfg, graph=None):
    return Kingfisher(cfg, graph=graph or StubAgent("ok"), threads=StubCheckpointer())


def _claim(cfg, session_id: str) -> Path:
    from kingfisher.layout import CLAIM, HARNESS

    return cfg.workspace / "sessions" / session_id / HARNESS / CLAIM


class _SlowAgent(StubAgent):
    """A graph with a step that takes a moment, standing in for a model call.

    Slow on both halves and slow in the way each one is: the sync step blocks,
    and the async step awaits, so a cancellation reaches it the way it would
    reach a real model call rather than the way it reaches a blocked thread.
    """

    def __init__(self, answer: str = "ok", *, step_s: float = 0.2) -> None:
        super().__init__(answer)
        self._step_s = step_s

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        time.sleep(self._step_s)
        yield from super().stream(state, config, stream_mode, subgraphs)

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        await asyncio.sleep(self._step_s)
        for chunk in StubAgent.stream(self, state, config, stream_mode, subgraphs):
            yield chunk


def _drain(kf, request):
    async def go():
        return [event async for event in kf.astream(request)]

    return asyncio.run(go())


# -- the same turn, not a second one --------------------------------------


class _ManySteps(StubAgent):
    """Keeps taking steps, so a deadline of zero fires between chunks."""

    def __init__(self) -> None:
        super().__init__("ok", updates=[{"agent": {"messages": [AIMessage("working")]}}] * 6)


class _OutOfSteps(StubAgent):
    """Hits langgraph's own recursion bound, which arrives as an exception."""

    def stream(self, state, config, stream_mode=None, subgraphs=False):
        yield from ()
        raise runtime.OutOfSteps

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        for chunk in ():  # pragma: no cover -- an async generator that only raises
            yield chunk
        raise runtime.OutOfSteps


#: Each way a turn can end, as a factory rather than an instance: the two paths
#: get their own graph, since a stub records what it was asked and sharing one
#: would let the second run read the first one's state.
ENDINGS = {
    "answered": partial(StubAgent, "ok"),
    "past its deadline": _ManySteps,
    "out of steps": partial(_OutOfSteps, "ok"),
}


@pytest.mark.parametrize("ending", ENDINGS)
def test_both_paths_end_a_turn_the_same_way(cfg, ending):
    """Parity where it is decided per loop rather than shared.

    Every bound used to be written twice, once in each loop, and the ending the
    caller sees with it -- which is the shape the async path drifted in before, a
    flag set by hand in the copy. Only the answered path was compared across the
    two, and that is the one path neither loop decides anything about.

    Compared as the events a caller receives and the reason the turn gives for
    stopping, because those are what a caller branches on.
    """
    bounded = replace(cfg, turn_timeout_s=0) if ending == "past its deadline" else cfg

    start(bounded, "sync")
    synchronous = list(
        service(bounded, ENDINGS[ending]()).stream(Request("go", session_id="sync"))
    )

    start(bounded, "async")
    asynchronous = _drain(
        service(bounded, ENDINGS[ending]()), Request("go", session_id="async")
    )

    assert [e.kind for e in asynchronous] == [e.kind for e in synchronous]
    assert asynchronous[-1].result.stop_reason == synchronous[-1].result.stop_reason
    assert asynchronous[-1].result.completed == synchronous[-1].result.completed


def test_each_entry_point_drives_the_graph_its_own_way(cfg):
    """Which driver each entry point uses, which is not an implementation detail:
    the `a`-prefixed middleware hook runs on one and the sync hook on the other,
    so a path that quietly fell back to the other driver would make that
    requirement disappear and reappear.
    """
    used: list[str] = []

    class _Recording(StubAgent):
        def stream(self, state, config, stream_mode=None, subgraphs=False):
            used.append("stream")
            yield from super().stream(state, config, stream_mode, subgraphs)

        async def astream(self, state, config, stream_mode=None, subgraphs=False):
            used.append("astream")
            for chunk in StubAgent.stream(self, state, config, stream_mode, subgraphs):
                yield chunk

    start(cfg, "s")
    kf = service(cfg, _Recording("ok"))

    _drain(kf, Request("go", session_id="s"))
    assert used == ["astream"]

    used.clear()
    list(kf.stream(Request("again", session_id="s")))
    assert used == ["stream"]


def test_arun_returns_what_run_returns(cfg):
    start(cfg, "s")
    synchronous = service(cfg).run(Request("go", session_id="s"))

    start(cfg, "a")
    asynchronous = asyncio.run(service(cfg).arun(Request("go", session_id="a")))

    assert asynchronous.answer == synchronous.answer
    assert asynchronous.completed == synchronous.completed
    # Filled, not equal. These are two turns in two sessions, and they compared equal
    # only while a turn id was a sequence and both were the first -- which is the kind
    # of agreement that stops meaning anything the moment the ids are made rather than
    # counted. What this is asking is whether `arun` fills the field at all.
    assert asynchronous.turn_id and synchronous.turn_id
    assert asynchronous.turn_id != synchronous.turn_id


def test_arun_can_dispose_of_the_session_like_run(cfg):
    """`delete_session` is on the drains and neither stream, for the reason `run`
    gives: a generator has no "after the turn" this library controls.
    """
    session = start(cfg, "s")

    result = asyncio.run(service(cfg).arun(Request("go", session_id=session), delete_session=True))

    assert result.completed
    assert not (cfg.workspace / "sessions" / session).exists()


def test_disposing_of_a_session_does_not_happen_on_the_event_loop(cfg, monkeypatch):
    """Disposal reaches the store as well as the disk, and a deployment's store may
    be a network away -- so `arun` must not do it on the loop while every other turn
    in the process waits. Measured at 0.75ms locally, which is the floor and not the
    cost that matters.

    Asserted as *which thread* each of the two deletions ran on, because the time
    they take is the deployment's and the thread they take it on is ours.
    """
    import threading

    from kingfisher import DefaultBackends

    on_main: dict[str, bool] = {}

    def watched(name, original):
        def recording(self, *args):
            on_main[name] = threading.current_thread() is threading.main_thread()
            return original(self, *args)

        return recording

    monkeypatch.setattr(DefaultBackends, "delete", watched("session", DefaultBackends.delete))
    monkeypatch.setattr(
        StubCheckpointer, "delete_thread", watched("thread", StubCheckpointer.delete_thread)
    )
    session = start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())

    asyncio.run(kf.arun(Request("go", session_id=session), delete_session=True))

    assert on_main == {"thread": False, "session": False}, "a deletion ran on the loop's thread"
    assert not (cfg.workspace / "sessions" / session).exists()


def test_a_failing_turn_raises_on_the_callers_side(cfg):
    """A turn that raises must raise at the caller, not end the stream as though it
    had finished -- an answer of "" and no sign why.
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


#: Every way a caller can cancel twice, or cancel something already cancelling.
#: Named rather than inlined because the interesting one is not obvious: a second
#: cancellation arriving *during* the first one's cleanup is what would interrupt
#: the close and leave the session claimed.
DOUBLE_CANCELS = (
    "twice at once",
    "five times at once",
    "again while it unwinds",
    "wait_for expiry",
    "timeout, then cancelled",
)


async def _cancel_twice(kf, session: str, how: str) -> None:
    """Cancel a turn in flight the way `how` says, and return once it has stopped."""
    if how == "wait_for expiry":
        with pytest.raises((TimeoutError, asyncio.CancelledError)):
            await asyncio.wait_for(kf.arun(Request("go", session_id=session)), timeout=0.05)
        return

    if how == "timeout, then cancelled":

        async def under_a_deadline() -> None:
            async with asyncio.timeout(0.05):
                await kf.arun(Request("go", session_id=session))

        task = asyncio.ensure_future(under_a_deadline())
        await asyncio.sleep(0.04)
        task.cancel()
        with pytest.raises(BaseException):  # noqa: B017 -- either arrives first
            await task
        return

    task = asyncio.ensure_future(kf.arun(Request("go", session_id=session)))
    await asyncio.sleep(0.05)
    if how == "again while it unwinds":
        task.cancel()
        for _ in range(5):
            # A cancel on each of the next few loop iterations, which is while the
            # first one's cleanup is unwinding.
            await asyncio.sleep(0)
            task.cancel()
    else:
        for _ in range(2 if how == "twice at once" else 5):
            task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.parametrize("how", DOUBLE_CANCELS)
def test_cancelling_twice_still_leaves_the_session_free(cfg, how):
    """A second cancellation must not interrupt the first one's cleanup.

    The only await in the cleanup is the `aclose` that runs the turn's `finally`,
    and an await is where a cancellation lands -- so the worry was that cancelling
    twice would skip the close and hold the claim, which is the failure the close
    exists to prevent arriving by another door.

    **What this cannot do, said plainly.** It asserts what a caller sees, and it
    cannot discriminate how the session came free: awaiting a cancelled task is
    itself a turn of the event loop, and one turn is all asyncio's
    async-generator finalizer needs. Measured -- every case here passes with the
    explicit closes deleted. The guard for the mechanism is the test below, and
    the one for `astream`'s inner close is
    `test_a_cancelled_turn_does_not_keep_running_behind_the_caller`, which uses
    `aclose` and so can run synchronous work with no loop turn in between.

    It stays because the behaviour is what a caller depends on, and because five
    ways of cancelling twice is the part nobody would re-derive by reading.
    """
    session = start(cfg, f"s-{DOUBLE_CANCELS.index(how)}")
    # Long enough that the cancellations land inside the step and short enough
    # that the turn admitted afterwards does not cost the suite a second.
    kf = service(cfg, _SlowAgent(step_s=0.3))

    async def cancel_then_look() -> tuple[bool, bool]:
        await _cancel_twice(kf, session, how)
        # No await between the cancellation and these two, so nothing gets a
        # chance to tidy up on the turn's behalf.
        held = _claim(cfg, session).exists()
        try:
            admitted = kf.run(Request("after", session_id=session)).completed
        except SessionBusyError:
            admitted = False
        return held, admitted

    held, admitted = asyncio.run(cancel_then_look())

    assert not held, f"{how} left the turn's claim behind"
    assert admitted, f"{how} left the session unusable"


# -- cancelling during setup -----------------------------------------------
#
# Setup runs on a thread and claims the session before a cancel can reach it, so
# a cancel there has a claim to give back that no lifecycle has taken yet.


def _held_in_setup(monkeypatch) -> tuple[threading.Event, threading.Event]:
    """Stop each turn at the end of setup, claimed and built, until `release` is set.

    Returns `(entered, release)`.
    """
    entered, release = threading.Event(), threading.Event()
    built = Kingfisher._built_turn

    def held(self, *args, **kwargs):
        entered.set()
        release.wait(timeout=5)
        return built(self, *args, **kwargs)

    monkeypatch.setattr(Kingfisher, "_built_turn", held)
    return entered, release


def test_cancelling_during_setup_leaves_the_session_free(cfg, monkeypatch):
    """A cancel returned past the setup thread, which went on to claim the session
    with nobody left to end the turn, so the next one was refused as busy until the
    claim went stale.

    Driven rather than timed: setup is let go only after the cancel has landed, so
    this cannot pass by setup happening to finish first.
    """
    session = start(cfg, "s")
    kf = service(cfg)
    entered, release = _held_in_setup(monkeypatch)

    async def cancel_during_setup() -> bool:
        task = asyncio.ensure_future(kf.arun(Request("go", session_id=session)))
        assert await asyncio.to_thread(entered.wait, 5), "the turn never reached setup"
        task.cancel()
        # The cancel is delivered on the first of these, while setup is still held.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        # No await between the cancel returning and this.
        return _claim(cfg, session).exists()

    assert not asyncio.run(cancel_during_setup()), "setup's claim outlived the cancel"
    assert kf.run(Request("again", session_id=session)).completed


@pytest.mark.parametrize("how", DOUBLE_CANCELS)
def test_cancelling_during_setup_any_way_leaves_the_session_free(cfg, monkeypatch, how):
    """The ways of cancelling above, landing in setup instead of the model call.

    `wait_for` and `asyncio.timeout` turn their cancel into `TimeoutError` only if a
    `CancelledError` comes back out, so the one setup waited through has to be
    re-raised rather than swallowed with the claim given back.
    """
    session = start(cfg, f"s-{DOUBLE_CANCELS.index(how)}")
    kf = service(cfg)
    built = Kingfisher._built_turn

    def slow(self, *args, **kwargs):
        time.sleep(0.3)  # every cancel in `_cancel_twice` lands inside this
        return built(self, *args, **kwargs)

    monkeypatch.setattr(Kingfisher, "_built_turn", slow)

    async def cancel_then_look() -> bool:
        await _cancel_twice(kf, session, how)
        return _claim(cfg, session).exists()

    assert not asyncio.run(cancel_then_look()), f"{how} left setup's claim behind"
    monkeypatch.setattr(Kingfisher, "_built_turn", built)
    assert kf.run(Request("after", session_id=session)).completed


def test_cancelling_at_any_step_of_setup_leaves_the_session_free(cfg, monkeypatch, tmp_path):
    """A cancel landing at any one of setup's awaits -- the lookup, the open, the pin,
    the claim, the quota, the data, the pause, the transcript, the build -- must leave
    the session unclaimed and usable. Before setup was a sequence the only await was
    one thread; each step is a place a cancel can land now, and a claim or a built
    turn dropped at any of them holds the session until the claim goes stale.

    Counted rather than listed, so a step added to setup is cancelled at too.
    """
    import yaml

    from kingfisher.domain.access import parse
    from kingfisher.infrastructure import steps as steps_module
    from tests.conftest import an_agent

    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(
        cfg,
        access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"),
        session_max_bytes=10**9,
    )
    data = tmp_path / "in.csv"
    data.write_text("a,b\n")
    session = start(policied, "s")
    kf = Kingfisher(policied, graph=StubAgent("ok"), threads=StubCheckpointer())
    asked, held = Request("go", agent="only_a", session_id=session, data=(data,)), ("A",)
    real = steps_module._awaited
    # The first turn pins the agent and every later one reads the pin instead, so the
    # count is taken on a turn like the ones cancelled below.
    kf.run(asked, source_ids=held)

    made: list[object] = []

    def counting(step):
        made.append(step)
        return real(step)

    monkeypatch.setattr(steps_module, "_awaited", counting)
    asyncio.run(kf.arun(asked, source_ids=held))
    monkeypatch.setattr(steps_module, "_awaited", real)
    # The control: a setup that made a step or two would cancel at a step or two.
    assert len(made) >= 10, f"setup made {len(made)} async steps"

    def holding(target: int, entered: asyncio.Event, gate: asyncio.Event):
        count = 0

        def awaited(step):
            nonlocal count
            count += 1
            if count != target:
                return real(step)

            async def held_here():
                entered.set()
                await gate.wait()
                return await real(step)

            return held_here()

        return awaited

    async def cancel_at(target: int) -> None:
        entered, gate = asyncio.Event(), asyncio.Event()
        monkeypatch.setattr(steps_module, "_awaited", holding(target, entered, gate))
        task = asyncio.ensure_future(kf.arun(asked, source_ids=held))
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        # The cancel is delivered on the first of these; a step that changes something
        # is then let finish, which is what the sequence waits for.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    for target in range(1, len(made) + 1):
        asyncio.run(cancel_at(target))
        monkeypatch.setattr(steps_module, "_awaited", real)
        step = made[target - 1]
        assert not _claim(policied, session).exists(), f"cancelled at {step!r}, the claim stayed"
        assert kf.run(asked, source_ids=held).completed, f"cancelled at {step!r}, unusable"


def test_setup_runs_on_kingfishers_pool_in_the_callers_context(cfg, monkeypatch):
    """Setup queued behind every other blocking call on the loop's default executor,
    12 workers on an 8-CPU host, and a hop that starts its thread in an empty context
    drops the caller's tracing -- which `findings.md` records a bare `run_in_executor`
    doing.
    """
    ambient: contextvars.ContextVar[str] = contextvars.ContextVar("ambient", default="UNSET")
    seen = []
    built = Kingfisher._built_turn

    def watched(self, *args, **kwargs):
        seen.append((threading.current_thread().name, ambient.get()))
        return built(self, *args, **kwargs)

    monkeypatch.setattr(Kingfisher, "_built_turn", watched)
    start(cfg, "s")
    kf = service(cfg)

    async def run_with_a_context():
        ambient.set("set-by-the-caller")
        await kf.arun(Request("go", session_id="s"))

    asyncio.run(run_with_a_context())

    ((thread, context),) = seen
    assert thread.startswith("kingfisher"), f"setup ran on {thread!r}"
    assert context == "set-by-the-caller"


def test_the_turns_cleanup_cannot_be_interrupted_by_a_cancellation():
    """Why the tests above hold by construction and not by timing.

    A cancellation is delivered at a suspension point. `_end_turn`, which releases the
    claim, the checkpointer and the interpreter, is a plain function with nothing in it
    to suspend at, so once it starts it finishes -- however many times a caller
    cancels, and however slow it gets. `astream` runs it on kingfisher's pool and
    waits for it through every cancel; `stream` runs it where it is.

    Made async for the async path, it would start being interruptible, every test
    above would pass on timing alone, and nothing else here would notice.
    """
    import inspect

    from kingfisher.application.service import Kingfisher as Service

    ending = Service._end_turn
    assert not inspect.iscoroutinefunction(ending), (
        "`_end_turn` is a coroutine now, so a cancellation can stop it halfway -- "
        "between giving the claim back and closing the checkpointer, or before either"
    )
    assert not inspect.isasyncgenfunction(ending) and not inspect.isgeneratorfunction(ending)


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
    from kingfisher import backend_at
    from kingfisher.layout import CLAIM

    kf = service(cfg)
    session = start(cfg, "s")
    assert backend_at(cfg, cfg.workspace / "sessions" / session).claim(CLAIM, stale_after=3600)

    with pytest.raises(SessionBusyError, match="already has a turn running"):
        _drain(kf, Request("go", session_id=session))

"""The async path: the same turn, on an event loop.

`astream` exists for concurrency, not for speed. A turn is the model's time --
our own code measures at 15-46ms of 1.5s -- so nothing here makes one turn
faster. What it buys is turns overlapping, which is the shape a service needs.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from kingfisher import Kingfisher
from kingfisher.domain.request import Request
from tests.conftest import StubCheckpointer
from tests.test_run import StubAgent


class AsyncStubAgent(StubAgent):
    """A stub graph that answers on `astream` as the real one does."""

    async def astream(self, state, config, stream_mode=None, subgraphs=False):
        for chunk in self.stream(state, config, stream_mode):
            await asyncio.sleep(0)  # yield the loop, as a real call would
            yield chunk


def test_astream_yields_the_same_events_as_stream(cfg):
    """One `_prepare`, two loops. If they drift, this is what notices."""
    service = Kingfisher(cfg, agent=AsyncStubAgent("ok"), threads=StubCheckpointer())

    sync_kinds = [e.kind for e in service.stream(Request("go"))]

    async def collect():
        return [e.kind async for e in service.astream(Request("go"))]

    assert asyncio.run(collect()) == sync_kinds


def test_arun_returns_the_same_result_shape(cfg):
    service = Kingfisher(cfg, agent=AsyncStubAgent("hello"), threads=StubCheckpointer())

    result = asyncio.run(service.arun(Request("go")))

    assert result.answer == "hello"
    assert result.turn_id == "t001"
    assert result.run_dir.is_dir()


def test_turns_on_one_service_genuinely_overlap(cfg):
    """The whole claim, made deterministic.

    Each turn waits at a barrier that only opens once all three have reached
    it. If `astream` serialised -- because `_prepare` blocked the loop, or the
    graph were driven synchronously -- the third would never arrive and this
    would time out rather than fail on an assertion about scheduling order.
    """
    barrier = asyncio.Barrier(3)

    class Barred(AsyncStubAgent):
        async def astream(self, state, config, stream_mode=None, subgraphs=False):
            await barrier.wait()  # nobody proceeds until everyone is here
            for chunk in self.stream(state, config, stream_mode):
                yield chunk

    service = Kingfisher(cfg, agent=Barred("ok"), threads=StubCheckpointer())
    # A session id is a credential now: it has to be started before it is used.
    names = [service.start_session() for _ in range(3)]

    async def turn(name):
        return [e.kind async for e in service.astream(Request("go", session_id=name))]

    async def race():
        return await asyncio.wait_for(
            asyncio.gather(*(turn(n) for n in names)), timeout=10
        )

    kinds = asyncio.run(race())
    assert all(k[-1] == "finished" for k in kinds)


def test_a_sync_saver_is_refused_rather_than_blocking(cfg):
    """`SqliteSaver` does not merely block the loop on `aget_tuple` -- it
    raises. Worth pinning, because "use the async saver" is otherwise a
    footnote someone discovers at runtime."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    with pytest.raises(NotImplementedError, match="does not support async"):
        asyncio.run(SqliteSaver.aget_tuple(object(), {}))  # ty: ignore[invalid-argument-type]


def test_the_blocking_setup_does_not_stall_every_other_turn(cfg, monkeypatch):
    """`_prepare` is filesystem work -- 15-46ms measured -- and on an event loop
    that is 15-46ms during which no other turn can progress. `astream` runs it
    with `asyncio.to_thread`, so three turns overlap their setup.

    Asserted as two facts rather than as a stopwatch. It used to time three
    turns and require the total under 70% of the serial cost, which is the same
    claim expressed as a ratio -- and a ratio is a race against the machine. On
    a loaded runner it came in at 0.34s against a 0.315s bar and failed, having
    proved nothing except that CI was busy. Both replacements hold whatever
    speed the machine runs at:

    - the work happened on a thread that is not the loop's, so the loop was free
    - two of the three overlapped, so they were not merely off the loop one
      after another -- which `to_thread` awaited in sequence would also be
    """
    real_prepare = Kingfisher._prepare
    delay = 0.15
    loop_thread: list[int] = []
    spans: list[tuple[float, float, int]] = []
    lock = threading.Lock()

    def slow_prepare(self, *args, **kwargs):
        started = time.perf_counter()
        time.sleep(delay)  # blocking, exactly like the I/O it stands in for
        finished = time.perf_counter()
        with lock:
            spans.append((started, finished, threading.get_ident()))
        return real_prepare(self, *args, **kwargs)

    monkeypatch.setattr(Kingfisher, "_prepare", slow_prepare)

    service = Kingfisher(cfg, agent=AsyncStubAgent("ok"), threads=StubCheckpointer())
    names = [service.start_session() for _ in range(3)]

    async def race():
        loop_thread.append(threading.get_ident())

        async def turn(name):
            async for _ in service.astream(Request("go", session_id=name)):
                pass

        await asyncio.gather(*(turn(n) for n in names))

    asyncio.run(race())

    assert len(spans) == len(names), spans
    assert all(ident != loop_thread[0] for _, _, ident in spans), (
        "setup ran on the event loop's own thread — every other turn was stalled "
        "for the duration, which is what `to_thread` is here to prevent"
    )
    assert _any_overlap(spans), (
        f"no two of {len(spans)} setups overlapped, so they ran one after another "
        f"— off the loop, but still serialised: {_as_intervals(spans)}"
    )


def _any_overlap(spans):
    """Whether any two of these intervals share a moment.

    The whole claim, and it does not care how long anything took. Serialised
    work produces disjoint intervals at any speed; concurrent work overlaps at
    any speed.
    """
    return any(
        a_start < b_end and b_start < a_end
        for i, (a_start, a_end, _) in enumerate(spans)
        for b_start, b_end, _ in spans[i + 1 :]
    )


def _as_intervals(spans):
    """The spans relative to the first start, for a failure worth reading."""
    origin = min(start for start, _, _ in spans)
    return [(round(s - origin, 3), round(e - origin, 3)) for s, e, _ in sorted(spans)]


def test_the_turn_bound_holds_on_the_async_path_too(cfg):
    """The timeout is checked between chunks, and there are two loops now.
    Enforcing it in one and not the other is exactly the drift that splitting
    `stream` and `astream` risks, so both are pinned.
    """
    from dataclasses import replace

    from tests.test_quotas import SlowAgent

    class SlowAsyncAgent(SlowAgent):
        async def astream(self, state, config, stream_mode=None, subgraphs=False):
            for chunk in self.stream(state, config, stream_mode):
                await asyncio.sleep(0)
                yield chunk

    agent = SlowAsyncAgent(steps=50)
    service = Kingfisher(
        replace(cfg, turn_timeout_s=0), agent=agent, threads=StubCheckpointer()
    )

    result = asyncio.run(service.arun(Request("go")))

    assert result.cut_short
    assert agent.taken < 50, "the async turn ran to completion despite the bound"


def test_an_unbounded_async_turn_is_untouched(cfg):
    """The negative control: without it the test above would pass even if
    every async turn were cut short."""
    service = Kingfisher(cfg, agent=AsyncStubAgent("ok"), threads=StubCheckpointer())

    assert not asyncio.run(service.arun(Request("go"))).cut_short


def test_the_async_saver_is_reachable_from_outside_the_package(cfg):
    """Why it is exported at all.

    `astream` needs a saver with async methods -- `SqliteSaver` raises
    `NotImplementedError` on `aget_tuple`, so a sync one does not merely block
    the loop, it refuses. Until this was public, the only saver a consumer
    could build was the one that cannot serve the async path, which made the
    concurrency `astream` exists for unreachable from outside.

    The refusal itself is pinned by
    `test_a_sync_saver_is_refused_rather_than_blocking`; this is the other
    half -- that the saver which does not refuse is reachable by name.
    """
    import kingfisher

    async def open_and_ask():
        async with kingfisher.async_checkpointer(cfg) as saver:
            return await saver.aget_tuple({"configurable": {"thread_id": "nobody"}})

    assert asyncio.run(open_and_ask()) is None

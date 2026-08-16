"""The async path: the same turn, on an event loop.

`astream` exists for concurrency, not for speed. A turn is the model's time --
our own code measures at 15-46ms of 1.5s -- so nothing here makes one turn
faster. What it buys is turns overlapping, which is the shape a service needs.
"""

from __future__ import annotations

import asyncio
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
    """`_prepare` is filesystem work -- 15-46ms measured -- and on an
    event loop that is 15-46ms during which no other turn can progress.

    Run on a worker thread, three turns overlap their setup and the wall clock
    is about one of them. Run on the loop, it is the sum. The margin here is
    deliberately wide: the claim is "not serialised", not a precise number.
    """
    real_prepare = Kingfisher._prepare
    delay = 0.15

    def slow_prepare(self, *args, **kwargs):
        time.sleep(delay)  # blocking, exactly like the I/O it stands in for
        return real_prepare(self, *args, **kwargs)

    monkeypatch.setattr(Kingfisher, "_prepare", slow_prepare)

    service = Kingfisher(cfg, agent=AsyncStubAgent("ok"), threads=StubCheckpointer())
    names = [service.start_session() for _ in range(3)]

    async def race():
        async def turn(name):
            async for _ in service.astream(Request("go", session_id=name)):
                pass

        started = time.perf_counter()
        await asyncio.gather(*(turn(n) for n in names))
        return time.perf_counter() - started

    elapsed = asyncio.run(race())

    serial = delay * len(names)
    assert elapsed < serial * 0.7, f"setup serialised: {elapsed:.2f}s of a possible {serial:.2f}s"


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

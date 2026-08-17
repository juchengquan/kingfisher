"""Turning a run into an SSE body, and the two rules that make it correct.

**The first event is pulled before the response starts.** `astream` does
`_prepare` as its first statement and yields nothing before it, so every
refusal a turn can make -- unknown session, busy, quota, a capability not
granted, a definition that will not parse -- is available before any event
exists. Pulling it here means those become status codes. Handing the generator
straight to `StreamingResponse` would put 200 on the wire first and bury every
refusal in the stream, where a client has to parse the body to learn it failed.

**A heartbeat is what notices a hangup.** Proxies drop idle connections, which
is the obvious reason. The better one is that a disconnect is only detected
when the server next tries to send: without a heartbeat, a client that hangs up
during a quiet two-minute tool call keeps costing model calls until the next
token. With one, the waste is bounded by the interval.

Stopping the turn on disconnect then needs nothing else. Closing the generator
runs `astream`'s `finally`, which releases the session's claim -- measured: the
claim is held mid-turn, gone after a hangup, and the next turn on that session
is accepted immediately. Disconnect *is* cancellation, which is why there is no
endpoint for it.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from kingfisher_service.payloads import frame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from kingfisher import RunEvent

#: What a heartbeat looks like on the wire: an SSE comment. Every client
#: ignores it by spec, so it keeps the connection warm without becoming an
#: event kind that consumers have to know about.
PING = ": ping\n\n"

_EXHAUSTED = object()


async def _next_or_end(events: AsyncIterator[RunEvent]) -> Any:
    """`anext`, with exhaustion as a value rather than an exception.

    So the wait below can treat "finished" and "still working" the same way --
    a task that completed -- instead of catching `StopAsyncIteration` out of
    `asyncio.wait`, where it does not travel well.
    """
    try:
        return await events.__anext__()
    except StopAsyncIteration:
        return _EXHAUSTED


async def close(events: AsyncIterator[RunEvent]) -> None:
    """Let go of a run, however it ended.

    This is what gives the session's claim back: it runs `astream`'s own
    `finally`, which releases the slot whether the turn answered, was refused
    mid-stream, or had its client hang up. An iterator that is not a generator
    has nothing to close, and says so by not having the method.
    """
    closer = getattr(events, "aclose", None)
    if closer is not None:
        await closer()


async def opening(events: AsyncIterator[RunEvent]) -> RunEvent | None:
    """The first event, or `None` if the run produced none.

    Deliberately outside any `try`. Whatever `_prepare` raises must reach the
    caller so it can become a status code, and swallowing it here is the one
    mistake that would make the rule above pointless.
    """
    first = await _next_or_end(events)
    return None if first is _EXHAUSTED else first


async def body(
    events: AsyncIterator[RunEvent], first: RunEvent | None, *, heartbeat_s: float
) -> AsyncIterator[str]:
    """The SSE body: the event already pulled, then the rest, then silence.

    The pending `__anext__` is kept across heartbeats rather than restarted.
    Restarting it would abandon a model call in flight every time the interval
    elapsed, which is the opposite of what a keepalive is for.
    """
    if first is not None:
        yield frame(first)

    pending: asyncio.Task[Any] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(_next_or_end(events))
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_s)
            if not done:
                yield PING
                continue
            event = pending.result()
            pending = None
            if event is _EXHAUSTED:
                return
            yield frame(event)
    finally:
        # Reached on a hangup as well as on a finished turn, and the two need
        # different treatment for the same outcome -- `astream`'s `finally`
        # running, which is what gives the session's claim back.
        #
        # With a `__anext__` still in flight the run is *inside* the generator,
        # and `aclose()` there raises "asynchronous generator is already
        # running". Cancelling the task is the way in: the `CancelledError` is
        # thrown at the generator's own suspension point and unwinds it from
        # there.
        #
        # The `await` makes the stop have happened rather than be scheduled. No
        # test can show the difference -- a loop that keeps running gets there
        # anyway, which is every real server and `asyncio.run` both -- so this
        # is determinism at teardown rather than a fix for anything observed.
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        else:
            await close(events)

"""A turn that is stopping begins no more tool calls, and waits for those a thread runs."""

from __future__ import annotations

import asyncio
import functools
import threading
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, StructuredTool, ToolException
from langgraph.errors import GraphBubbleUp

from kingfisher.core.session.threads import finished

from .results import answer_in, failed

#: Set once the turn this runs in is stopping: its caller stopped reading, it ran out of
#: time, or its graph failed. Read on langgraph's threads, which copy the context.
STOPPING: ContextVar[threading.Event | None] = ContextVar("kingfisher_turn_stopping", default=None)

#: What a call refused for it is answered with.
NOT_RUN = "This call did not run: the turn stopped before it began."


@dataclass(frozen=True)
class OnThreads:
    """The calls a thread runs for an async turn, which no cancel can stop, and what each
    answered."""

    running: set[asyncio.Future[Any]] = field(default_factory=set)
    #: By call id, whether or not the graph lived to take it: a cancel can end the graph's
    #: step after the call answered and before its answer was kept.
    answers: dict[str, ToolMessage] = field(default_factory=dict)

    def hold(self, work: asyncio.Future[Any]) -> None:
        self.running.add(work)
        work.add_done_callback(self.running.discard)


#: The async turn's `OnThreads`. `None` on the sync path, where closing the graph's stream
#: already waits for its threads.
ON_THREADS: ContextVar[OnThreads | None] = ContextVar("kingfisher_turn_threads", default=None)


def stopping() -> bool:
    """Whether the turn this runs in is stopping."""
    flag = STOPPING.get()
    return flag is not None and flag.is_set()


def on_a_thread(tool: Any) -> bool:
    """Whether langchain runs this tool's body on a thread when the graph is awaited."""
    if isinstance(tool, StructuredTool):
        return tool.coroutine is None
    return isinstance(tool, BaseTool) and type(tool)._arun is BaseTool._arun


async def waited_for[T](work: Awaitable[T]) -> tuple[T, asyncio.CancelledError | None]:
    """What `work`, which a thread runs, produced, and the cancel that came meanwhile.

    Awaited through every cancel, since the thread goes on whatever is cancelled, and held
    by the turn so its stop can wait for it too. A cancel here is a stopping turn, so the
    calls still queued for a thread are refused from then on.
    """
    pending = asyncio.ensure_future(work)
    if (threads := ON_THREADS.get()) is not None:
        threads.hold(pending)
    return await finished(pending, on_cancel=_stop)


def _stop() -> None:
    if (flag := STOPPING.get()) is not None:
        flag.set()


@contextmanager
def _stopping_if_it_fails() -> Iterator[None]:
    """A call that raises fails its graph, which says so only once every call beside it has
    ended: from the raise, those not yet begun are refused. An interrupt is no failure,
    and the calls beside it run as they would."""
    try:
        yield
    except GraphBubbleUp:
        raise
    except BaseException:
        _stop()
        raise


def unless_stopping(tool: Any) -> Any:
    """`tool`, refusing to begin on its thread once its turn is stopping.

    Checked there, not only where the call is handed over: an awaited graph hands every
    call of a reply over at once, and the rest wait for a thread.
    """
    if not isinstance(tool, StructuredTool) or tool.func is None:
        return tool
    body = tool.func

    @functools.wraps(body)
    def begun(*args: Any, **kwargs: Any) -> Any:
        if stopping():
            raise ToolException(NOT_RUN)
        try:
            return body(*args, **kwargs)
        except BaseException as failure:
            # What no middleware answers for fails the graph, and the loop hears of it only
            # once a thread is free to run the tool's error callbacks: stopped here, at once.
            if not isinstance(failure, Exception):
                _stop()
            raise

    return tool.model_copy(update={"func": begun})


class StoppingMiddleware(AgentMiddleware):
    """Refuse a call that begins once its turn is stopping, and on the async path wait for
    one a thread runs.

    Closing a graph's stream waits for every call its reply queued, starting each as a
    thread frees: 40 calls asked together and stopped with a few under way all charge. A
    cancelled awaited graph lets go of a call on a thread at once, so the next turn is told
    it did not complete while its charge lands after.
    """

    def wrap_tool_call(self, request: Any, handler: Callable[[Any], Any]) -> Any:
        if stopping():
            return failed(request.tool_call, NOT_RUN)
        with _stopping_if_it_fails():
            return handler(request)

    async def awrap_tool_call(
        self, request: Any, handler: Callable[[Any], Awaitable[Any]]
    ) -> Any:
        if stopping():
            return failed(request.tool_call, NOT_RUN)
        threads = ON_THREADS.get()
        with _stopping_if_it_fails():
            if threads is None or not on_a_thread(request.tool):
                return await handler(request)
            result, cancelled = await waited_for(
                handler(request.override(tool=unless_stopping(request.tool)))
            )
        call = str(request.tool_call.get("id") or "")
        if (answer := answer_in(result, call)) is not None:
            threads.answers[call] = answer
        if cancelled is not None:
            raise cancelled
        return result

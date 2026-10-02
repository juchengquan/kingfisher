"""The threads kingfisher hands blocking work to, and waiting for that work to finish."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from kingfisher.config import ConfigError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _Pool:
    """The process's one pool, made the first time a size is asked for."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.executor: ThreadPoolExecutor | None = None
        self.size: int | None = None


_POOL = _Pool()


def thread_pool(size: int) -> ThreadPoolExecutor:
    """The process's pool, which holds `size` threads.

    A second size is refused rather than ignored: whichever service was built first
    would otherwise decide for every other, and the one that asked for more would
    queue behind a number it never chose.
    """
    if size < 1:
        msg = f"KINGFISHER_THREAD_POOL_SIZE must be at least 1, got {size}"
        raise ConfigError(msg)
    with _POOL.lock:
        if _POOL.executor is None:
            _POOL.executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="kingfisher")
            _POOL.size = size
        elif size != _POOL.size:
            msg = (
                f"this process's thread pool holds {_POOL.size} threads and this service "
                f"asks for {size}; KINGFISHER_THREAD_POOL_SIZE is one number per process"
            )
            raise ConfigError(msg)
        return _POOL.executor


def off_loop[T](
    pool: ThreadPoolExecutor, fn: Callable[..., T], /, *args: Any, **kwargs: Any
) -> asyncio.Future[T]:
    """`fn` on `pool`, in the caller's context.

    The context is copied by hand, as `asyncio.to_thread` does: a bare
    `run_in_executor` starts `fn` in an empty one, which drops a caller's ambient
    `RunnableConfig` and the tracing hanging off it.
    """
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return asyncio.get_running_loop().run_in_executor(pool, call)


async def finished[T](work: Awaitable[T]) -> tuple[T, asyncio.CancelledError | None]:
    """What `work` produced once it has finished, and the cancel that arrived meanwhile.

    Waited for however many times the caller is cancelled, so whatever `work` took --
    a session's claim, above all -- is in the caller's hands before the cancel goes
    on, and the caller gives it back. Returning at the first cancel left the work
    running behind the caller, holding what it took with nobody left to release it.
    The cancel is handed back rather than raised so the caller can give that back
    first, and re-raise it after.
    """
    pending = asyncio.ensure_future(work)
    cancelled: asyncio.CancelledError | None = None
    while True:
        try:
            return await asyncio.shield(pending), cancelled
        except asyncio.CancelledError as exc:
            # The work itself cancelled -- the loop shutting down -- not its caller.
            if pending.cancelled():
                raise
            cancelled = exc
        except BaseException as failed:
            if cancelled is not None:
                raise cancelled from failed
            raise

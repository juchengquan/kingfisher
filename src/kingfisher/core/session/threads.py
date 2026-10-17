"""The threads kingfisher hands blocking work to, and waiting for that work to finish."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from kingfisher.core.config import DEFAULT_THREAD_POOL_SIZE, ConfigError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _Pool:
    """The process's one pool, made the first time a size is asked for."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.executor: ThreadPoolExecutor | None = None
        self.size: int | None = None


_POOL = _Pool()


def thread_pool(size: int | None = None) -> ThreadPoolExecutor:
    """The process's pool of `size` threads, or with no size the pool as it is, made at
    the default if nothing has asked yet.

    A second size is refused rather than ignored, or whichever service was built first
    would decide for every other.
    """
    if size is not None and size < 1:
        msg = f"KINGFISHER_THREAD_POOL_SIZE must be at least 1, got {size}"
        raise ConfigError(msg)
    with _POOL.lock:
        if _POOL.executor is None:
            made = DEFAULT_THREAD_POOL_SIZE if size is None else size
            _POOL.executor = ThreadPoolExecutor(max_workers=made, thread_name_prefix="kingfisher")
            _POOL.size = made
        elif size is not None and size != _POOL.size:
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

    Copied by hand, as `asyncio.to_thread` does: a bare `run_in_executor` starts `fn`
    in an empty context, dropping the caller's `RunnableConfig` and its tracing.
    """
    call = functools.partial(contextvars.copy_context().run, fn, *args, **kwargs)
    return asyncio.get_running_loop().run_in_executor(pool, call)


async def finished[T](work: Awaitable[T]) -> tuple[T, asyncio.CancelledError | None]:
    """What `work` produced once it has finished, and the cancel that arrived meanwhile.

    Waited for through every cancel, so whatever `work` took -- a session's claim, above
    all -- reaches the caller, and the cancel is handed back rather than raised so the
    caller can give that back first.
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

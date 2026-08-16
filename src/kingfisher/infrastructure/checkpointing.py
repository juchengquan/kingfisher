"""Thread persistence.

`BaseCheckpointSaver` is already the swappable interface, so this is a factory
rather than a wrapper — wrapping an existing protocol in a bespoke one can only
lose fidelity. A deployment that outgrows sqlite passes its own saver to
`Kingfisher(threads=...)`; nothing else changes, including the thread deletion
that `delete_session` and `reap` depend on.

Sqlite is configured for more than one process, because more than one is the
shape this is deployed in: process count follows concurrency, and every process
serving a workspace opens this same file. Left at its defaults it does not
survive that — measured, six processes against one fresh database and three of
them died in `setup()`, before serving anything.
"""

from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.sqlite import SqliteSaver

from kingfisher.config import Config

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.base import BaseCheckpointSaver

#: How long to wait for another process to finish writing before giving up.
#: Generous on purpose: the alternative to waiting is losing a turn's history,
#: and a checkpoint write is milliseconds, so a queue this deep never forms
#: unless something is badly wrong.
BUSY_TIMEOUT_MS = 30_000


def checkpoint_db_path(cfg: Config) -> Path:
    return cfg.state_dir / "threads.db"


def thread_ids(store: Any) -> tuple[str, ...] | None:
    """Every thread the store holds, or `None` when it cannot say.

    `ThreadStore` is "something that forgets a thread" and deliberately stays
    that narrow; enumerating is a janitor's need, not the domain's, so it is
    asked for here rather than widened into the port. A store that cannot
    enumerate yields `None`, which the caller reads as "cannot reconcile" and
    skips -- the same shape as `registered_tools` returning `()` for "cannot
    check". An injected double is the usual case, and a sweep must not fail
    because the thing it was handed does not answer this question.

    Through the saver's public `list`, not a `SELECT DISTINCT thread_id`.
    Direct SQL measured 411x faster on a real database -- under a millisecond
    against 175ms -- and was still the wrong trade: this runs on a janitor's
    schedule, never on a request, and the public call cannot be broken by an
    upstream schema change. The cost is that `list` deserialises every
    checkpoint, so the 175ms was for 1,894 of them and grows with the
    database. If that ever matters, it is a reason to page, not a reason to
    reach into the schema.
    """
    lister = getattr(store, "list", None)
    if lister is None:
        return None
    return tuple({item.config["configurable"]["thread_id"] for item in lister(None)})


def build_checkpointer(cfg: Config) -> BaseCheckpointSaver:
    """Open (creating if needed) the workspace's thread database.

    `check_same_thread=False` because LangGraph may touch the connection from
    worker threads; the sqlite file lives inside the workspace by default, so a
    project stays self-contained and copyable unless `KINGFISHER_STATE_DIR`
    deliberately moves it elsewhere.

    Two settings make it safe for several processes, and the order matters.

    `busy_timeout` first: without it a writer that finds the database locked
    fails immediately rather than waiting, which is what killed half the
    processes in the measurement above. It is set by pragma as well as by
    `timeout=` so that it is in force for the statement on the next line.

    Then WAL, which lets readers work while a writer holds the file — the
    default rollback journal blocks them. Setting it needs an exclusive lock,
    and the busy handler does not retry *that* particular refusal, so a process
    losing the race is expected rather than exceptional: journal mode is a
    property of the file, so whoever won has already set it for everyone.
    """
    db = checkpoint_db_path(cfg)
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db, check_same_thread=False, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # Suppressed, not ignored: another process is setting it right now, and
    # journal mode persists on the file, so theirs is ours.
    with suppress(sqlite3.OperationalError):
        conn.execute("PRAGMA journal_mode=WAL")

    saver = SqliteSaver(conn)
    saver.setup()
    return saver


@asynccontextmanager
async def async_checkpointer(cfg: Config) -> AsyncIterator[BaseCheckpointSaver]:
    """The same database, opened for an event loop.

    `SqliteSaver` raises `NotImplementedError` on `aget_tuple`, so `astream`
    needs this one -- a sync saver does not merely block the loop, it refuses.

    Every setting above applies here for the same reasons and in the same
    order: `busy_timeout` before WAL, because the pragma that sets journal mode
    needs an exclusive lock and the busy handler does not retry that particular
    refusal. Sync and async processes share the file quite happily; WAL is a
    property of the database, not of who opened it.

    A context manager, unlike its sync counterpart, because this holds an
    aiosqlite connection *and* the worker thread that serves it. Returning the
    saver alone left both to be collected whenever -- which in practice was
    after the loop had closed, and aiosqlite's thread then raised
    `RuntimeError: Event loop is closed` into an exit nobody could catch.

        async with async_checkpointer(cfg) as threads:
            service = Kingfisher(cfg, threads=threads)

    One connection serves every turn on the loop, and aiosqlite gives a
    connection one worker thread -- so checkpoint writes are serialised across
    concurrent turns. At a checkpoint per graph step and milliseconds per
    write, that is far below the model's time and does not show up in
    measurement. A deployment that outgrows it opens a connection per worker,
    or passes its own saver; `Kingfisher(threads=...)` takes any.
    """
    import aiosqlite  # noqa: PLC0415 -- only an async deployment pays for this
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: PLC0415

    db = checkpoint_db_path(cfg)
    db.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(db, timeout=BUSY_TIMEOUT_MS / 1000)
    await conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # Suppressed for the same reason as above: another process is setting it
    # right now, and journal mode persists on the file, so theirs is ours.
    with suppress(sqlite3.OperationalError):
        await conn.execute("PRAGMA journal_mode=WAL")

    saver = AsyncSqliteSaver(conn)
    await saver.setup()
    try:
        yield saver
    finally:
        await conn.close()

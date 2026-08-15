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
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from langgraph.checkpoint.sqlite import SqliteSaver

from kingfisher.config import Config

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver

#: How long to wait for another process to finish writing before giving up.
#: Generous on purpose: the alternative to waiting is losing a turn's history,
#: and a checkpoint write is milliseconds, so a queue this deep never forms
#: unless something is badly wrong.
BUSY_TIMEOUT_MS = 30_000


def checkpoint_db_path(cfg: Config) -> Path:
    return cfg.state_dir / "threads.db"


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

"""Thread persistence.

`BaseCheckpointSaver` is already the swappable interface, so this is a factory
rather than a wrapper — wrapping an existing protocol in a bespoke one can only
lose fidelity. Swapping to Postgres later changes this function and nothing
else, including the thread deletion the sweep depends on.
"""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from langgraph.checkpoint.sqlite import SqliteSaver

from kingfisher.domain.config import Config

if TYPE_CHECKING:
    from langgraph.checkpoint.base import BaseCheckpointSaver


def checkpoint_db_path(cfg: Config):
    return cfg.workspace / ".kingfisher" / "threads.db"


def build_checkpointer(cfg: Config) -> BaseCheckpointSaver:
    """Open (creating if needed) the workspace's thread database.

    `check_same_thread=False` because LangGraph may touch the connection from
    worker threads; the sqlite file lives inside the workspace so a project
    stays self-contained and copyable.
    """
    db = checkpoint_db_path(cfg)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver

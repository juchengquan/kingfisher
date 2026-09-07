"""Thread persistence: the conversation behind a session."""

from __future__ import annotations

from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from langgraph.checkpoint.memory import InMemorySaver

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from langgraph.checkpoint.base import BaseCheckpointSaver


def build_session_checkpointer(session_dir: Path) -> BaseCheckpointSaver:
    """The saver this turn runs on, which holds nothing after it."""
    del session_dir
    return InMemorySaver()


def thread_ids(store: Any) -> tuple[str, ...] | None:
    """Every thread the store holds, or `None` when it cannot say.

    Through the saver's public `list`, not a `SELECT DISTINCT thread_id`. Direct SQL
    measured 411x faster on a real database -- under a millisecond against 175ms --
    and was still the wrong trade: this runs on a janitor's schedule, never on a
    request, and the public call cannot be broken by an upstream schema change. The
    cost is that `list` deserialises every checkpoint, so that 175ms was for 1,894 of
    them and grows with the database. If it ever matters, that is a reason to page
    rather than to reach into the schema.
    """
    lister = getattr(store, "list", None)
    if lister is None:
        return None
    return tuple({item.config["configurable"]["thread_id"] for item in lister(None)})


@asynccontextmanager
async def async_session_checkpointer(session_dir: Path) -> AsyncIterator[BaseCheckpointSaver]:
    """The async twin, and there is now nothing asynchronous about it."""
    del session_dir
    yield InMemorySaver()


def release_checkpointer(saver: Any) -> None:
    """Close a saver this service opened. Safe to call on anything."""
    conn = getattr(saver, "conn", None)
    if conn is None:
        return
    with suppress(Exception):
        conn.close()

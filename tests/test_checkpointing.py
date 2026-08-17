"""Thread persistence, configured for the shape it is deployed in.

More than one process serves a workspace -- process count follows concurrency,
and each one opens this same file. Left at sqlite's defaults that does not
work: measured, six processes against one fresh database and three died inside
`setup()`, before serving anything. These pin the two settings that fixed it.
"""

from __future__ import annotations

from kingfisher.infrastructure.harness.checkpointing import BUSY_TIMEOUT_MS, build_checkpointer


def _pragma(saver, name: str):
    return saver.conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_a_writer_waits_for_another_process_instead_of_failing(cfg):
    """Without this a writer that finds the database locked gives up at once,
    which is what killed half the processes in the measurement above."""
    saver = build_checkpointer(cfg)

    assert _pragma(saver, "busy_timeout") == BUSY_TIMEOUT_MS


def test_readers_are_not_blocked_by_a_writer(cfg):
    """The default rollback journal blocks them; WAL does not. It is a property
    of the file, so one process setting it sets it for every process."""
    saver = build_checkpointer(cfg)

    assert _pragma(saver, "journal_mode").lower() == "wal"


def test_a_second_opener_inherits_the_journal_mode(cfg):
    """A process that loses the race to set WAL still gets WAL, because the
    winner set it on the file. Losing that race is expected, not exceptional."""
    build_checkpointer(cfg)

    assert _pragma(build_checkpointer(cfg), "journal_mode").lower() == "wal"


def test_opening_twice_is_safe(cfg):
    """`setup()` runs on every open, and two processes open the same fresh
    database at the same time."""
    first, second = build_checkpointer(cfg), build_checkpointer(cfg)

    assert first.conn is not second.conn

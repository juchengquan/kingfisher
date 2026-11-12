"""Each save kingfisher makes about a session on its own disk reaches the drive before the
next begins, so a power cut cannot take back both copies of a record, or a copy a record
names."""

from __future__ import annotations

import fcntl
import os
import sys
from pathlib import Path

from kingfisher.core.layout import HARNESS, LEDGER_FILE
from kingfisher.core.session import copying, durable, store
from kingfisher.core.session.durable import flush as full_flush
from kingfisher.core.session.steps import drive
from kingfisher.core.session.store import SECOND_COPY, HarnessFiles, unsealed
from kingfisher.plugins.sessions.backend import backend_at

OLD = b'{"turn": "req-1", "messages": 2}\n'
NEW = OLD + b'{"turn": "req-2", "messages": 4}\n'


def test_each_copy_of_a_record_is_on_the_drive_before_the_other_is_written(
    cfg, session_dir, monkeypatch
):
    """Upstream writes each copy in place and flushes neither, so a power cut moments after
    a save could empty both copies of the ledger together. Driven through kingfisher's own
    backend, whose `.harness` route has to answer where its files are for any flush at all."""
    harness = HarnessFiles(backend_at(cfg, session_dir))
    drive(harness.store(LEDGER_FILE, OLD))
    folder = session_dir / HARNESS
    held_at_each: list[tuple[bool, list[bytes | None]]] = []

    def recorded(path: Path, made: object = (), *, new: bool, full: bool = True) -> None:
        names = (LEDGER_FILE, f"{LEDGER_FILE}{SECOND_COPY}")
        copies = [unsealed((folder / name).read_bytes()) for name in names]
        held_at_each.append((full, copies))

    monkeypatch.setattr(store, "flushed", recorded)
    drive(harness.store(LEDGER_FILE, NEW))

    assert [full for full, _ in held_at_each] == [True, True]
    # The first flush comes while the other copy still holds the save before.
    assert set(held_at_each[0][1]) == {OLD, NEW}
    assert held_at_each[1][1] == [NEW, NEW]


def test_a_copy_made_on_this_host_is_flushed_with_the_folders_made_for_it(
    tmp_path, monkeypatch
):
    """A copy still in memory when the power goes can come back empty while the record
    naming it, flushed, survives, and a rewind then puts the empty file back as restored."""
    source = tmp_path / "report.md"
    source.write_bytes(b"turn twelve's version\n")
    target = tmp_path / "files" / "t12" / "report.md"
    calls = []
    monkeypatch.setattr(
        copying, "flushed", lambda path, made=(), **how: calls.append((path, list(made), how))
    )

    copying.copied_on_host((None, source), (None, target))

    assert calls == [(target, [target.parent, target.parent.parent], {"new": True, "full": False})]


def test_flushing_a_new_file_flushes_the_folder_entries_a_power_cut_would_take(
    tmp_path, monkeypatch
):
    """A new file's contents on the drive are lost all the same if the folder naming it is
    not: the record would read as never written."""
    target = tmp_path / "made" / "record.jsonl"
    made = durable.missing_folders(target.parent)
    target.parent.mkdir()
    target.write_bytes(b"{}\n")
    opened = []
    real_open = os.open

    def recorded(path: Path, *how: int) -> int:
        opened.append(Path(path))
        return real_open(path, *how)

    monkeypatch.setattr(durable.os, "open", recorded)

    durable.flushed(target, made, new=True)

    assert set(opened) == {tmp_path, target.parent, target}


def test_a_record_takes_the_full_flush_and_a_copy_the_plain_one(tmp_path, monkeypatch):
    """On macOS `fsync` stops at the drive's cache, which a power cut empties: a record
    flushed only that far loses both its copies with it."""
    path = tmp_path / "ledger.jsonl"
    path.write_bytes(b"{}\n")
    full: list[int] = []
    monkeypatch.setattr(durable, "flush", full.append)

    durable.flushed(path, new=False)
    durable.flushed(path, new=False, full=False)

    assert len(full) == 1


def test_a_drive_that_refuses_the_full_flush_still_gets_a_plain_one(tmp_path, monkeypatch):
    """A network share answers `F_FULLFSYNC` with an error, and raised, every save to a
    session kept there would fail its turn. The real flush by name, which the suite's own
    sessions are spared."""
    held = os.open(tmp_path / "record", os.O_RDWR | os.O_CREAT)
    plain = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(fcntl, "F_FULLFSYNC", 51, raising=False)

    def refused(*_: object) -> None:
        raise OSError(45, "Operation not supported")

    monkeypatch.setattr(fcntl, "fcntl", refused)
    monkeypatch.setattr(durable.os, "fsync", plain.append)
    try:
        full_flush(held)
    finally:
        os.close(held)

    assert plain == [held]

"""Each file kingfisher keeps about a session is kept twice, so a save cut short leaves one."""

from __future__ import annotations

import errno
import os
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, TurnIdError
from kingfisher.core.layout import HARNESS, LEDGER_FILE
from kingfisher.core.session.steps import drive
from kingfisher.core.session.store import HarnessFiles
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import an_agent, start, write_old_record
from tests.unit.session.test_turn_log import _say

OLD = b'{"turn": "req-1", "messages": 2}\n{"turn": "req-2", "messages": 4}\n'
NEW = OLD + b'{"turn": "req-3", "messages": 6}\n'


class _FullHalfway:
    """A file that takes half of what it is handed, then says the disk is full."""

    def __init__(self, handle: Any, cut: list[bool]) -> None:
        self._handle, self._cut = handle, cut

    def __enter__(self) -> _FullHalfway:
        return self

    def __exit__(self, *raised: object) -> None:
        self._handle.close()

    def write(self, data: bytes) -> int:
        self._handle.write(data[: len(data) // 2])
        self._cut.append(True)
        raise OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize("before", ["kept twice", "from before"])
@pytest.mark.parametrize("stopped", [0, 1], ids=["in-its-first-write", "in-its-second-write"])
def test_a_save_that_stops_partway_leaves_a_whole_copy(
    cfg, session_dir, monkeypatch, before, stopped
):
    """Upstream's backend empties a file and then fills it, and so may a deployment's: kept
    once, a save cut short, the process killed or the disk full, kept the ledger's front
    and lost older turns' rows, and a turn id the session held was taken again. Driven
    through the real backend, so the cut is the one it leaves; and from a file older than
    its second copy, which a save writing the first copy first would leave cut."""
    harness = HarnessFiles(backend_at(cfg, session_dir))
    if before == "kept twice":
        drive(harness.store(LEDGER_FILE, OLD))
    else:
        write_old_record(session_dir / HARNESS / LEDGER_FILE, OLD)
    opened, writes, cut = os.fdopen, [], []

    def fdopen(fd, mode="r", *args, **kwargs):
        handle = opened(fd, mode, *args, **kwargs)
        if "w" not in mode:
            return handle
        writes.append(fd)
        return _FullHalfway(handle, cut) if len(writes) - 1 == stopped else handle

    with monkeypatch.context() as patched:
        patched.setattr(os, "fdopen", fdopen)
        with pytest.raises(OSError, match="No space"):
            drive(harness.store(LEDGER_FILE, NEW))
    assert cut, "upstream no longer writes through os.fdopen"

    # Stopped in its first write, the save left what was there; in its second, what it wrote.
    assert drive(harness.fetch(LEDGER_FILE)) == (OLD if stopped == 0 else NEW)


@pytest.mark.parametrize(
    "damage",
    [
        lambda held: held[: held.index(b"\n") + 1],
        lambda held: held.replace(b"req-2", b"req-9", 1),
    ],
    ids=["cut-where-a-row-ends", "a-byte-changed-under-its-seal"],
)
def test_a_damaged_first_copy_still_refuses_a_turn_id_the_ledger_held(scripted, damage):
    """Kept once, a ledger cut where a row ends read as a shorter whole one, and a turn took
    `req-2` again though the turn log held it: three turns, the ledger cut to its first
    third. A copy whose bytes changed under an intact seal is no more whole than a cut one."""
    an_agent(scripted)
    start(scripted, "s")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    for turn_id in ("req-1", "req-2", "req-3"):
        _say(kf, "go on", AIMessage("ok"), turn_id=turn_id)
    ledger = scripted.workspace / "sessions" / "s" / HARNESS / LEDGER_FILE
    ledger.write_bytes(damage(ledger.read_bytes()))

    with pytest.raises(TurnIdError):
        _say(kf, "once more", AIMessage("ok"), turn_id="req-2")
    # The control: an id no turn took is still a new turn.
    _say(kf, "once more", AIMessage("ok"), turn_id="req-4")

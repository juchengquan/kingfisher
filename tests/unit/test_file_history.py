"""The files each turn left, copied under `.harness` so a session can be put back where any
turn left it, and the line a turn that says which."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.protocol import GlobResult
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, MemoryEntry, Request
from kingfisher.core.layout import ARTIFACTS_SEEN, FILE_HISTORY, HARNESS, KEPT_FILES
from kingfisher.core.session import file_history
from kingfisher.core.session.file_history import (
    HELD_BY_STORE,
    LATE,
    OVER_BUDGET,
    copied_on_host,
    dropped_for_space,
    folder_for,
)
from kingfisher.core.session.memory import without_any
from kingfisher.core.session.model import QuotaExceededError
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import WorkspaceScopedBackend
from tests.conftest import start
from tests.unit.test_memory_store import Shelf
from tests.unit.test_memory_store import _kingfisher as _with_store
from tests.unit.test_memory_store import _turn as _store_turn
from tests.unit.test_memory_writer import _turn as _writer_turn
from tests.unit.test_memory_writer import _writing
from tests.unit.test_run import StubAgent


def _session(cfg, session_id: str = "s") -> Path:
    return cfg.workspace / "sessions" / session_id


def _history(cfg, session_id: str = "s") -> dict[str, dict[str, Any]]:
    """Each turn's line, by its turn."""
    held = _session(cfg, session_id) / HARNESS / FILE_HISTORY
    lines = [json.loads(line) for line in held.read_text(encoding="utf-8").splitlines()]
    return {line["turn"]: line for line in lines}


def _copy(cfg, turn: str, path: str, session_id: str = "s") -> Path:
    return _session(cfg, session_id) / HARNESS / KEPT_FILES / folder_for(turn) / path


def _run(kf: Kingfisher, turn: str, session_id: str = "s") -> Any:
    return kf.run(Request("go", session_id=session_id, turn_id=turn))


def test_each_turn_keeps_the_version_it_left_of_what_changed_and_names_what_went(cfg):
    """A copy taken at the wrong moment, one turn's copy written over another's, or a removal
    left unsaid, is a rewind putting back files no turn left."""
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "report.txt").write_text("one", encoding="utf-8")
    (outputs / "notes.txt").write_text("notes", encoding="utf-8")
    (outputs / "old.txt").write_text("old", encoding="utf-8")
    _run(kf, "t1")

    (outputs / "report.txt").write_text("two!", encoding="utf-8")
    (outputs / "old.txt").unlink()
    _run(kf, "t2")

    lines = _history(cfg)
    assert set(lines["t1"]["kept"]) == {"outputs/report.txt", "outputs/notes.txt",
                                        "outputs/old.txt", "memory/AGENTS.md"}
    assert set(lines["t2"]["kept"]) == {"outputs/report.txt"}, "an unchanged file was copied"
    assert lines["t2"]["removed"] == ["outputs/old.txt"]
    assert lines["t2"]["kept"]["outputs/report.txt"]["bytes"] == len("two!")
    assert _copy(cfg, "t1", "outputs/report.txt").read_text(encoding="utf-8") == "one"
    assert _copy(cfg, "t2", "outputs/report.txt").read_text(encoding="utf-8") == "two!"
    assert not _copy(cfg, "t2", "outputs/notes.txt").exists()


def test_the_memory_file_is_kept_as_the_memory_writer_left_it(scripted):
    """Copied before the writer ran, a rewind would restore memory the turn never ended
    with."""
    kf = _writing(scripted, memory_every=1)

    _writer_turn(kf, AIMessage(content="noted"), AIMessage(content="- Likes EUR"), turn_id="t1")

    assert "Likes EUR" in _copy(scripted, "t1", "memory/AGENTS.md").read_text(encoding="utf-8")


def test_a_turn_past_its_deadline_copies_nothing_and_the_next_turn_does(cfg):
    """A turn out of time copying while its claim goes stale races the next turn, and a
    version it skipped that no later turn copied is lost to every rewind."""
    late = Kingfisher(replace(cfg, turn_timeout_s=0), graph=StubAgent("ok"),
                      sessions=LocalSessions())
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "report.txt").write_text("one", encoding="utf-8")

    _run(late, "t1")

    assert _history(cfg)["t1"]["not_kept"]["outputs/report.txt"]["why"] == LATE
    assert not (_session(cfg) / HARNESS / KEPT_FILES).exists()

    _run(Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions()), "t2")

    assert _copy(cfg, "t2", "outputs/report.txt").read_text(encoding="utf-8") == "one"


def test_the_memory_file_a_store_keyed_is_not_kept(scripted):
    """Kept, the user's memory would sit in the session's history, where the store's own
    forgetting never reaches."""
    store = Shelf(ana=(MemoryEntry(id="m1", text="Lives in Lisbon"),))
    kf = _with_store(scripted, store)
    start(scripted, "s")

    _store_turn(kf, AIMessage(content="done"))

    (line,) = _history(scripted).values()
    assert line["not_kept"]["memory/AGENTS.md"]["why"] == HELD_BY_STORE
    copies = _session(scripted) / HARNESS / KEPT_FILES
    assert not [p for p in copies.rglob("*") if p.is_file() and "Lisbon" in p.read_text()]


def test_over_budget_the_copy_restoring_only_the_oldest_turns_goes_first(cfg):
    """By age the notes written once in the first turn would go first though every rewind
    needs them, and a file larger than the whole budget would be copied only to be dropped."""
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    # Room for the memory file, the notes and the last two reports, of 10, 11 and 12 bytes:
    # a size of its own each, so no disk's clock can make two versions look alike.
    budget = (outputs.parent / "memory" / "AGENTS.md").stat().st_size + 5 + 11 + 12
    kf = Kingfisher(replace(cfg, rewind_max_bytes=budget), graph=StubAgent("ok"),
                    sessions=LocalSessions())
    (outputs / "notes.txt").write_text("n" * 5, encoding="utf-8")
    (outputs / "big.bin").write_bytes(b"b" * (budget + 1))
    for size, turn in enumerate(("t1", "t2", "t3"), start=10):
        (outputs / "report.txt").write_text("r" * size, encoding="utf-8")
        _run(kf, turn)

    lines = _history(cfg)
    assert lines["t1"]["kept"]["outputs/report.txt"]["dropped_in"] == "t3"
    assert not _copy(cfg, "t1", "outputs/report.txt").exists()
    assert _copy(cfg, "t1", "outputs/notes.txt").read_text(encoding="utf-8") == "n" * 5
    assert _copy(cfg, "t2", "outputs/report.txt").exists()
    assert lines["t1"]["not_kept"]["outputs/big.bin"]["why"] == OVER_BUDGET


def test_the_order_copies_are_dropped_in_follows_what_each_can_still_restore():
    """Missed, a removal or a version a later turn could not copy would leave a copy that
    restores nothing outliving one still needed.

    Driven against lines built here: no turn can be made to fail a copy on cue.
    """
    lines = [
        {"turn": "t1", "under": "t1", "kept": {
            "outputs/a": {"bytes": 1}, "outputs/b": {"bytes": 1}, "outputs/c": {"bytes": 1},
        }},
        {"turn": "t2", "under": "t2", "removed": ["outputs/b"]},
        {"turn": "t3", "under": "t3", "not_kept": {"outputs/c": {"why": LATE}}},
    ]

    assert dropped_for_space(lines, 0) == [(0, "outputs/b"), (0, "outputs/c"), (0, "outputs/a")]
    assert dropped_for_space(lines, 3) == [], "the control: within the budget, nothing goes"


def test_the_session_cap_does_not_count_the_copies(cfg):
    """Counted, a session rewriting one report a turn would be refused for its history
    alone, where `KINGFISHER_REWIND_MAX_BYTES` is what bounds that."""
    kf = Kingfisher(replace(cfg, session_max_bytes=15_000), graph=StubAgent("ok"),
                    sessions=LocalSessions())
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    for turn in ("t1", "t2"):
        (outputs / "report.bin").write_bytes(turn.encode() * 5_000)
        _run(kf, turn)

    _run(kf, "t3")

    # The control: the live session alone over the cap is still refused.
    (outputs / "more.bin").write_bytes(b"x" * 15_000)
    with pytest.raises(QuotaExceededError):
        _run(kf, "t4")


def test_forgetting_an_entry_takes_it_out_of_every_copy_of_the_memory_file(scripted):
    """Left in a copy, the next rewind brings back what the caller asked to be forgotten."""
    kf = _writing(scripted, memory_every=1)
    _writer_turn(kf, AIMessage(content="noted"), AIMessage(content="- Likes EUR"), turn_id="t1")
    (entry,) = kf.session("s").memory()

    kf.session("s").forget(entry.id)

    assert "Likes EUR" not in _copy(scripted, "t1", "memory/AGENTS.md").read_text(
        encoding="utf-8"
    )


def test_a_copy_loses_an_entry_by_its_id_and_by_what_it_says():
    """Matched by the id alone a copy keeps a line not yet numbered, and by the text alone an
    earlier wording, so a rewind brings either back."""
    copy = "# Memory\n\n- [m1] Likes the euro\n  for totals\n- Likes EUR\n- [m2] Uses Excel\n"

    without = without_any(copy, MemoryEntry(id="m1", text="Likes EUR"))

    assert without == "# Memory\n\n- [m2] Uses Excel\n"


def test_a_turn_s_folder_is_one_segment_no_other_turn_s_id_can_share():
    """A caller names its own turns: `../x` would leave the history's folder, and `T1`
    beside `t1` would share one on macOS's disk."""
    assert folder_for("t1a2b3c4") == "t1a2b3c4"
    named = {folder_for(turn) for turn in ("t1", "T1", "../x", "a/b", "_t1")}
    assert len(named) == 5
    assert all("/" not in one and ".." not in one for one in named)


class _Cut(WorkspaceScopedBackend):
    """A backend whose listing of `/outputs` comes back cut short."""

    def glob(self, pattern: str, path: str | None = None) -> Any:
        if path == "/outputs/":
            return GlobResult(matches=[], truncated=True)
        return super().glob(pattern, path)


class _CutSessions(LocalSessions):
    cut = False

    def open(self, cfg, session_id, /, *, catalogue=None):
        made = super().open(cfg, session_id, catalogue=catalogue)
        if self.cut:
            made.__class__ = _Cut
        return made


def test_a_listing_cut_short_records_nothing_rather_than_every_file_as_removed(cfg):
    """Every file the listing missed would be on the line as removed, and a rewind would
    delete it."""
    sessions = _CutSessions()
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=sessions)
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "report.txt").write_text("one", encoding="utf-8")
    _run(kf, "t1")
    seen = (_session(cfg) / HARNESS / ARTIFACTS_SEEN).read_bytes()

    sessions.cut = True
    _run(kf, "t2")

    assert set(_history(cfg)) == {"t1"}
    assert (_session(cfg) / HARNESS / ARTIFACTS_SEEN).read_bytes() == seen


# -- how a copy is made ----------------------------------------------------------


@pytest.mark.parametrize("clones", ["as this disk clones", "on a disk that makes none"])
def test_a_copy_on_this_host_keeps_what_the_file_held_when_its_source_changes(
    tmp_path, monkeypatch, clones
):
    """A copy sharing the file -- a link, not a clone -- changes when the agent rewrites it in
    place, and a disk refusing clones, as ext4 does, must still get a copy."""
    if clones != "as this disk clones":

        def refuse(source: int, target: Path) -> None:
            # As Linux's refusal does, which has made the file before the kernel refuses.
            target.touch()
            raise OSError(95, "Operation not supported", str(target))

        monkeypatch.setattr(file_history, "_clone", refuse)
    root = tmp_path.resolve()
    (root / "outputs").mkdir()
    source = root / "outputs" / "report.txt"
    source.write_bytes(b"as it was")
    target = root / ".harness" / "files" / "t1" / "outputs" / "report.txt"

    assert copied_on_host((root, source), (root / ".harness", target)) == len(b"as it was")
    with source.open("r+b") as rewrite:
        rewrite.write(b"rewritten")

    assert target.read_bytes() == b"as it was"


def test_a_copy_on_this_host_follows_no_link(tmp_path):
    """A link the shell left, read outside every fence and handed back by a rewind, carries
    out any file this process can read."""
    root = (tmp_path / "session").resolve()
    (root / "outputs").mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("not the agent's", encoding="utf-8")
    (root / "outputs" / "link.txt").symlink_to(secret)
    (root / "elsewhere").symlink_to(tmp_path, target_is_directory=True)
    target = root / ".harness" / "files" / "t1" / "outputs" / "link.txt"

    for linked in (root / "outputs" / "link.txt", root / "elsewhere" / "secret.txt"):
        with pytest.raises(OSError, match="link"):
            copied_on_host((root, linked), (root / ".harness", target))
    # Named by a backend's own `host_path`, with no root to judge it against: the file
    # itself being a link is still refused.
    with pytest.raises(OSError):
        copied_on_host((None, root / "outputs" / "link.txt"), (None, target))
    assert not target.exists()


class _Remote(WorkspaceScopedBackend):
    """A backend that names no host path, as one keeping its files elsewhere."""

    copied: list[tuple[str, str]]

    def host_path(self, virtual: str) -> None:
        return None


class _RemoteCopying(_Remote):
    """And copies a file itself."""

    def copy(self, source: str, destination: str) -> str | None:
        self.copied.append((source, destination))
        (got,) = self.download_files([source])
        if got.content is None:
            return got.error
        (put,) = self.upload_files([(destination, got.content)])
        return put.error


class _RemoteSessions(LocalSessions):
    def __init__(self, kind: type[_Remote]) -> None:
        super().__init__()
        self.kind = kind
        self.copied: list[tuple[str, str]] = []

    def open(self, cfg, session_id, /, *, catalogue=None):
        made = super().open(cfg, session_id, catalogue=catalogue)
        made.__class__ = self.kind
        made.copied = self.copied
        return made


@pytest.mark.parametrize("kind", [_RemoteCopying, _Remote], ids=["its own copy", "neither"])
def test_a_backend_naming_no_host_path_keeps_copies_by_its_own_copy_or_whole(cfg, kind):
    """A backend elsewhere kept no history, or one declaring `copy` was read and written
    whole through this process, which holds the file in memory."""
    sessions = _RemoteSessions(kind)
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=sessions)
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "report.txt").write_text("one", encoding="utf-8")

    _run(kf, "t1")

    assert _copy(cfg, "t1", "outputs/report.txt").read_text(encoding="utf-8") == "one"
    asked = {source for source, _ in sessions.copied}
    assert asked == ({"/outputs/report.txt", "/memory/AGENTS.md"} if kind is _RemoteCopying
                     else set())


def test_a_failed_copy_is_logged_without_the_file_or_the_backends_words(cfg, caplog):
    """A file's name is what the agent called it, and a backend's error may quote it: both
    reached the log when a copy failed, and a log reaches people the session never did."""

    class Failing(_Remote):
        def host_path(self, virtual: str) -> None:
            msg = f"no host for {virtual}"
            raise RuntimeError(msg)

        def copy(self, source: str, destination: str) -> str | None:
            msg = f"cannot copy {source}"
            raise OSError(msg)

    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=_RemoteSessions(Failing))
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "jane-doe-4471.txt").write_text("one", encoding="utf-8")

    with caplog.at_level(logging.WARNING, logger="kingfisher"):
        _run(kf, "t1")

    assert "jane-doe" not in caplog.text, "the file's name reached the log"
    assert "Failing.host_path raised RuntimeError at" in caplog.text
    assert "turn t1 kept no copy of a file under /outputs" in caplog.text
    # The control: the session's own record still says which file and why.
    assert "jane-doe" in _history(cfg)["t1"]["not_kept"]["outputs/jane-doe-4471.txt"]["why"]


def test_a_file_the_backend_cannot_copy_is_recorded_with_why(cfg):
    """A copy that failed recorded as kept is a rewind restoring a file from nothing."""

    class Refusing(_Remote):
        def copy(self, source: str, destination: str) -> str | None:
            return "the store is down"

    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=_RemoteSessions(Refusing))
    outputs = _session(cfg, start(cfg, "s")) / "outputs"
    (outputs / "report.txt").write_text("one", encoding="utf-8")

    _run(kf, "t1")

    line = _history(cfg)["t1"]
    assert "kept" not in line
    assert line["not_kept"]["outputs/report.txt"]["why"] == "the store is down"

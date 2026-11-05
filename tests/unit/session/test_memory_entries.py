"""The session's memory file as entries: numbered by kingfisher, and what a turn did reported."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, MemoryEntry, Request
from kingfisher.core.session.memory import changes, numbered
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted

# -- the file as entries -----------------------------------------------------


def test_an_unnumbered_line_is_numbered_after_the_highest_id():
    """Numbered from one, a line added under `[m4]` would take `m1` and could later meet
    an `m1` written back by a store or a hand edit."""
    text = "# Project memory\n\n- [m4] Short answers\n- Likes EUR\n"

    written, entries = numbered(text)

    assert written == "# Project memory\n\n- [m4] Short answers\n- [m5] Likes EUR\n"
    assert entries == (MemoryEntry("m4", "Short answers"), MemoryEntry("m5", "Likes EUR"))


def test_numbering_twice_changes_nothing():
    """A turn numbers at its start and again at its end; a second pass that moved ids
    would report every entry as removed and added."""
    once, _ = numbered("- Likes EUR\n- [m2] Short answers\n  even shorter on Fridays\n")

    assert numbered(once)[0] == once


def test_an_indented_line_continues_the_entry_above_it():
    _, entries = numbered("- [m1] Short answers\n  even shorter on Fridays\n")

    assert entries == (MemoryEntry("m1", "Short answers even shorter on Fridays"),)


def test_an_id_written_twice_keeps_the_first_and_numbers_the_second():
    """An agent copying a line would otherwise leave two entries one `forget` could not
    tell apart."""
    written, entries = numbered("- [m1] Likes EUR\n- [m1] Likes GBP\n")

    assert written == "- [m1] Likes EUR\n- [m2] Likes GBP\n"
    assert [one.id for one in entries] == ["m1", "m2"]


def test_headings_and_prose_are_kept_and_are_not_entries():
    text = "# Project memory\n\nNotes the agent keeps.\n\n## Conventions\n- Likes EUR\n"

    written, entries = numbered(text)

    assert written.startswith("# Project memory\n\nNotes the agent keeps.\n\n## Conventions\n")
    assert entries == (MemoryEntry("m1", "Likes EUR"),)


def test_changes_are_read_by_id():
    before = (MemoryEntry("m1", "Likes EUR"), MemoryEntry("m2", "Short answers"))
    after = (MemoryEntry("m1", "Likes EUR, rounded"), MemoryEntry("m3", "Reviews on Mondays"))

    found = changes(before, after)

    assert found.added == (MemoryEntry("m3", "Reviews on Mondays"),)
    assert found.changed == (MemoryEntry("m1", "Likes EUR, rounded"),)
    assert found.removed == (MemoryEntry("m2", "Short answers"),)
    assert found.said() == "added m3; changed m1; removed m2"


# -- what a turn reports -----------------------------------------------------


def _remembering(scripted) -> Kingfisher:
    an_agent(scripted, **EVERY_TOOL)
    return Kingfisher(replace(scripted, memory_enabled=True), sessions=LocalSessions())


def _edits_memory(old: str, new: str) -> AIMessage:
    args = {"file_path": "/memory/AGENTS.md", "old_string": old, "new_string": new}
    return AIMessage(content="", tool_calls=[{"name": "edit_file", "args": args, "id": "c1"}])


def _memory_file(scripted, session: str = "s"):
    return scripted.workspace / "sessions" / session / "memory" / "AGENTS.md"


def _turn(kf: Kingfisher, *script: Any) -> tuple[Any, list[Any]]:
    Scripted.script.extend(script)
    events = list(kf.stream(Request("go", agent="only", session_id="s")))
    (finished,) = [one for one in events if one.kind == "finished"]
    return finished.result, events


def test_an_entry_the_agent_adds_is_numbered_and_reported(scripted):
    """Driven through a turn on kingfisher's own backend: the agent's edit is what the
    turn's end finds, numbers, writes back and reports."""
    kf = _remembering(scripted)
    start(scripted, "s")

    result, events = _turn(
        kf,
        _edits_memory("# Project memory\n", "# Project memory\n\n- Likes EUR\n"),
        AIMessage(content="noted"),
    )

    assert result.memory.added == (MemoryEntry("m1", "Likes EUR"),)
    assert [one.text for one in events if one.kind == "memory_updated"] == ["added m1"]
    assert "- [m1] Likes EUR" in _memory_file(scripted).read_text(encoding="utf-8")


def test_a_line_written_before_the_turn_is_numbered_at_its_start_and_not_reported(scripted):
    """Numbered only at the end, a line already there would read as added by this turn."""
    kf = _remembering(scripted)
    start(scripted, "s")
    memory = _memory_file(scripted)
    memory.write_text("# Project memory\n\n- Short answers\n", encoding="utf-8")

    result, events = _turn(kf, AIMessage(content="hello"))

    assert not result.memory
    assert not [one for one in events if one.kind == "memory_updated"]
    assert memory.read_text(encoding="utf-8") == "# Project memory\n\n- [m1] Short answers\n"


def test_a_turn_that_leaves_memory_alone_reports_nothing(scripted):
    kf = _remembering(scripted)
    start(scripted, "s")

    result, events = _turn(kf, AIMessage(content="hello"))

    assert not result.memory
    assert not [one for one in events if one.kind == "memory_updated"]


def test_with_memory_off_the_file_is_neither_read_nor_numbered(scripted):
    """Off is off: the file a deployment switched memory away from is not rewritten."""
    an_agent(scripted, **EVERY_TOOL)
    kf = Kingfisher(scripted, sessions=LocalSessions())
    start(scripted, "s")
    memory = _memory_file(scripted)
    memory.write_text("- Short answers\n", encoding="utf-8")

    result, _ = _turn(kf, AIMessage(content="hello"))

    assert not result.memory
    assert memory.read_text(encoding="utf-8") == "- Short answers\n"

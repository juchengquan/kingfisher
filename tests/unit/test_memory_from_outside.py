"""A session's memory read and forgotten by a caller outside any turn: read without being
rewritten, and one entry removed under the session's claim."""

from __future__ import annotations

import asyncio

import pytest
from deepagents.backends.protocol import FileDownloadResponse

from kingfisher import Kingfisher, MemoryEntry, MemoryEntryError, SessionBusyError
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.core.session.memory import memory_entries, numbered, without
from kingfisher.core.session.steps import drive
from kingfisher.plugins import LocalSessions
from tests.conftest import start

MEMORY = (
    "# Project memory\n"
    "\n"
    "Notes the agent keeps.\n"
    "\n"
    "- [m1] Reports figures in EUR\n"
    "  rounded to the nearest thousand\n"
    "- [m2] Short answers\n"
    "## Conventions\n"
    "- [m3] Reviews on Mondays\n"
)

# -- one entry out of the text -----------------------------------------------


def test_forgetting_drops_the_entry_and_its_continuation_and_nothing_else():
    """A continuation line left behind would join the entry above it, so forgetting `m1`
    would quietly reword whatever came before."""
    assert without(MEMORY, "m1") == (
        "# Project memory\n"
        "\n"
        "Notes the agent keeps.\n"
        "\n"
        "- [m2] Short answers\n"
        "## Conventions\n"
        "- [m3] Reviews on Mondays\n"
    )


def test_what_is_left_is_every_other_entry_as_it_was_read():
    """Forgetting by a continuation rule of its own would take lines `numbered` gives a
    neighbour, or leave ones it gives the forgotten entry -- here, an indented line after
    a heading, which `numbered` still counts as the entry above."""
    text = (
        "- [m1] Short answers\n  even shorter\n"
        "- [m2] Likes EUR\n## Later\n  rounded\n- [m3] Reviews on Mondays\n"
    )
    _, before = numbered(text)

    for one in before:
        _, after = numbered(without(text, one.id))
        assert after == tuple(entry for entry in before if entry.id != one.id)


def test_an_unnumbered_line_is_forgotten_by_the_id_a_reading_gave_it():
    """`memory()` numbers without writing, so the id a caller was shown may not be on disk
    yet; forgetting must find it by the same numbering, not by what the file says."""
    text = "- Likes EUR\n- Short answers\n"

    assert [one.id for one in numbered(text)[1]] == ["m1", "m2"]
    assert without(text, "m1") == "- [m2] Short answers\n"


def test_an_id_not_there_is_refused_naming_those_that_are():
    """Ignored, a mistyped id would look like a forget that worked."""
    with pytest.raises(MemoryEntryError, match=r"'m9'.*m1, m2, m3"):
        without(MEMORY, "m9")
    with pytest.raises(MemoryEntryError, match="holds none"):
        without("# Project memory\n", "m1")


# -- through a session ---------------------------------------------------------


def _memory_file(cfg, session: str = "s"):
    return cfg.workspace / "sessions" / session / "memory" / "AGENTS.md"


def _remembering(cfg, text: str = MEMORY):
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, "s")
    _memory_file(cfg).write_text(text, encoding="utf-8")
    handle = kf.session("s")
    assert handle is not None
    return kf, handle


def _call(handle, way: str, method: str, *args):
    if way == "async":
        return asyncio.run(getattr(handle, f"a{method}")(*args))
    return getattr(handle, method)(*args)


def test_memory_is_the_entries_and_reading_writes_nothing(cfg, way):
    """Written back, a read taken without the claim could land over a turn's edit; it
    numbers an unnumbered line in what it answers and leaves the file alone."""
    text = MEMORY + "- Prefers tables\n"
    _, handle = _remembering(cfg, text)

    assert _call(handle, way, "memory") == (
        MemoryEntry("m1", "Reports figures in EUR rounded to the nearest thousand"),
        MemoryEntry("m2", "Short answers"),
        MemoryEntry("m3", "Reviews on Mondays"),
        MemoryEntry("m4", "Prefers tables"),
    )
    assert _memory_file(cfg).read_text(encoding="utf-8") == text


def test_no_memory_file_is_no_entries():
    """An agent may delete the file; reading it then is an empty memory, not an error."""

    class Empty:
        def download_files(self, paths):
            return [FileDownloadResponse(path=p, error="file_not_found") for p in paths]

    assert drive(memory_entries(Empty(), DEFAULT_LAYOUT)) == ()


def test_forget_writes_the_file_back_without_that_entry(cfg, way):
    """Through kingfisher's own backend, so the write lands where a turn reads it."""
    _, handle = _remembering(cfg)

    _call(handle, way, "forget", "m1")

    assert _memory_file(cfg).read_text(encoding="utf-8") == without(MEMORY, "m1")
    assert [one.id for one in _call(handle, way, "memory")] == ["m2", "m3"]


def test_forget_gives_the_claim_back_whether_or_not_it_found_the_entry(cfg, way):
    """A claim kept would refuse every later turn in the session until it went stale."""
    _, handle = _remembering(cfg)
    sessions = LocalSessions()

    _call(handle, way, "forget", "m1")
    with pytest.raises(MemoryEntryError, match=r"m2, m3"):
        _call(handle, way, "forget", "m1")

    assert not sessions.held(cfg, "s", stale_after=cfg.claim_stale_after)
    assert _memory_file(cfg).read_text(encoding="utf-8") == without(MEMORY, "m1")


def test_forget_is_refused_while_a_turn_holds_the_session(cfg, way):
    """Written under a running turn, the forget would be reported as the turn's own
    removal, and an agent saving memory from the copy it loaded would bring the entry back."""
    _, handle = _remembering(cfg)
    sessions = LocalSessions()
    token = sessions.claim(cfg, "s", stale_after=cfg.claim_stale_after)
    assert token is not None

    with pytest.raises(SessionBusyError):
        _call(handle, way, "forget", "m1")

    assert _memory_file(cfg).read_text(encoding="utf-8") == MEMORY
    assert sessions.held(cfg, "s", stale_after=cfg.claim_stale_after), (
        "the refusal let go of the turn's claim"
    )
    sessions.release(cfg, "s", token)
    _call(handle, way, "forget", "m1")
    assert _memory_file(cfg).read_text(encoding="utf-8") == without(MEMORY, "m1")

"""What becomes of a saved tool result no ledger row names: handed to an `UnusedResults`
at the end of the turn, and removed by kingfisher's own.

Through real turns, a scripted model and kingfisher's own sessions, because which files
are handed over is decided by what the turn wrote and the ledger it left.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.layout import DEFAULT_LAYOUT, SCRATCH, TOOL_RESULTS, TRANSCRIPT_FILE
from kingfisher.plugins import (
    DeleteUnusedResults,
    LocalSessions,
    Plugins,
    UnusedFile,
    UnusedFiles,
)
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted

ROUTE = DEFAULT_LAYOUT.tool_results_route

TOOLS = '''
def web_search(query: str) -> str:
    """Search the web."""
    return f"Ten results for {query}"


TOOLS = [web_search]
'''

#: What a turn that died before writing its rows leaves: named like a result, no row.
STRAY = "A-0007-web_search.txt"
LEFT = b"from a turn that died"


@pytest.fixture
def workspace(scripted):
    """One agent with `web_search`, a session `s`, and a stray result in it."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    folder = _folder(scripted)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / STRAY).write_bytes(LEFT)
    return scripted


def _folder(cfg):
    return cfg.workspace / "sessions" / "s" / SCRATCH / TOOL_RESULTS


def _kept(cfg) -> list[str]:
    return sorted(one.name for one in _folder(cfg).iterdir())


def _searched() -> list[AIMessage]:
    return [
        AIMessage(
            content="",
            tool_calls=[{"name": "web_search", "args": {"query": "EV"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]


def _turn(kf: Kingfisher, *script: AIMessage, awaited: bool = False):
    Scripted.script.extend(script or [AIMessage(content="done")])
    request = Request("go", agent="only", session_id="s")
    return asyncio.run(kf.arun(request)) if awaited else kf.run(request)


class Keeping:
    """Removes nothing, and keeps what each turn handed it."""

    def __init__(self) -> None:
        self.handed: list[list[tuple[str, int | None, bytes]]] = []
        self.sessions: list[str] = []

    def settle(self, found: UnusedFiles) -> None:
        self.handed.append([(one.path, one.size, one.read()) for one in found])
        self.sessions.append(found.session_id)


def test_the_default_removes_exactly_what_the_sweep_removed(workspace):
    """A default that removed less leaves dead turns' files forever; more, and it takes
    the agent's own file or one a row names."""
    folder = _folder(workspace)
    (folder / "notes.md").write_text("the agent's", encoding="utf-8")
    (folder / "A-0009-dir").mkdir()
    (folder / "A-0009-dir" / "inside.txt").write_text("x", encoding="utf-8")

    result = _turn(Kingfisher(workspace, sessions=LocalSessions()), *_searched())

    assert result.answer == "done"
    assert _kept(workspace) == ["A-0001-web_search.txt", "A-0009-dir", "notes.md"]
    assert (folder / "A-0009-dir" / "inside.txt").exists()


def test_a_plugin_is_handed_only_the_files_safe_to_drop(workspace):
    """Never a file a row names, this turn's or an earlier one's, never one the agent
    wrote under a name of its own, and never a directory."""
    folder = _folder(workspace)
    (folder / "notes.md").write_text("the agent's", encoding="utf-8")
    (folder / "A-0009-dir").mkdir()
    keeping = Keeping()
    kf = Kingfisher(workspace, sessions=LocalSessions(), unused_results=keeping)

    _turn(kf, *_searched())
    _turn(kf, *_searched())

    stray = (f"{ROUTE}{STRAY}", len(LEFT), LEFT)
    assert keeping.handed == [[stray], [stray]], "the stray was not handed over alone"
    assert keeping.sessions == ["s", "s"], "an archive could not tell whose files these are"
    assert _kept(workspace) == [
        "A-0001-web_search.txt", "A-0002-web_search.txt", STRAY, "A-0009-dir", "notes.md",
    ]


def test_a_turn_with_nothing_unused_does_not_ask(workspace):
    """Asked with nothing, a plugin that does slow setup per call pays it every turn."""
    keeping = Keeping()
    kf = Kingfisher(workspace, sessions=LocalSessions(), unused_results=keeping)
    (_folder(workspace) / STRAY).unlink()

    _turn(kf, *_searched())

    assert keeping.handed == []


class Reaching:
    """Tries to reach past what it was handed, and records what happened."""

    def __init__(self) -> None:
        self.refused: list[str] = []
        self.offered: tuple[set[str], set[str]] = (set(), set())

    def settle(self, found: UnusedFiles) -> None:
        (one,) = found
        self.offered = (_public(found), _public(one))
        elsewhere = f"/.harness/{TRANSCRIPT_FILE}"
        for attempt in (
            lambda: replace(one, path=elsewhere).remove(),
            lambda: replace(one, path=elsewhere).read(),
            lambda: UnusedFile(elsewhere, 0, found).remove(),
        ):
            try:
                attempt()
            except ValueError as refused:
                self.refused.append(str(refused))


def _public(thing: Any) -> set[str]:
    return {name for name in dir(thing) if not name.startswith("_")}


def test_the_files_handed_over_reach_no_other_path(workspace):
    """A helper that took any path would hand a plugin the whole session, its ledger and
    transcript included, to remove."""
    reaching = Reaching()
    kf = Kingfisher(workspace, sessions=LocalSessions(), unused_results=reaching)

    _turn(kf, *_searched())

    assert len(reaching.refused) == 3, reaching.refused
    assert all("is not one of the unused results" in said for said in reaching.refused)
    assert (workspace.workspace / "sessions" / "s" / ".harness" / TRANSCRIPT_FILE).exists()
    assert reaching.offered == ({"session_id"}, {"path", "size", "read", "remove"})


class Raising:
    def settle(self, found: UnusedFiles) -> None:
        msg = "the archive is down"
        raise RuntimeError(msg)


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_plugin_that_raises_is_logged_and_the_turn_keeps_its_answer(
    workspace, caplog, awaited
):
    """The turn has run: failing it over files the next turn offers again would lose an
    answer the conversation already holds."""
    kf = Kingfisher(workspace, sessions=LocalSessions(), unused_results=Raising())

    with caplog.at_level(logging.WARNING):
        result = _turn(kf, *_searched(), awaited=awaited)

    assert (result.answer, result.completed) == ("done", True)
    assert "session s" in caplog.text
    assert "1 of 1 file(s) left" in caplog.text
    assert "the archive is down" in caplog.text
    assert STRAY in _kept(workspace)


class Twice:
    """Removes each file twice: the second finds it gone."""

    def settle(self, found: UnusedFiles) -> None:
        for one in found:
            one.remove()
            one.remove()


def test_removing_a_file_already_gone_is_not_an_error(workspace, caplog):
    """A plugin retrying after a partial failure removes a file a second time."""
    kf = Kingfisher(workspace, sessions=LocalSessions(), unused_results=Twice())

    with caplog.at_level(logging.WARNING):
        _turn(kf, *_searched())

    assert STRAY not in _kept(workspace)
    assert "unused results" not in caplog.text


class Watching:
    """Records, while settling, whether the session was still claimed and which thread
    it was asked on."""

    def __init__(self, sessions: LocalSessions, cfg: Any) -> None:
        self.sessions, self.cfg = sessions, cfg
        self.held: bool | None = None
        self.on_a_loop: bool | None = None

    def settle(self, found: UnusedFiles) -> None:
        self.held = self.sessions.held(self.cfg, "s", stale_after=3600)
        try:
            asyncio.get_running_loop()
            self.on_a_loop = True
        except RuntimeError:
            self.on_a_loop = False
        DeleteUnusedResults().settle(found)


@pytest.mark.parametrize("awaited", [False, True], ids=["run", "arun"])
def test_a_plugin_is_asked_while_the_session_is_still_claimed(workspace, awaited):
    """Asked after the claim is let go, a turn taking the session over could write a file
    under an id a crash reused, and the late plugin remove it. On the async path off the
    event loop too, since a plugin is sync and may block."""
    sessions = LocalSessions()
    watching = Watching(sessions, workspace)
    kf = Kingfisher(workspace, sessions=sessions, unused_results=watching)

    _turn(kf, *_searched(), awaited=awaited)

    assert watching.held is True, "the claim was let go before the plugin was asked"
    assert watching.on_a_loop is False, "the plugin ran on the event loop"
    assert STRAY not in _kept(workspace)


def test_the_command_hands_the_plugin_named_in_its_plugins_on(workspace):
    """A deployment that archives its results had every command turn delete them, with
    nothing to say the setting did not reach."""
    keeping = Keeping()
    wiring = Plugins(unused_results=keeping).keywords()

    _turn(Kingfisher(workspace, sessions=LocalSessions(), **wiring), *_searched())

    assert [[path for path, _, _ in handed] for handed in keeping.handed] == [
        [f"{ROUTE}{STRAY}"]
    ]
    assert STRAY in _kept(workspace)

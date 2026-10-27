"""Delegations side by side, as the command shows them: each whole, as it finishes."""

from __future__ import annotations

import io
from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from kingfisher import Kingfisher, Request
from kingfisher.cli.progress import show
from kingfisher.core.result import RunEvent, RunResult
from kingfisher.plugins import LocalSessions
from tests.unit.test_stream_delegations import SAID, _twice

if TYPE_CHECKING:
    from collections.abc import Iterator


def _render(events: list[RunEvent]) -> str:
    """One stream, where the blocks and the answer meet."""
    out = io.StringIO()
    show(iter(events), out)
    return out.getvalue()


def _start(agent: str, asked: str, *delegations: str) -> RunEvent:
    return RunEvent(kind="delegation_start", agent=agent, text=asked, delegations=delegations)


def _said(agent: str | None, text: str, *delegations: str) -> RunEvent:
    return RunEvent(kind="token", agent=agent, text=text, delegations=delegations)


def _answered(made: str, text: str, *delegations: str, agent: str | None = None) -> RunEvent:
    return RunEvent(
        kind="tool_result", tool="task", call=made, text=text, agent=agent, delegations=delegations
    )


def _finished() -> RunEvent:
    return RunEvent(kind="finished", result=RunResult(session_id="s", turn_id="t", answer="done"))


#: Two runs of `reviewer` at once, word by word, the second finishing first.
SIDE_BY_SIDE = [
    RunEvent(kind="model_call", tools=("task", "task"), calls=("t1", "t2")),
    _start("reviewer", "check the draft", "t1"),
    _start("reviewer", "check the tests\nand the fixtures", "t2"),
    _said("reviewer", "a0 ", "t1"),
    _said("reviewer", "b0 ", "t2"),
    RunEvent(kind="model_call", agent="reviewer", tools=("read_file",), delegations=("t1",)),
    _said("reviewer", "a1", "t1"),
    _said("reviewer", "b1", "t2"),
    _answered("t2", "B"),
    _answered("t1", "A"),
    _said(None, "done"),
    _finished(),
]


def test_two_delegations_side_by_side_print_whole_as_each_finishes():
    """They printed merged word by word, `b0a0  b1a1`; and in finish order, so the one
    done first is not kept waiting behind the one called first."""
    assert _render(SIDE_BY_SIDE).splitlines() == [
        "[model] → task, task  (in=0 cached=0)",
        "[reviewer] check the tests",
        "b0 b1",
        "[tool ] task: B",
        "[reviewer] check the draft",
        "a0 ",
        "[model:reviewer] → read_file  (in=0 cached=0)",
        "a1",
        "[tool ] task: A",
        "[main]",
        "done",
    ]


def test_a_lone_delegation_is_shown_exactly_as_before():
    """The common case, and the one a change here could quietly make worse: the same
    events with no delegation said of them render byte for byte the same."""
    events = [
        RunEvent(kind="model_call", tools=("task",), calls=("t1",)),
        _start("reviewer", "check the draft", "t1"),
        _said("reviewer", "a0 ", "t1"),
        RunEvent(kind="model_call", agent="reviewer", tools=("read_file",), delegations=("t1",)),
        _said("reviewer", "a1", "t1"),
        _answered("t1", "A"),
        _said(None, "done"),
        _finished(),
    ]
    before = [replace(e, delegations=()) for e in events if e.kind != "delegation_start"]

    assert _render(events) == _render(before)
    assert "check the draft" not in _render(events)


def test_a_lone_subagent_consulting_a_lone_helper_is_shown_as_before():
    """A helper is open beside its subagent, not beside a sibling: counted as one, the
    usual reviewer-asks-for-a-second-opinion run would print as a block at the end."""
    events = [
        _start("reviewer", "check the draft", "t1"),
        _said("reviewer", "asking ", "t1"),
        _start("second-opinion", "again", "t1", "h1"),
        _said("second-opinion", "agreed", "t1", "h1"),
        _answered("h1", "agreed", "t1", agent="reviewer"),
        _said("reviewer", "done checking", "t1"),
        _answered("t1", "A"),
    ]
    before = [replace(e, delegations=()) for e in events if e.kind != "delegation_start"]

    assert _render(events) == _render(before)


def test_helpers_side_by_side_are_blocks_inside_their_subagents_block():
    """The same rule one level down, so two helpers of one held reviewer do not
    interleave inside its block."""
    text = _render(
        [
            _start("reviewer", "check one", "t1"),
            _start("reviewer", "check two", "t2"),
            _start("second-opinion", "ask one", "t1", "h1"),
            _start("second-opinion", "ask two", "t1", "h2"),
            _said("second-opinion", "x", "t1", "h1"),
            _said("second-opinion", "y", "t1", "h2"),
            _said("reviewer", "r2 says", "t2"),
            _answered("h2", "Y", "t1", agent="reviewer"),
            _answered("h1", "X", "t1", agent="reviewer"),
            _said("reviewer", "r1 says", "t1"),
            _answered("t1", "R1"),
            _answered("t2", "R2"),
        ]
    )

    assert text.splitlines() == [
        "[reviewer] check one",
        "[second-opinion] ask two",
        "y",
        "[tool :reviewer] task: Y",
        "[second-opinion] ask one",
        "x",
        "[tool :reviewer] task: X",
        "[reviewer]",
        "r1 says",
        "[tool ] task: R1",
        "[reviewer] check two",
        "r2 says",
        "[tool ] task: R2",
    ]


def test_a_turn_that_stops_with_delegations_open_shows_what_they_said():
    """Held output dropped at a gate or a deadline would be work the reader never saw;
    it comes before the line saying why the turn stopped."""
    text = _render(
        [
            _start("reviewer", "check the draft", "t1"),
            _start("reviewer", "check the tests", "t2"),
            _said("reviewer", "a0", "t1"),
            _said("reviewer", "b0", "t2"),
            RunEvent(kind="cut_short", text="turn stopped after 5s"),
            _finished(),
        ]
    )

    assert text.splitlines()[:7] == [
        "[reviewer] check the draft",
        "a0",
        "(unfinished)",
        "[reviewer] check the tests",
        "b0",
        "(unfinished)",
        "[cut_short] turn stopped after 5s",
    ]


def test_a_helper_left_open_is_shown_inside_its_subagents_block():
    """Shown outermost first, the subagent's block would be gone by the time its
    helpers' came to land in it, and they would follow it to the terminal."""
    text = _render(
        [
            _start("reviewer", "check one", "t1"),
            _start("reviewer", "check two", "t2"),
            _start("second-opinion", "ask one", "t1", "h1"),
            _start("second-opinion", "ask two", "t1", "h2"),
            _said("second-opinion", "x", "t1", "h1"),
            RunEvent(kind="cut_short", text="turn stopped after 5s"),
        ]
    )

    assert text.splitlines() == [
        "[reviewer] check one",
        "[second-opinion] ask one",
        "x",
        "(unfinished)",
        "[second-opinion] ask two",
        "(unfinished)",
        "(unfinished)",
        "[reviewer] check two",
        "(unfinished)",
        "[cut_short] turn stopped after 5s",
    ]


def test_a_stream_that_raises_still_shows_what_was_held():
    """The other way a turn stops: out of `show` by an exception, past any event."""

    def raising() -> Iterator[RunEvent]:
        yield _start("reviewer", "check the draft", "t1")
        yield _start("reviewer", "check the tests", "t2")
        yield _said("reviewer", "a0", "t1")
        msg = "the provider went away"
        raise RuntimeError(msg)

    out = io.StringIO()
    with pytest.raises(RuntimeError):
        show(raising(), out)

    assert out.getvalue().splitlines() == [
        "[reviewer] check the draft",
        "a0",
        "(unfinished)",
        "[reviewer] check the tests",
        "(unfinished)",
    ]


def test_on_two_streams_the_blocks_are_progress_and_the_answer_is_alone():
    """A redirected stdout still gets the answer and nothing a subagent said."""
    out, progress = io.StringIO(), io.StringIO()

    show(iter(SIDE_BY_SIDE), out, progress)

    assert out.getvalue() == "done"
    assert "[reviewer] check the tests\nb0 b1\n" in progress.getvalue()


def test_a_real_turn_side_by_side_prints_each_run_whole(cfg, session_dir):
    """Driven through a real graph, where the two runs interleave on the stream itself."""
    events = Kingfisher(cfg, graph=_twice(cfg, session_dir), sessions=LocalSessions()).stream(
        Request("go", session_id=session_dir.name)
    )
    out, progress = io.StringIO(), io.StringIO()

    show(events, out, progress)

    shown = progress.getvalue()
    for words in SAID:
        assert words in shown, f"{words!r} did not print whole:\n{shown}"
    assert out.getvalue() == "THE ANSWER"

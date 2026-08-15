"""The driver's rendering, which had no tests at all.

Nothing imported main.py, so the one file whose entire job is what the user
sees was the one file nobody checked. Every way this fails is silent and
textual -- a line jammed onto the end of a sentence, an unbounded argument, a
tool result leaking through as prose -- which is exactly what "run it and
look" misses, because it only shows on the input you did not happen to try.
"""

from __future__ import annotations

import io
from pathlib import Path

import main
from kingfisher.domain.result import RunEvent, RunResult


def _render(events: list[RunEvent]) -> tuple[str, RunResult | None]:
    out = io.StringIO()
    result = main.render(iter(events), out)
    return out.getvalue(), result


def _a_result() -> RunResult:
    return RunResult(
        session_id="s",
        turn_id="t001",
        answer="42",
        run_dir=Path("/tmp/run"),
        log_path=Path("/tmp/log"),
        swept=(),
        commit=None,
    )


def test_structural_events_render_one_per_line():
    text, _ = _render(
        [
            RunEvent(kind="run_start", text="/runs/s/t001"),
            RunEvent(kind="model_call", tools=("execute",), args=({"command": "ls"},)),
        ]
    )

    assert text.splitlines() == [
        "[start] /runs/s/t001",
        "[model] → execute(command=ls)  (in=0 cached=0)",
    ]


def test_the_finished_event_is_returned_not_printed():
    """It carries the result; it has nothing to say that was not already said."""
    expected = _a_result()
    text, result = _render([RunEvent(kind="finished", text="42", result=expected)])

    assert result is expected
    assert text == ""


def test_no_result_when_the_stream_never_finishes():
    """A stream cut short must not look like a successful run."""
    text, result = _render([RunEvent(kind="run_start", text="/runs/s/t001")])

    assert result is None
    assert "[start]" in text

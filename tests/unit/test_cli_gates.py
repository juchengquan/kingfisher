"""What the command says when a turn stopped for a decision it cannot give.

The generic ending this replaces told a reader two things that are both false for a
gated turn -- that the answer above is what was reached, and that they should carry
on with `--session`. The second is the one that matters: carrying on supersedes the
gate rather than answering it, so the advice steered somebody into discarding the
decision the turn had stopped to ask for.
"""

from __future__ import annotations

import argparse
from typing import Any

import kingfisher
from kingfisher.domain.result import AWAITING, RunResult
from kingfisher.domain.result import PendingDecision as Pending
from kingfisher.presentation.cli import __main__ as cli

PAUSED = RunResult(
    session_id="s-1",
    turn_id="t-1",
    answer="",
    stop_reason=AWAITING,
    pending=(
        Pending(
            call_id="abc123#0",
            tool="execute",
            args={"command": "rm -rf /tmp/x"},
            decisions=("approve", "reject", "respond"),
        ),
    ),
)

CUT_SHORT = RunResult(
    session_id="s-2", turn_id="t-2", answer="half of it", stop_reason="max_duration"
)


def _args(**over: Any) -> argparse.Namespace:
    return argparse.Namespace(
        **{
            "data": (),
            "task": "go",
            "agent": None,
            "session": None,
            "held": None,
            "delete_session": False,
            **over,
        }
    )


def _drive(monkeypatch, result: RunResult) -> None:
    """Run the command's ending against a result, with nothing real behind it."""
    monkeypatch.setattr(cli, "config_from_env", object)
    monkeypatch.setattr(kingfisher, "Kingfisher", lambda *a, **k: _Nothing())
    monkeypatch.setattr(cli, "show", lambda *a, **k: result)


class _Nothing:
    def stream(self, *_a: Any, **_k: Any) -> Any:
        return iter(())


def test_a_gated_ending_prints_the_ids_and_says_it_cannot_answer(monkeypatch, capsys):
    """The ids are the only way to answer, and nothing else in this command prints
    them -- so without this a person at a terminal can see that something stopped and
    has no way to find out what.
    """
    _drive(monkeypatch, PAUSED)

    code = cli._run(_args())

    said = capsys.readouterr().err
    assert code == 1
    assert "abc123#0" in said
    assert "execute" in said
    assert "cannot give" in said


def test_a_gated_ending_warns_that_running_again_discards(monkeypatch, capsys):
    """The specific falsehood this replaced. `--session` is the right advice for a
    turn cut off at a bound and the wrong one here, and the two used to share a line.
    """
    _drive(monkeypatch, PAUSED)

    cli._run(_args())

    said = capsys.readouterr().err
    assert "discards these" in said
    assert "the answer above is what was reached" not in said


def test_a_turn_cut_off_at_a_bound_still_says_to_continue_it(monkeypatch, capsys):
    """The control beside it. Without this, the assertions above pass against a
    command that stopped giving anybody the continuation advice at all.
    """
    _drive(monkeypatch, CUT_SHORT)

    code = cli._run(_args())

    said = capsys.readouterr().err
    assert code == 1
    assert "the answer above is what was reached" in said
    assert "discards these" not in said


def test_a_gated_ending_still_declines_to_delete_the_session(monkeypatch, capsys):
    """`--delete-session` on a turn holding a gate would throw away the thing the
    decision is for. The library already declines it; this is the command saying so.
    """
    _drive(monkeypatch, PAUSED)

    cli._run(_args(delete_session=True))

    assert "session kept" in capsys.readouterr().err

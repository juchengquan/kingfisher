"""What the command says when a turn stopped for a decision it cannot give.

The generic ending this replaces told a reader two things that are both false for a
gated turn -- that the answer above is what was reached, and that they should carry
on with `--session`. The second is the one that matters: carrying on supersedes the
gate rather than answering it, so the advice steered somebody into discarding the
decision the turn had stopped to ask for.
"""

from __future__ import annotations

import argparse
import importlib
from typing import Any

import pytest

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
    # The module itself, imported by name: as a dotted string `kingfisher.application.run`
    # resolves to the `run` function the package exports, whenever nothing has loaded
    # the module first -- so this patch worked in the full suite and failed alone.
    monkeypatch.setattr(
        importlib.import_module("kingfisher.application.run"),
        "configured_backend",
        lambda cfg: None,
    )
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
    # The command it hands back is runnable as printed, ids and all. A reader who
    # has to assemble it from the flag reference is a reader who will run the other
    # thing instead.
    assert "kingfisher decide --session s-1 --approve abc123#0" in said


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


# -- answering it ----------------------------------------------------------


def _decide_args(**over: Any) -> argparse.Namespace:
    return argparse.Namespace(
        **{
            "session": "s-1",
            "approve": [],
            "reject": [],
            "respond": [],
            "agent": None,
            "held": None,
            "delete_session": False,
            **over,
        }
    )


def test_respond_wants_its_text_and_says_so(capsys):
    """Refused here rather than sent on. The library refuses an empty `respond` too,
    with a message about a decision shape the person at the terminal never wrote --
    this one names the flag they did write.
    """
    code = cli._decide(_decide_args(respond=["abc123#0"]))

    assert code == 2
    assert "--respond wants CALL_ID=TEXT" in capsys.readouterr().err


def test_the_three_decisions_reach_the_library_as_written(monkeypatch):
    """The translation is the whole of this verb, and nothing else asserts it: a
    `--reject` that arrived as an approval would run the call somebody refused.
    """
    seen: list[Any] = []

    class _Recording:
        def stream(self, asked: Any, **_k: Any) -> Any:
            seen.append(asked)
            return iter(())

    monkeypatch.setattr(cli, "config_from_env", object)
    # The module itself, imported by name: as a dotted string `kingfisher.application.run`
    # resolves to the `run` function the package exports, whenever nothing has loaded
    # the module first -- so this patch worked in the full suite and failed alone.
    monkeypatch.setattr(
        importlib.import_module("kingfisher.application.run"),
        "configured_backend",
        lambda cfg: None,
    )
    monkeypatch.setattr(kingfisher, "Kingfisher", lambda *a, **k: _Recording())

    cli._decide(
        _decide_args(approve=["a#0"], reject=["b#1"], respond=["c#2=done already"])
    )

    (asked,) = seen
    assert asked.session_id == "s-1"
    assert {(d.call_id, d.action) for d in asked.decisions} == {
        ("a#0", "approve"),
        ("b#1", "reject"),
        ("c#2", "respond"),
    }
    assert [d.message for d in asked.decisions if d.action == "respond"] == ["done already"]


def test_deciding_nothing_shows_what_is_waiting(cfg, monkeypatch, capsys):
    """The way back to the ids for somebody who has lost the output of the run that
    paused -- and it reads the mark rather than starting a turn, because every other
    way into the session is a turn and a turn is what discards the gate.
    """
    from kingfisher.infrastructure.session_store import pending_as_mark, write_pause_mark
    from tests.conftest import harness_of, start

    start(cfg, "s-1")
    write_pause_mark(
        harness_of(cfg, "s-1"),
        {"pending": pending_as_mark([PAUSED.pending[0]])},
    )
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)

    code = cli._decide(_decide_args())

    said = capsys.readouterr().err
    assert code == 1
    assert "abc123#0" in said
    assert "execute" in said
    assert "approve, reject, respond" in said


def test_asking_a_session_that_is_not_waiting_says_so(cfg, monkeypatch, capsys):
    """Distinct from "no such session", because the remedies differ: one is a typo
    and the other is a turn that finished.
    """
    from tests.conftest import start

    start(cfg, "s-1")
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)

    code = cli._decide(_decide_args())

    assert code == 2
    assert "is not waiting on a decision" in capsys.readouterr().err


def test_asking_about_a_session_that_does_not_exist_says_that_instead(
    cfg, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)

    code = cli._decide(_decide_args(session="never-opened"))

    assert code == 2
    assert "no such session" in capsys.readouterr().err


def test_the_whole_loop_runs_through_the_command(cfg, session_dir, monkeypatch, capsys):
    """A gated turn, and the verb that answers it, against the real service.

    Everything above this asserts on one half: the flags translate, or the ending
    prints. Only this shows the two halves meet -- that the id the command printed
    is the id the command accepts, and that answering it runs the call. The ids are
    kingfisher's own and are built in one place and parsed in another, so a change
    to their shape would pass every other test in this file.
    """
    from deepagents import create_deep_agent
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from kingfisher import Kingfisher, Request, backend_at
    from tests.conftest import FakeToolCallingModel

    graph = create_deep_agent(
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "/derived/done.txt", "content": "ran"},
                            "id": "c1",
                        }
                    ],
                ),
                AIMessage(content="written"),
            ]
        ),
        backend=backend_at(cfg, session_dir),
        tools=None,
        interrupt_on={"write_file": True},
        checkpointer=InMemorySaver(),
    )
    service = Kingfisher(cfg, graph=graph)
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)
    monkeypatch.setattr(kingfisher, "Kingfisher", lambda *a, **k: service)

    paused = service.run(Request("write it", session_id=session_dir.name))
    assert paused.pending, "the turn did not stop, so there is nothing to answer"
    capsys.readouterr()  # the run's own output, so the assertion below reads clean

    code = cli._decide(
        _decide_args(session=session_dir.name, approve=[paused.pending[0].call_id])
    )

    assert code == 0, capsys.readouterr().err
    assert (session_dir / "derived" / "done.txt").read_text() == "ran"


def _a_paused_session_only_a_reaches(cfg):
    """A deployment with source ids, and a paused session pinned to an agent that only
    `A` reaches -- with a pending call whose arguments are worth not leaking.
    """
    from dataclasses import replace

    import yaml

    from kingfisher.domain.access import parse
    from kingfisher.infrastructure.session_store import pending_as_mark, write_pause_mark
    from tests.conftest import an_agent, harness_of, pin, start

    an_agent(cfg, "only_a", source_ids="[A]")
    policied = replace(cfg, access=parse(yaml.safe_load("source_ids: [A, B]\n"), source="t"))
    kf = kingfisher.Kingfisher(policied, backend=kingfisher.default_backend)
    start(policied, "s-1")
    pin(kf, "s-1", "only_a")
    write_pause_mark(harness_of(policied, "s-1"), {"pending": pending_as_mark([PAUSED.pending[0]])})
    return policied, kf


def test_a_caller_who_cannot_reach_the_session_is_not_shown_what_it_waits_on(cfg):
    """It asked as nobody, so anyone holding a session id read the pending calls and
    their arguments -- the one door into a session that did not ask who was calling.
    """
    from kingfisher import UnknownSessionError

    _, kf = _a_paused_session_only_a_reaches(cfg)

    # The control beside the escape: the caller who does reach it is shown the call.
    assert [call.call_id for call in kf.pending("s-1", source_ids=("A",))] == ["abc123#0"]
    with pytest.raises(UnknownSessionError):
        kf.pending("s-1", source_ids=("B",))


def test_the_command_asks_as_the_caller_it_names(cfg, monkeypatch, capsys):
    """`--as` reaches the question, and an unreachable session reads as a missing one."""
    policied, _ = _a_paused_session_only_a_reaches(cfg)
    monkeypatch.setattr(cli, "config_from_env", lambda: policied)

    refused = cli._decide(_decide_args(held=("B",)))
    told = capsys.readouterr().err
    shown = cli._decide(_decide_args(held=("A",)))

    assert refused == 2
    assert "no such session: s-1" in told
    assert "abc123#0" not in told
    assert shown == 1
    assert "abc123#0" in capsys.readouterr().err

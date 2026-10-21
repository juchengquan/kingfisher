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
import shlex
from typing import Any

import pytest

import kingfisher
import kingfisher.plugins
from kingfisher.cli import __main__ as cli
from kingfisher.core.access.source_ids import UNSCOPED, Datazone
from kingfisher.core.plugins import Plugins
from kingfisher.core.request import Resume
from kingfisher.core.result import AWAITING, RunResult
from kingfisher.core.result import PendingDecision as Pending
from kingfisher.core.session.steps import drive
from kingfisher.plugins import LocalSessions
from tests.conftest import RecordedEvents, Through

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
            "input": (),
            "task": "go",
            "agent": None,
            "session": None,
            "held": None,
            "datazone": None,
            "delete_session": False,
            "log": None,
            **over,
        }
    )


def _drive(monkeypatch, result: RunResult) -> None:
    """Run the command's ending against a result, with nothing real behind it."""
    monkeypatch.setattr(cli, "config_from_env", object)
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_sessions",
        lambda: None,
    )
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_plugins",
        Plugins,
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
    assert "kingfisher decide --session s-1 --approve 'abc123#0'" in said


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
            "datazone": None,
            "delete_session": False,
            "log": None,
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
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_sessions",
        lambda: None,
    )
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_plugins",
        Plugins,
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
    from kingfisher.core.session.store import pending_as_mark, write_pause_mark
    from tests.conftest import harness_of, start

    start(cfg, "s-1")
    drive(
        write_pause_mark(
            harness_of(cfg, "s-1"),
            {"pending": pending_as_mark([PAUSED.pending[0]])},
        )
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


def test_a_decision_a_session_is_not_waiting_for_is_refused_through_the_command(
    cfg, at_the_command_line, capsys
):
    """The same sentence as the rule above, through `main` and with an answer attached
    -- which is the path that had it.

    `DecisionError` was in neither `REFUSALS` nor any sibling `except`, so the clean
    exit 2 above became a traceback and exit 1: the code `decide --help` assigns to a
    turn that ran and stopped at another gate, read by whatever script is watching.
    """
    from tests.conftest import start

    start(cfg, "s-1")

    code = cli.main(["decide", "--session", "s-1", "--approve", "abc"])

    said = capsys.readouterr().err
    assert code == 2
    assert "is not waiting on a decision" in said
    assert "Traceback" not in said, "the refusal reached the terminal as a traceback"


def test_a_gate_on_a_name_nothing_answers_to_is_refused_through_the_command(
    cfg, at_the_command_line, capsys
):
    """`AgentError` was the other one missing from `REFUSALS`, and `run` is where it
    showed: an agent file naming a gate nothing matches gave a traceback and exit 1,
    where the help assigns 1 to a turn that ran and stopped at a bound.

    The message is the one worth reaching a terminal intact -- it lists what the gate
    could have named.
    """
    agents = cfg.catalogue_roots["agents"]
    agents.mkdir(parents=True, exist_ok=True)
    (agents / "gated.yaml").write_text(
        "name: gated\ndescription: An agent.\ninterrupt_on: [nosuchtool]\n"
        "system_prompt: |\n  Go.\n",
        encoding="utf-8",
    )

    code = cli.main(["run", "hi", "--agent", "gated"])

    said = capsys.readouterr().err
    assert code == 2
    assert "which no tool here answers to" in said
    assert "Offered:" in said, "the list of names a gate could have used went with it"
    assert "Traceback" not in said, "the refusal reached the terminal as a traceback"


def _paused_at_the_command_line(cfg, monkeypatch, tmp_path) -> None:
    """Session `s-1` waiting on `abc123#0`, in a working directory with nothing in it."""
    from kingfisher.core.session.store import pending_as_mark, write_pause_mark
    from tests.conftest import harness_of, start

    start(cfg, "s-1")
    mark = {"pending": pending_as_mark([PAUSED.pending[0]])}
    drive(write_pause_mark(harness_of(cfg, "s-1"), mark))
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize(
    "given",
    [("--agent", "operator"), ("--delete-session",), ("--log", "kept/turns.jsonl")],
    ids=lambda given: given[0],
)
def test_the_listing_refuses_a_flag_only_an_answer_uses(
    given, at_the_command_line, monkeypatch, tmp_path, capsys
):
    """Each was taken and dropped with nothing said -- `--agent` promising a check it
    never made, and `--log` creating its directory for a turn that never ran.
    """
    _paused_at_the_command_line(at_the_command_line, monkeypatch, tmp_path)

    code = cli.main(["decide", "--session", "s-1", *given])

    said = capsys.readouterr().err
    assert code == 2
    assert f"{given[0]} applies only when answering" in said
    assert "abc123#0" not in said
    assert not (tmp_path / "kept").exists(), "the log's directory was made for no turn"


def test_the_listing_without_them_still_shows_what_is_waiting(
    at_the_command_line, monkeypatch, tmp_path, capsys
):
    """The control beside the refusal: without it, a `decide` refusing every listing
    would pass the test above."""
    _paused_at_the_command_line(at_the_command_line, monkeypatch, tmp_path)

    code = cli.main(["decide", "--session", "s-1"])

    assert code == 1
    assert "abc123#0" in capsys.readouterr().err


def test_an_answer_still_takes_all_three(at_the_command_line, monkeypatch, tmp_path):
    """The other control: refusing the flags whenever they are given would refuse the
    one form they exist for."""
    _paused_at_the_command_line(at_the_command_line, monkeypatch, tmp_path)
    resumed: list[tuple[Resume, bool]] = []
    monkeypatch.setattr(
        cli,
        "_drive",
        lambda _kf, resume, *, held, delete_session: (
            resumed.append((resume, delete_session)) or 0
        ),
    )

    code = cli.main(
        [
            "decide", "--session", "s-1", "--approve", "abc123#0",
            "--agent", "operator", "--delete-session", "--log", "kept/turns.jsonl",
        ]
    )

    ((resume, delete_session),) = resumed
    assert code == 0
    assert resume.agent == "operator"
    assert delete_session
    assert (tmp_path / "kept").is_dir(), "--log never reached the turn's run events"


@pytest.mark.parametrize(
    "asked",
    [
        (["--session", "", "--approve", "abc123#0"], "--session is empty"),
        (["--session", "s-1", "--approve", "abc123#0", "--approve", "abc123#0"],
         "abc123#0 answered more than once"),
    ],
    ids=["empty-session", "answered-twice"],
)
def test_an_answer_resume_would_refuse_is_refused_through_the_command(
    asked, at_the_command_line, monkeypatch, tmp_path, capsys
):
    """Each reached `Resume`, whose `ValueError` was a traceback and exit 1, the code
    `decide --help` gives a turn that ran and stopped at another gate."""
    given, refused = asked
    _paused_at_the_command_line(at_the_command_line, monkeypatch, tmp_path)

    code = cli.main(["decide", *given])

    said = capsys.readouterr().err
    assert code == 2
    assert refused in said
    assert "Traceback" not in said


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

    from kingfisher import Kingfisher, Request
    from kingfisher.plugins import backend_at
    from tests.conftest import FakeToolCallingModel

    graph = create_deep_agent(
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"file_path": "/outputs/done.txt", "content": "ran"},
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
    service = Kingfisher(cfg, graph=graph, sessions=LocalSessions())
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)
    monkeypatch.setattr(kingfisher, "Kingfisher", lambda *a, **k: service)

    paused = service.run(Request("write it", session_id=session_dir.name))
    assert paused.pending, "the turn did not stop, so there is nothing to answer"
    capsys.readouterr()  # the run's own output, so the assertion below reads clean

    code = cli._decide(
        _decide_args(session=session_dir.name, approve=[paused.pending[0].call_id])
    )

    assert code == 0, capsys.readouterr().err
    assert (session_dir / "outputs" / "done.txt").read_text() == "ran"


def _a_paused_session_only_a_reaches(cfg):
    """A deployment with source ids, and a paused session pinned to an agent that only
    `A` reaches -- with a pending call whose arguments are worth not leaking.
    """


    from kingfisher.core.session.store import pending_as_mark, write_pause_mark
    from tests.conftest import an_agent, harness_of, pin, start

    an_agent(cfg, "only_a", source_ids="[A]")
    policied = cfg
    kf = kingfisher.Kingfisher(policied, sessions=kingfisher.plugins.LocalSessions())
    start(policied, "s-1")
    pin(kf, "s-1", "only_a")
    mark = {"pending": pending_as_mark([PAUSED.pending[0]])}
    drive(write_pause_mark(harness_of(policied, "s-1"), mark))
    return policied, kf


def test_a_caller_who_cannot_reach_the_session_is_not_shown_what_it_waits_on(cfg, way):
    """It asked as nobody, so anyone holding a session id read the pending calls and
    their arguments -- the one door into a session that did not ask who was calling.
    """
    from kingfisher import UnknownSessionError

    _, kf = _a_paused_session_only_a_reaches(cfg)

    reads = Through(kf, way)

    # The control beside the escape: the caller who does reach it is shown the call.
    assert [call.call_id for call in reads.pending("s-1", source_ids=("A",))] == ["abc123#0"]
    with pytest.raises(UnknownSessionError):
        reads.pending("s-1", source_ids=("B",))


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


@pytest.mark.parametrize("verb", ["run", "decide"])
def test_log_reaches_the_service_on_both_verbs_that_run_a_turn(verb, tmp_path, monkeypatch):
    """A resumed turn is a turn too. Without `--log` a command keeps no record of what
    a turn did, which is where it was left when the run log stopped being a file in
    the session.
    """
    from kingfisher.plugins.events import JsonlRunEvents

    handed: list[object] = []

    class Recording(_Nothing):
        def __init__(self, *args, run_events=None, **kwargs) -> None:
            handed.append(run_events)

    monkeypatch.setattr(cli, "config_from_env", object)
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_sessions",
        lambda: None,
    )
    monkeypatch.setattr(
        importlib.import_module("kingfisher.cli.configured"),
        "configured_plugins",
        Plugins,
    )
    monkeypatch.setattr(kingfisher, "Kingfisher", Recording)
    monkeypatch.setattr(cli, "show", lambda *a, **k: None)
    log = tmp_path / "turns.jsonl"

    if verb == "run":
        cli._run(_args(log=log))
    else:
        cli._decide(_decide_args(log=log, approve=["c1"]))

    (sink,) = handed
    assert isinstance(sink, JsonlRunEvents)
    assert sink.path == log


def test_the_plugins_reach_the_service_and_log_replaces_their_run_events(tmp_path, monkeypatch):
    """What `KINGFISHER_PLUGINS` names is handed to every `Kingfisher` the command builds,
    and `--log`, the narrower ask, takes the place of the run events it names."""
    from kingfisher.plugins.events import JsonlRunEvents

    handed: list[dict[str, object]] = []
    events = RecordedEvents()

    class Recording(_Nothing):
        def __init__(self, *args, **kwargs) -> None:
            handed.append(kwargs)

    monkeypatch.setattr(cli, "config_from_env", object)
    run = importlib.import_module("kingfisher.cli.configured")
    monkeypatch.setattr(run, "configured_sessions", lambda: None)
    monkeypatch.setattr(run, "configured_plugins", lambda: Plugins(run_events=events))
    monkeypatch.setattr(kingfisher, "Kingfisher", Recording)
    monkeypatch.setattr(cli, "show", lambda *a, **k: None)

    cli._run(_args())
    cli._run(_args(log=tmp_path / "turns.jsonl"))

    plain, logged = handed
    assert plain["run_events"] is events
    assert isinstance(logged["run_events"], JsonlRunEvents)


TWO_WAITING = RunResult(
    session_id="s-3",
    turn_id="t-3",
    answer="",
    stop_reason=AWAITING,
    pending=(
        Pending(call_id="abc123#0", tool="execute", decisions=("approve", "reject")),
        Pending(call_id="abc123#1", tool="write_file", decisions=("reject", "respond")),
    ),
)


class _JudgedError(Exception):
    """Raised once the library has judged a resume, before it builds a graph to run it."""


def _ran_as_printed(monkeypatch, cfg, said: str, result: RunResult) -> None:
    """The `answer with:` line, parsed by the command's own parser and judged by the
    library: driven rather than matched, because a line that looks right can still be
    one `decide` refuses.

    Through the real `_drive`, against a session only `A` reaches: with
    `_drive` stubbed, a line naming no caller passed here and was refused where a
    definition carries a lock.
    """
    from kingfisher.cli.progress import show
    from kingfisher.core.layout import PAUSED_STATE
    from kingfisher.core.service import Kingfisher
    from kingfisher.core.session import runtime
    from kingfisher.core.session.store import pending_as_mark, write_pause_mark
    from tests.conftest import an_agent, harness_of, pin, start

    line = next(one for one in said.splitlines() if one.startswith("answer with: "))
    argv = shlex.split(line.removeprefix("answer with: "))
    assert argv[0] == "kingfisher"

    # The agent's own `source_ids:` is the lock, so this workspace already refuses a
    # line naming nobody; the datazone the run names is one no definition requires,
    # which is held and opens nothing.
    an_agent(cfg, "only_a", source_ids="[A]")
    kf = Kingfisher(cfg, sessions=LocalSessions())
    start(cfg, result.session_id)
    pin(kf, result.session_id, "only_a")
    harness = harness_of(cfg, result.session_id)
    drive(write_pause_mark(harness, {"pending": pending_as_mark(list(result.pending))}))
    drive(harness.store(PAUSED_STATE, b"held"))

    judge = runtime.resume_payload

    def judged(answers, pending):
        judge(answers, pending)
        raise _JudgedError

    monkeypatch.setattr(runtime, "resume_payload", judged)
    monkeypatch.setattr(cli, "config_from_env", lambda: cfg)
    monkeypatch.setattr(kingfisher, "Kingfisher", lambda *a, **k: kf)
    monkeypatch.setattr(cli, "show", show)

    with pytest.raises(_JudgedError):
        cli._decide(cli.build_parser().parse_args(argv[1:]))


#: Who the runs below were asked as: the caller the printed line has to name again.
ASKED_AS = {"held": ("A",), "datazone": "finance"}


def test_the_printed_answer_answers_every_waiting_call(cfg, monkeypatch, capsys):
    """The line named only the first call, and `decide` refuses an answer that leaves
    one waiting -- so with two calls pending, running it as printed was refused with
    "still waiting on abc123#1"; and it offered `--approve` to a call that takes none.
    """
    _drive(monkeypatch, TWO_WAITING)
    cli._run(_args(**ASKED_AS))

    said = capsys.readouterr().err
    # Quoted as `--respond` is, so an id the shell would split still arrives whole.
    assert "--approve 'abc123#0' --reject 'abc123#1'\n" in said
    _ran_as_printed(monkeypatch, cfg, said, TWO_WAITING)


def test_one_waiting_call_is_still_answered_by_approving_it(cfg, monkeypatch, capsys):
    """The control beside it: one call that takes approval is answered as before."""
    _drive(monkeypatch, PAUSED)
    cli._run(_args(**ASKED_AS))

    said = capsys.readouterr().err
    assert "--approve 'abc123#0'\n" in said
    _ran_as_printed(monkeypatch, cfg, said, PAUSED)


def test_the_printed_answer_names_the_caller_the_run_was_asked_as(capsys):
    """It named no `--as` or `--datazone`, so under a policy pasting it was refused as
    a call from nobody."""
    cli._awaiting(PAUSED, ("A", Datazone("finance")))
    cli._awaiting(PAUSED, UNSCOPED)
    cli._awaiting(PAUSED, None)

    lines = [one for one in capsys.readouterr().err.splitlines() if "answer with:" in one]
    assert [one.split(" --approve")[0] for one in lines] == [
        "answer with: kingfisher decide --session s-1 --as A --datazone finance",
        "answer with: kingfisher decide --session s-1 --as UNSCOPED",
        "answer with: kingfisher decide --session s-1",
    ]

"""The saved results of turns a rewind left behind: out of the agent's reach, back on a
rewind returning to them, still aged, and a move that stopped finished later.

Through real turns, so the files moved are the ones the recorder filed and the rows that
name them the ones the turns wrote.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request, RewindError
from kingfisher.core.layout import HARNESS, OFF_PATH_RESULTS, RECORDS, RESULTS_MOVING, TOOL_RESULTS
from kingfisher.core.session import off_path_results
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted

TOOLS = '''
def loud_about(subject: str) -> str:
    """Return about 101,000 characters naming `subject`, long enough to be filed at once."""
    return "\\n".join(f"{subject} {n:05d} " + "x" * 90 for n in range(1_000))


TOOLS = [loud_about]
'''


@pytest.fixture
def filing(scripted):
    """An agent whose one tool's result is filed as it arrives, and the session `s`."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _call(*made: tuple[str, dict[str, Any], str]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"name": name, "args": args, "id": at} for name, args, at in made],
    )


def _loud(*subjects: str) -> AIMessage:
    return _call(*(("loud_about", {"subject": one}, one) for one in subjects))


def _say(kf: Kingfisher, task: str, *script: AIMessage):
    Scripted.script.extend([*script, AIMessage("done")])
    return kf.run(Request(task, agent="only", session_id="s"))


def _shown(cfg) -> dict[str, bytes]:
    return _held(cfg.workspace / "sessions" / "s" / RECORDS / TOOL_RESULTS)


def _hidden(cfg) -> dict[str, bytes]:
    return _held(cfg.workspace / "sessions" / "s" / HARNESS / OFF_PATH_RESULTS)


def _held(folder) -> dict[str, bytes]:
    return {one.name: one.read_bytes() for one in folder.iterdir()} if folder.exists() else {}


def _unsettled(cfg) -> bool:
    return (cfg.workspace / "sessions" / "s" / HARNESS / RESULTS_MOVING).exists()


def _two_turns(kf: Kingfisher):
    """A turn filing A-0001, and one after it filing A-0002 and A-0003."""
    first = _say(kf, "one", _loud("one"))
    second = _say(kf, "two", _loud("two", "three"))
    assert [entry.path for entry in second.ledger] == [
        "/records/tool_results/A-0002-loud_about.txt",
        "/records/tool_results/A-0003-loud_about.txt",
    ]
    return first, second


def test_a_rewind_takes_a_left_turns_results_out_of_the_agents_reach(filing):
    """Left in the results folder, a turn the rewind abandoned was one `glob` away: the
    agent could read what a branch the caller took back had found."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, _ = _two_turns(kf)
    before = _shown(filing)

    kf.session("s").rewind(first.turn_id)

    assert set(_shown(filing)) == {"A-0001-loud_about.txt"}
    assert _hidden(filing) == {name: before[name] for name in before if "A-0001" not in name}
    Scripted.heard.clear()
    _say(
        kf,
        "look",
        _call(
            ("read_file", {"file_path": "/records/tool_results/A-0002-loud_about.txt"}, "r2"),
            ("glob", {"pattern": "A-000*", "path": "/records/tool_results"}, "g"),
            # The control: the path's own result is read where it was.
            ("read_file", {"file_path": "/records/tool_results/A-0001-loud_about.txt"}, "r1"),
        ),
    )
    answers = {one.tool_call_id: str(one.content) for one in Scripted.heard[-1]
               if isinstance(one, ToolMessage)}
    assert "two 00000" not in answers["r2"]
    assert "A-0001" in answers["g"]
    assert "A-0002" not in answers["g"] and "A-0003" not in answers["g"]
    assert "one 00000" in answers["r1"]


def test_a_rewind_back_returns_the_results_byte_for_byte_and_a_caller_reads_them_between(
    filing,
):
    """Left hidden, a rewind back to the turn handed its agent a ledger naming files that were
    gone; and read from the folder alone, a caller asking for a hidden result by its id was
    told it was kept nowhere."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, second = _two_turns(kf)
    before = _shown(filing)
    kf.session("s").rewind(first.turn_id)

    assert kf.session("s").result("A-0002").startswith("two 00000")

    kf.session("s").rewind(second.turn_id)

    assert _shown(filing) == before
    assert _hidden(filing) == {}
    assert not _unsettled(filing)


def test_a_result_expires_whether_its_turn_is_on_the_path_or_not(filing):
    """Swept from the results folder alone, a result expired while hidden kept its file
    forever, and `KINGFISHER_KEEP_RESULTS_ROUNDS` bounded nothing once a session rewound."""
    cfg = replace(filing, keep_results_rounds=1)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    first, _ = _two_turns(kf)
    kf.session("s").rewind(first.turn_id)
    assert _hidden(cfg), "the control: nothing was hidden to expire"

    _say(kf, "something else")

    assert _shown(cfg) == {}
    assert _hidden(cfg) == {}


def _stopping(  # noqa: PLR0913 -- each way a copy can go wrong, by keyword
    monkeypatch,
    cfg,
    *,
    after: int,
    cut: bool = False,
    only: str | None = None,
    refused: bool = False,
) -> None:
    """Make the moves stop at the copy after `after` that matches `only`, the destination's
    folder; with `cut`, once that copy has written half its file; with `refused`, as a
    backend refusing the copy rather than the machine going away."""
    real = off_path_results.copied_through
    seen = []

    def copied(files: Any, source: str, destination: str, size: Any) -> Any:
        if only is None or f"/{only}/" in destination:
            seen.append(destination)
            if len(seen) > after:
                if cut:
                    yield from real(files, source, destination, size)
                    landed = cfg.workspace / "sessions" / "s" / destination.lstrip("/")
                    landed.write_bytes(landed.read_bytes()[: landed.stat().st_size // 2])
                if refused:
                    return None, "permission_denied", None
                msg = "the machine went away"
                raise OSError(msg)
        return (yield from real(files, source, destination, size))

    monkeypatch.setattr(off_path_results, "copied_through", copied)


def test_a_rewind_stopped_twice_is_finished_by_the_next_turn(filing, monkeypatch):
    """With no mark left behind, a rewind that stopped between two moves left the agent a
    result from a branch it no longer followed for every turn after."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, second = _two_turns(kf)
    before = _shown(filing)
    with monkeypatch.context() as patched:
        _stopping(patched, filing, after=1)
        with pytest.raises(OSError, match="went away"):
            kf.session("s").rewind(first.turn_id)
    assert set(_shown(filing)) == {"A-0001-loud_about.txt", "A-0003-loud_about.txt"}, (
        "the control: the rewind did not stop between its moves"
    )
    with monkeypatch.context() as patched:
        _stopping(patched, filing, after=0, cut=True)
        with pytest.raises(OSError, match="went away"):
            kf.session("s").rewind(first.turn_id)
    assert _unsettled(filing)

    _say(kf, "and now?")

    assert set(_shown(filing)) == {"A-0001-loud_about.txt"}
    assert _hidden(filing) == {name: before[name] for name in before if "A-0001" not in name}
    assert not _unsettled(filing)
    kf.session("s").rewind(second.turn_id)
    assert _shown(filing) == before


def test_a_rewind_back_finishes_the_stopped_move_before_reversing_it(filing, monkeypatch):
    """Reversed straight away, the cut copy a stopped move had left in `.harness` was the
    one moved back, and the result came back half."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, second = _two_turns(kf)
    before = _shown(filing)
    with monkeypatch.context() as patched:
        _stopping(patched, filing, after=1, cut=True)
        with pytest.raises(OSError, match="went away"):
            kf.session("s").rewind(first.turn_id)
    assert len(_hidden(filing)["A-0003-loud_about.txt"]) < len(
        before["A-0003-loud_about.txt"]
    ), "the control: the stopped copy is whole"

    kf.session("s").rewind(second.turn_id)

    assert _shown(filing) == before
    assert _hidden(filing) == {}


def test_a_stopped_move_that_cannot_be_finished_refuses_the_rewind(filing, monkeypatch):
    """Let through, a rewind back moved the stopped move's cut copy over the whole one,
    since nothing had finished copying the whole one across first."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, second = _two_turns(kf)
    before = _shown(filing)
    with monkeypatch.context() as patched:
        _stopping(patched, filing, after=1, cut=True)
        with pytest.raises(OSError, match="went away"):
            kf.session("s").rewind(first.turn_id)

    with monkeypatch.context() as patched:
        # Every move into `.harness` fails, and every move out of it would succeed.
        _stopping(patched, filing, after=0, only=OFF_PATH_RESULTS, refused=True)
        with pytest.raises(RewindError, match="could not be"):
            kf.session("s").rewind(second.turn_id)

    assert _shown(filing)["A-0003-loud_about.txt"] == before["A-0003-loud_about.txt"]
    assert _unsettled(filing)
    assert [turn.id for turn in kf.session("s").turns() if turn.current] == [first.turn_id]


def test_a_rewind_of_the_files_alone_moves_no_result(filing):
    """Moved with the files, a rewind keeping the conversation took away results the
    conversation it kept still names."""
    kf = Kingfisher(filing, sessions=LocalSessions())
    first, _ = _two_turns(kf)
    before = _shown(filing)

    kf.session("s").rewind(first.turn_id, only="files")

    assert _shown(filing) == before
    assert _hidden(filing) == {}

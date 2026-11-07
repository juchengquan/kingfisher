"""Rewinding a session: its conversation, the results its next turn is told of, and what
it heard of its inputs folder put back where an earlier turn left them, with the turns
after it kept.

Through real turns -- a scripted model, kingfisher's own build, deepagents' own
summariser -- because what a rewind rebuilds is what those turns wrote, and a log written
here would hold only what the test expected.
"""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage

from assets.middlewares.summarize_by_count import SummarizeByCount
from kingfisher import (
    START,
    Kingfisher,
    Request,
    RewindError,
    SessionBusyError,
    TurnFiles,
    TurnIdError,
)
from kingfisher.cli.__main__ import main
from kingfisher.core.layout import (
    FILE_HISTORY,
    HARNESS,
    INPUTS_SEEN,
    LEDGER_FILE,
    MEMORY_WRITER,
    PAUSED_MARK,
    TURNS_FILE,
)
from kingfisher.core.request import Decision, DecisionError
from kingfisher.core.result import AWAITING
from kingfisher.core.session import file_history, preparation, rewind
from kingfisher.core.session.file_history import COPIES_OFF, OVER_BUDGET, dropped_for_space
from kingfisher.plugins import LocalSessions
from tests.conftest import (
    EVERY_TOOL,
    an_agent,
    read_conversation,
    read_record,
    read_record_bytes,
    start,
    tools_dir,
    write_old_record,
)
from tests.unit.scripted import Scripted
from tests.unit.session.test_turn_log import TOOLS, _calls, _resume, _say, _searches
from tests.unit.stand_ins import StubAgent


def _conversation(cfg) -> tuple:
    return read_conversation(cfg.workspace / "sessions" / "s" / HARNESS)


@pytest.fixture
def rewinding(scripted):
    """An agent with a search tool and a gated query, and the session `s`."""
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOLS, encoding="utf-8")
    an_agent(scripted, interrupt_on="[sql_query]", **EVERY_TOOL)
    start(scripted, "s")
    return scripted


def _sent(index: int = 0) -> str:
    """Everything the model was handed on one call of the turn just run."""
    return "\n".join(str(message.content) for message in Scripted.heard[index])


def _asked() -> str:
    """The message the turn just run was sent as the user's, history left out."""
    return str(next(one.content for one in reversed(Scripted.heard[0]) if one.type == "human"))


def _harness_files(cfg) -> dict:
    harness = cfg.workspace / "sessions" / "s" / HARNESS
    return {path: path.read_bytes() for path in harness.rglob("*") if path.is_file()}


def test_a_rewind_puts_the_conversation_back_and_the_next_turn_carries_on_from_it(rewinding):
    """Left at the conversation as it was, the next turn answers from turns the caller took
    back; following the last turn the log holds, its record hangs off a turn its model
    never saw; told of an abandoned turn's results, the model cites them; and a result
    number given again would name two results."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    after_first = _conversation(rewinding)
    _say(kf, "TASK-SECOND", AIMessage("ANSWER-SECOND"))
    # Last, so its results are what the next turn would be told of were it still followed.
    third = _say(kf, "TASK-THIRD", _calls(("web_search", "q", "c1")), AIMessage("ANSWER-THIRD"))
    assert [entry.id for entry in third.ledger] == ["A-0001"]

    done = kf.session("s").rewind(first.turn_id)

    assert done.to == first.turn_id
    assert _conversation(rewinding) == after_first
    Scripted.heard.clear()
    fourth = _say(kf, "TASK-FOURTH", _calls(("web_search", "r", "c2")), AIMessage("ok"))
    sent = _sent()
    assert "ANSWER-FIRST" in sent
    assert not any(word in sent for word in ("TASK-SECOND", "ANSWER-THIRD", "A-0001"))
    assert [entry.id for entry in fourth.ledger] == ["A-0002"]
    turns = kf.session("s").turns()
    assert {turn.id: turn.parent for turn in turns}[fourth.turn_id] == first.turn_id
    assert [turn.id for turn in turns if turn.current] == [first.turn_id, fourth.turn_id]
    # A turn left behind still holds its id.
    with pytest.raises(TurnIdError):
        _say(kf, "again", turn_id=third.turn_id)


def test_a_rewind_to_before_a_summary_brings_back_what_the_summary_replaced(rewinding):
    """Rebuilt from what the model last saw, a turn before the last summary has nothing left to
    rebuild: its words are behind the summary, in a file the agent can write."""
    an_agent(
        rewinding,
        interrupt_on="[sql_query]",
        middlewares="[{name: summarize-by-count, settings: {messages: 12}}]",
        **EVERY_TOOL,
    )
    kf = Kingfisher(
        rewinding, sessions=LocalSessions(), middlewares={"summarize-by-count": SummarizeByCount}
    )
    first = _say(kf, "TASK-EARLY", *_searches("a", 3), AIMessage("ANSWER-EARLY"))
    after_first = _conversation(rewinding)
    _say(kf, "TASK-LATER", *_searches("b", 4), AIMessage("ANSWER-LATER"))
    assert Scripted.summaries >= 1, "the control: the second turn never summarised"
    assert _conversation(rewinding)[0].summary, "the control: the summary replaced nothing"

    kf.session("s").rewind(first.turn_id)

    assert _conversation(rewinding) == after_first


def test_a_rewind_returns_to_a_turn_it_left_behind(rewinding):
    """Deleted at the first rewind, the turns after it could never be returned to, and a
    rewind made by mistake would lose them for good."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    second = _say(kf, "TASK-SECOND", AIMessage("ANSWER-SECOND"))
    third = _say(kf, "TASK-THIRD", AIMessage("ANSWER-THIRD"))
    after_third = _conversation(rewinding)
    kf.session("s").rewind(first.turn_id)
    _say(kf, "TASK-OTHER", AIMessage("ANSWER-OTHER"))

    kf.session("s").rewind(third.turn_id)

    assert _conversation(rewinding) == after_third
    assert [turn.id for turn in kf.session("s").turns() if turn.current] == [
        first.turn_id,
        second.turn_id,
        third.turn_id,
    ]
    Scripted.heard.clear()
    _say(kf, "TASK-NEXT", AIMessage("ok"))
    assert "ANSWER-THIRD" in _sent()
    assert "ANSWER-OTHER" not in _sent()


def test_a_rewind_to_the_start_leaves_no_conversation(rewinding):
    """A turn after it that still followed the old turns would be answered from a
    conversation the caller emptied."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))

    done = kf.session("s").rewind(START)

    assert done.to is None
    assert _conversation(rewinding) == ()
    assert not any(turn.current for turn in kf.session("s").turns())
    Scripted.heard.clear()
    fresh = _say(kf, "TASK-FRESH", AIMessage("ok"))
    assert "ANSWER-FIRST" not in _sent()
    assert {turn.id: turn.parent for turn in kf.session("s").turns()}[fresh.turn_id] is None


def test_a_rewind_discards_the_pause_it_finds(rewinding):
    """Kept, the pause would let a resume run calls asked for in a conversation the
    session is no longer at."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    paused = _say(kf, "query it", _calls(("sql_query", "x", "g1")))
    assert paused.stop_reason == AWAITING, "the control: the turn never stopped at the gate"

    done = kf.session("s").rewind(first.turn_id)

    assert [one.tool for one in done.discarded] == ["sql_query"]
    assert not (rewinding.workspace / "sessions" / "s" / HARNESS / PAUSED_MARK).exists()
    with pytest.raises(DecisionError, match="not waiting"):
        _resume(kf, Decision(call_id=paused.pending[0].call_id, action="approve"))


def test_a_refused_rewind_changes_nothing(rewinding):
    """A rewind writing before its refusals would leave a session half on one branch and
    half on another: a turn the session never ran named, or a turn already holding it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    kept = _harness_files(rewinding)

    with pytest.raises(RewindError, match="no turn"):
        kf.session("s").rewind("t-never")
    held = LocalSessions().claim(rewinding, "s", stale_after=3600)
    assert held is not None
    with pytest.raises(SessionBusyError):
        kf.session("s").rewind(START)
    LocalSessions().release(rewinding, "s", held)

    assert _harness_files(rewinding) == kept


def test_a_misspelt_part_takes_back_nothing(rewinding):
    """Taken for neither part, `only="file"` took back both, and the files the caller meant
    to keep were overwritten."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (_outputs(rewinding) / "report.md").write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    (_outputs(rewinding) / "report.md").write_text("SHORT", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))
    kept = _harness_files(rewinding)
    misspelt: Any = "file"

    with pytest.raises(ValueError, match="'file'"):
        kf.session("s").rewind(first.turn_id, only=misspelt)

    assert (_outputs(rewinding) / "report.md").read_text(encoding="utf-8") == "SHORT"
    assert _harness_files(rewinding) == kept


def test_turns_logged_under_one_id_are_listed_and_a_rewind_behind_them_is_refused(cfg, monkeypatch):
    """A supplied graph's session that ran two turns under one id, before #1208 refused it,
    logged the second's line in place of the first's: the walk back came round forever, so
    `turns` and `rewind` never returned, and one cut short would rebuild a conversation no
    turn ended on."""
    start(cfg, "s")
    kf = Kingfisher(cfg, graph=StubAgent("ok"), sessions=LocalSessions())
    for turn_id in ("req-76", "req-77", "req-78"):
        kf.run(Request(turn_id, agent="only", session_id="s", turn_id=turn_id))

    def no_id_held(log):
        # The check as it was before #1208, which read no log.
        return frozenset()

    with monkeypatch.context() as patched:
        patched.setattr(preparation, "logged_turns", no_id_held)
        kf.run(Request("again", agent="only", session_id="s", turn_id="req-77"))
    # Past the loop, so a walk from it meets the loop partway rather than at its start.
    kf.run(Request("req-79", agent="only", session_id="s", turn_id="req-79"))

    listed: list = []
    # On a thread, since the walk never ending would hang the suite rather than fail it.
    worker = threading.Thread(target=lambda: listed.extend(kf.session("s").turns()), daemon=True)
    worker.start()
    worker.join(10)
    assert [turn.id for turn in listed] == ["req-76", "req-77", "req-78", "req-79"]

    kept = _harness_files(cfg)
    for behind in ("req-77", "req-78", "req-79"):
        with pytest.raises(RewindError, match="lead back into each other"):
            kf.session("s").rewind(behind)
    assert _harness_files(cfg) == kept
    # The control: a turn from before the reuse is not behind the loop, and goes back.
    assert kf.session("s").rewind("req-76").to == "req-76"


def test_a_result_left_on_a_branch_is_still_read_by_its_id(rewinding):
    """Out of the conversation and never filed, a result a rewind left behind was kept
    nowhere a caller could read it, though its row still named it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "look", _calls(("web_search", "q", "c1")), AIMessage("found"))

    kf.session("s").rewind(START)

    assert kf.session("s").result("A-0001") == "Ten results for q"


def test_a_result_left_on_a_branch_still_ages(rewinding):
    """Counted along the path only, a result a rewind left behind never expired, and
    `KINGFISHER_KEEP_RESULTS_ROUNDS` bounded nothing once a session had rewound."""
    cfg = replace(rewinding, keep_results_rounds=1)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    _say(kf, "look", _calls(("web_search", "q", "c1")), AIMessage("found"))
    kf.session("s").rewind(START)

    later = _say(kf, "something else", AIMessage("ok"))

    ledger = cfg.workspace / "sessions" / "s" / HARNESS / LEDGER_FILE
    (row,) = [one for one in map(json.loads, read_record(ledger).splitlines()) if one.get("id")]
    assert row["expired_in"] == later.turn_id


def test_what_the_agent_heard_of_its_inputs_goes_back_with_the_conversation(rewinding, tmp_path):
    """Left as the latest turn heard it, the inputs folder a rewound conversation never
    saw change would not be reported as changed, and the model would answer from a file
    it never read."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (tmp_path / "a.csv").write_text("a", encoding="utf-8")
    (tmp_path / "b.csv").write_text("b", encoding="utf-8")
    first = _say(kf, "read a", AIMessage("ok"), inputs=(tmp_path / "a.csv",))
    heard = (rewinding.workspace / "sessions" / "s" / HARNESS / INPUTS_SEEN).read_bytes()
    _say(kf, "read b", AIMessage("ok"), inputs=(tmp_path / "b.csv",))

    kf.session("s").rewind(first.turn_id)

    assert (rewinding.workspace / "sessions" / "s" / HARNESS / INPUTS_SEEN).read_bytes() == heard
    Scripted.heard.clear()
    _say(kf, "what is new?", AIMessage("ok"))
    assert "b.csv" in _sent()


def test_the_command_lists_turns_and_rewinds(rewinding, at_the_command_line, capsys):
    """The verbs a person without a library rewinds with: listing the turns marks where
    the session is, and rewinding by name moves it."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    first = _say(kf, "TASK-FIRST", AIMessage("one"))
    _say(kf, "TASK-SECOND", AIMessage("two"))

    assert main(["rewind", "--session", "s", "--to", first.turn_id]) == 0
    assert main(["turns", "--session", "s"]) == 0

    printed = capsys.readouterr().out
    assert f"where turn {first.turn_id} ended" in printed
    marked = [line for line in printed.splitlines() if line.startswith("*")]
    assert len(marked) == 1
    assert first.turn_id in marked[0]


def test_the_command_takes_back_one_part(rewinding, at_the_command_line, capsys):
    """Dropped on its way to the library, `--only conversation` took back the files too,
    overwriting what the person meant to keep."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (_outputs(rewinding) / "report.md").write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    (_outputs(rewinding) / "report.md").write_text("SHORT", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))

    rewound = ["rewind", "--session", "s", "--to", first.turn_id, "--only", "conversation"]
    assert main(rewound) == 0

    assert (_outputs(rewinding) / "report.md").read_text(encoding="utf-8") == "SHORT"
    said = f"the conversation of session s is now where turn {first.turn_id} ended"
    assert said in capsys.readouterr().out


def _outputs(cfg):
    return cfg.workspace / "sessions" / "s" / "outputs"


def test_each_turn_lists_what_it_did_to_its_files(rewinding, at_the_command_line, capsys):
    """Listed without its files, a turn told a caller choosing where to go back only its
    task: what came back, and what could not, was known only once the rewind was done. A
    turn nothing recorded reads as unknown, never as one that changed nothing."""
    cfg = replace(rewinding, rewind_max_bytes=25)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    (_outputs(cfg) / "report.md").write_text("A" * 10, encoding="utf-8")
    (_outputs(cfg) / "draft.md").write_text("D", encoding="utf-8")
    _say(kf, "write them", AIMessage("ok"), turn_id="t1")
    (_outputs(cfg) / "report.md").write_text("B" * 10, encoding="utf-8")
    (_outputs(cfg) / "draft.md").unlink()
    (_outputs(cfg) / "big.csv").write_text("x" * 30, encoding="utf-8")
    _say(kf, "rework them", AIMessage("ok"), turn_id="t2")
    (_outputs(cfg) / "report.md").write_text("C" * 10, encoding="utf-8")
    # Over the budget: the copy restoring only t1 goes first.
    _say(kf, "once more", AIMessage("ok"), turn_id="t3")

    files = {turn.id: turn.files for turn in kf.session("s").turns()}
    assert files["t1"] is not None
    assert {"outputs/draft.md", "outputs/report.md"} <= set(files["t1"].changed)
    assert files["t1"].without_copy["outputs/report.md"] == (
        "its copy was dropped for space in turn t3"
    )
    assert files["t2"] == TurnFiles(
        changed=("outputs/big.csv", "outputs/report.md"),
        removed=("outputs/draft.md",),
        without_copy={"outputs/big.csv": OVER_BUDGET},
    )
    assert files["t3"] == TurnFiles(changed=("outputs/report.md",))

    # As a turn that died before its line leaves the history.
    history = cfg.workspace / "sessions" / "s" / HARNESS / FILE_HISTORY
    kept = [one for one in read_record(history).splitlines() if '"turn": "t2"' not in one]
    write_old_record(history, "".join(f"{one}\n" for one in kept))
    assert {turn.id: turn.files for turn in kf.session("s").turns()}["t2"] is None

    assert main(["turns", "--session", "s"]) == 0
    printed = capsys.readouterr().out
    assert "    changed outputs/draft.md" in printed
    assert "    no copy of outputs/report.md: its copy was dropped for space in turn t3" in printed
    assert "    files not recorded" in printed


def test_a_rewind_puts_back_the_files_the_turn_left(rewinding):
    """Left as the latest turn left them, the files would answer for a conversation the
    caller took back: the agent reads a report it remembers writing differently."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (_outputs(rewinding) / "report.md").write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    (_outputs(rewinding) / "report.md").write_text("SHORT", encoding="utf-8")
    (_outputs(rewinding) / "extra.md").write_text("more", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))

    done = kf.session("s").rewind(first.turn_id)

    assert (_outputs(rewinding) / "report.md").read_text(encoding="utf-8") == "LONG"
    assert not (_outputs(rewinding) / "extra.md").exists()
    assert set(done.restored) == {"outputs/report.md", "outputs/extra.md"}
    assert dict(done.not_restored) == {}
    Scripted.heard.clear()
    _say(kf, "and now?", AIMessage("ok"))
    assert "Changed since your last turn" not in _sent()


def test_on_a_disk_that_makes_no_clones_a_file_put_back_is_not_the_next_turns_change(
    rewinding, monkeypatch
):
    """Copied back without a clone, a file takes a new time; listed by the time its turn
    recorded, it read as changed to the next turn, which copied it again and listed it among
    its own changes."""

    def refuse(_: int, target: Any) -> None:
        # As Linux's refusal does, which has made the file before the kernel refuses.
        target.touch()
        raise OSError(95, "Operation not supported", str(target))

    monkeypatch.setattr(file_history, "_clone", refuse)
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    report = _outputs(rewinding) / "report.md"
    report.write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    report.write_text("SHORT", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))
    kf.session("s").rewind(first.turn_id)

    after = _say(kf, "and now?", AIMessage("ok"))

    (record,) = (one for one in kf.session("s").turns() if one.id == after.turn_id)
    assert record.files is not None
    assert record.files.changed == ()


def test_a_version_with_no_copy_is_left_and_the_next_turn_is_told_once(rewinding):
    """Left without a word, the agent answers from the version its conversation
    remembers; told on every turn after, a line that changes nothing costs every one."""
    cfg = replace(rewinding, rewind_max_bytes=10)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    (_outputs(cfg) / "report.md").write_text("A" * 10, encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    (_outputs(cfg) / "report.md").write_text("B" * 10, encoding="utf-8")
    _say(kf, "rewrite it", AIMessage("rewritten"))

    done = kf.session("s").rewind(first.turn_id)

    assert (_outputs(cfg) / "report.md").read_text(encoding="utf-8") == "B" * 10
    assert "dropped for space" in done.not_restored["outputs/report.md"]
    Scripted.heard.clear()
    _say(kf, "what is in the report?", AIMessage("ok"))
    assert "Changed since your last turn: /outputs/report.md." in _asked()
    Scripted.heard.clear()
    _say(kf, "and now?", AIMessage("ok"))
    assert "Changed since your last turn" not in _asked()


def test_a_file_the_turn_did_not_have_goes_though_no_copy_holds_it(rewinding):
    """Left because no copy held it -- too large for the budget, or dropped for space -- a
    file a later turn made stayed beside files put back as the earlier turn left them, a
    folder no turn ever had."""
    cfg = replace(rewinding, rewind_max_bytes=100)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    outputs = _outputs(cfg)
    (outputs / "notes.md").write_text("notes", encoding="utf-8")
    first = _say(kf, "write notes", AIMessage("ok"))
    (outputs / "video.bin").write_bytes(b"v" * 1000)
    (outputs / "clip.bin").write_bytes(b"c" * 60)
    _say(kf, "render them", AIMessage("ok"))
    (outputs / "table.bin").write_bytes(b"t" * 90)
    third = _say(kf, "tabulate", AIMessage("ok"))

    back = kf.session("s").rewind(first.turn_id)

    assert sorted(one.name for one in outputs.iterdir()) == ["notes.md"]
    assert {"outputs/video.bin", "outputs/clip.bin", "outputs/table.bin"} <= set(back.restored)
    assert dict(back.not_restored) == {}
    # The price: forward again, only the version a copy holds comes back.
    forward = kf.session("s").rewind(third.turn_id)
    assert sorted(one.name for one in outputs.iterdir()) == ["notes.md", "table.bin"]
    assert "outputs/table.bin" in forward.restored
    assert {"outputs/video.bin", "outputs/clip.bin"} <= set(forward.not_restored)


def test_at_a_budget_of_0_a_rewind_leaves_the_folder_and_says_it_may_have_changed(rewinding):
    """At 0 nothing records the files in /outputs: put back by a guess they would be lost,
    and named file by file the next turn's message grows with the folder. The memory
    file, still kept, has to come back."""
    cfg = replace(rewinding, rewind_max_bytes=0)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    outputs = _outputs(cfg)
    memory = outputs.parent / "memory" / "AGENTS.md"
    memory.write_text("- Prefers tables\n", encoding="utf-8")
    (outputs / "plan.md").write_text("v1", encoding="utf-8")
    first = _say(kf, "plan it", AIMessage("ok"))
    memory.write_text("- Prefers tables\n- Is Bob\n", encoding="utf-8")
    (outputs / "plan.md").write_text("v2", encoding="utf-8")
    (outputs / "chart.png").write_bytes(b"png")
    _say(kf, "chart it", AIMessage("ok"))

    done = kf.session("s").rewind(first.turn_id)

    assert (outputs / "plan.md").read_text(encoding="utf-8") == "v2"
    assert (outputs / "chart.png").read_bytes() == b"png"
    assert memory.read_text(encoding="utf-8") == "- Prefers tables\n"
    assert dict(done.not_restored) == {"outputs": COPIES_OFF}
    (record,) = (one for one in kf.session("s").turns() if one.id == first.turn_id)
    assert record.files is not None
    assert record.files.unrecorded == ("memory", "outputs")
    Scripted.heard.clear()
    _say(kf, "and now?", AIMessage("ok"))
    assert "May have changed since your last turn: /outputs." in _asked()


def test_once_copies_resume_a_rewind_puts_the_files_back_again(rewinding):
    """A turn at 0 left the folders unknown to every rewind after it, though the turn that
    resumed copying named every file in them."""
    kf_off = Kingfisher(replace(rewinding, rewind_max_bytes=0), sessions=LocalSessions())
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    report = _outputs(rewinding) / "report.md"
    report.write_text("v1", encoding="utf-8")
    _say(kf_off, "write it", AIMessage("ok"))
    report.write_text("v2", encoding="utf-8")
    resumed = _say(kf, "rewrite it", AIMessage("ok"))
    report.write_text("v3", encoding="utf-8")
    _say(kf, "and again", AIMessage("ok"))

    done = kf.session("s").rewind(resumed.turn_id)

    assert report.read_text(encoding="utf-8") == "v2"
    assert "outputs/report.md" in done.restored


def test_the_memory_file_and_what_the_writer_has_not_read_go_back(rewinding):
    """Left, the memory would keep what the caller rewound past, and the writer would
    save facts from turns no longer on the path."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    folder = rewinding.workspace / "sessions" / "s"
    memory, unread = folder / "memory" / "AGENTS.md", folder / HARNESS / MEMORY_WRITER
    memory.write_text("- Prefers tables\n", encoding="utf-8")
    write_old_record(unread, json.dumps({"turns": 1, "said": ["User: hello"]}))
    first = _say(kf, "hello", AIMessage("hi"))
    kept_unread = unread.read_bytes()
    memory.write_text("- Prefers tables\n- Is Bob\n", encoding="utf-8")
    write_old_record(unread, json.dumps({"turns": 2, "said": ["User: I am Bob"]}))
    _say(kf, "I am Bob", AIMessage("noted"))

    kf.session("s").rewind(first.turn_id)

    assert memory.read_text(encoding="utf-8") == "- Prefers tables\n"
    assert read_record_bytes(unread) == kept_unread
    # Listed after the rewind as the listing marks it, not by its contents, the memory file
    # read as changed by the next turn, which never touched it.
    after = _say(kf, "and now?", AIMessage("ok"))
    (record,) = (one for one in kf.session("s").turns() if one.id == after.turn_id)
    assert record.files is not None
    assert "memory/AGENTS.md" not in record.files.changed


def test_a_rewind_to_the_start_leaves_the_files_and_tells_nothing(rewinding):
    """Nothing records the folders before the first turn: emptied on a guess, they lose
    a caller's own files; told, an empty conversation is warned about files it never
    saw."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    (_outputs(rewinding) / "report.md").write_text("LONG", encoding="utf-8")
    _say(kf, "write it", AIMessage("written"))

    done = kf.session("s").rewind(START)

    assert (_outputs(rewinding) / "report.md").read_text(encoding="utf-8") == "LONG"
    assert "before the first turn" in done.not_restored["outputs/report.md"]
    Scripted.heard.clear()
    _say(kf, "start over", AIMessage("ok"))
    assert "changed since your last turn" not in _sent().lower()


def test_the_files_alone_go_back_and_the_conversation_stays(rewinding):
    """Taken back with the files, a conversation the caller meant to keep is lost; and with
    the files compared at the next turn's end as the rewind left them, that turn recorded
    nothing, so a rewind back to it rebuilt the later files."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    outputs = _outputs(rewinding)
    (outputs / "report.md").write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    (outputs / "report.md").write_text("SHORT", encoding="utf-8")
    (outputs / "extra.md").write_text("more", encoding="utf-8")
    second = _say(kf, "shorten it", AIMessage("shortened"))
    kept = _conversation(rewinding)

    done = kf.session("s").rewind(first.turn_id, only="files")

    assert (outputs / "report.md").read_text(encoding="utf-8") == "LONG"
    assert not (outputs / "extra.md").exists()
    assert set(done.restored) == {"outputs/report.md", "outputs/extra.md"}
    assert _conversation(rewinding) == kept
    Scripted.heard.clear()
    third = _say(kf, "and now?", AIMessage("ok"))
    assert "Changed since your last turn: /outputs/extra.md, /outputs/report.md." in _asked()
    assert {one.id: one.parent for one in kf.session("s").turns()}[third.turn_id] == second.turn_id
    (outputs / "report.md").write_text("LATER", encoding="utf-8")
    _say(kf, "rewrite it", AIMessage("rewritten"))
    kf.session("s").rewind(third.turn_id)
    assert (outputs / "report.md").read_text(encoding="utf-8") == "LONG"
    assert not (outputs / "extra.md").exists()


def test_the_conversation_alone_goes_back_and_the_files_stay(rewinding):
    """Taken back with the conversation, files the caller meant to keep are lost; and with
    the files compared at the next turn's end as the rewind found them, that turn recorded
    nothing, so a rewind back to it rebuilt the files as the earlier turn had them."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    outputs = _outputs(rewinding)
    (outputs / "report.md").write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    after_first = _conversation(rewinding)
    (outputs / "report.md").write_text("SHORT", encoding="utf-8")
    (outputs / "extra.md").write_text("more", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))

    done = kf.session("s").rewind(first.turn_id, only="conversation")

    assert (outputs / "report.md").read_text(encoding="utf-8") == "SHORT"
    assert (outputs / "extra.md").exists()
    assert (done.restored, dict(done.not_restored)) == ((), {})
    assert _conversation(rewinding) == after_first
    Scripted.heard.clear()
    third = _say(kf, "and now?", AIMessage("ok"))
    assert "Changed since your last turn: /outputs/extra.md, /outputs/report.md." in _asked()
    (outputs / "report.md").write_text("LATER", encoding="utf-8")
    _say(kf, "rewrite it", AIMessage("rewritten"))
    kf.session("s").rewind(third.turn_id)
    assert (outputs / "report.md").read_text(encoding="utf-8") == "SHORT"
    assert (outputs / "extra.md").exists()


@pytest.mark.parametrize("only", ["files", "conversation"])
def test_what_was_heard_goes_with_the_conversation_and_the_writers_notes_with_the_memory(
    rewinding, tmp_path, only
):
    """With the wrong part, the agent is told of inputs against a conversation that never
    heard of them, or the memory writer saves into its file facts the file already holds,
    or ones said in turns the file was taken back from."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    harness = rewinding.workspace / "sessions" / "s" / HARNESS
    heard_file, unread = harness / INPUTS_SEEN, harness / MEMORY_WRITER
    (tmp_path / "a.csv").write_text("a", encoding="utf-8")
    (tmp_path / "b.csv").write_text("b", encoding="utf-8")
    write_old_record(unread, json.dumps({"turns": 1, "said": ["User: hello"]}))
    first = _say(kf, "read a", AIMessage("ok"), inputs=(tmp_path / "a.csv",))
    heard, kept_unread = heard_file.read_bytes(), unread.read_bytes()
    write_old_record(unread, json.dumps({"turns": 2, "said": ["User: I am Bob"]}))
    _say(kf, "read b", AIMessage("ok"), inputs=(tmp_path / "b.csv",))
    heard_later, unread_later = heard_file.read_bytes(), unread.read_bytes()
    assert (heard, kept_unread) != (heard_later, unread_later), "the control: nothing to take"

    kf.session("s").rewind(first.turn_id, only=only)

    if only == "files":
        assert heard_file.read_bytes() == heard_later
        assert read_record_bytes(unread) == kept_unread
    else:
        assert heard_file.read_bytes() == heard
        assert unread.read_bytes() == unread_later


def test_a_turn_after_a_rewind_to_the_start_is_rebuilt_with_every_file_it_found(rewinding):
    """Compared at its end with the folders as the rewind left them, the turn recorded only
    what it changed, and a rewind back to it removed every other file, the memory file
    too."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    outputs = _outputs(rewinding)
    memory = rewinding.workspace / "sessions" / "s" / "memory" / "AGENTS.md"
    (outputs / "a.md").write_text("a", encoding="utf-8")
    _say(kf, "one", AIMessage("ok"))
    kf.session("s").rewind(START)
    (outputs / "b.md").write_text("b", encoding="utf-8")
    again = _say(kf, "two", AIMessage("ok"))
    (outputs / "c.md").write_text("c", encoding="utf-8")
    _say(kf, "three", AIMessage("ok"))
    assert memory.exists(), "the control: no memory file to lose"

    kf.session("s").rewind(again.turn_id)

    assert sorted(one.name for one in outputs.iterdir()) == ["a.md", "b.md"]
    assert memory.exists()


def test_a_file_a_rewind_left_is_rebuilt_as_the_next_turn_found_it(rewinding):
    """Compared at its end with the folders as the rewind left them, the next turn did not
    record a file the rewind could not put back, so a rewind back to it found the earlier
    version's lack of a copy, blamed the budget, and left a version from another branch."""
    cfg = replace(rewinding, rewind_max_bytes=100)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    data = _outputs(cfg) / "data.bin"
    data.write_bytes(b"1" * 1000)
    first = _say(kf, "one", AIMessage("ok"))
    data.write_bytes(b"2" * 50)
    _say(kf, "two", AIMessage("ok"))
    kf.session("s").rewind(first.turn_id)
    assert data.read_bytes() == b"2" * 50, "the control: the rewind put it back"
    third = _say(kf, "three", AIMessage("ok"))
    data.write_bytes(b"4" * 50)
    _say(kf, "four", AIMessage("ok"))

    back = kf.session("s").rewind(third.turn_id)

    assert data.read_bytes() == b"2" * 50
    assert "outputs/data.bin" not in back.not_restored


def test_a_rewind_dying_after_the_path_leaves_the_next_turn_recording_what_it_found(
    rewinding, monkeypatch
):
    """With the listing saved only after the files, a rewind dying between left the next turn
    comparing the files with the later turn's, so a rewind back to it rebuilt them as the
    earlier turn had them."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    report = _outputs(rewinding) / "report.md"
    report.write_text("LONG", encoding="utf-8")
    first = _say(kf, "write it", AIMessage("written"))
    report.write_text("SHORT", encoding="utf-8")
    _say(kf, "shorten it", AIMessage("shortened"))

    def died(*_: Any) -> Any:
        msg = "the machine went away"
        raise OSError(msg)

    with monkeypatch.context() as patched:
        patched.setattr(rewind, "restore_state", died)
        with pytest.raises(OSError, match="went away"):
            kf.session("s").rewind(first.turn_id)
    assert report.read_text(encoding="utf-8") == "SHORT", "the control: the files went back"
    third = _say(kf, "and now?", AIMessage("ok"))
    report.write_text("LATER", encoding="utf-8")
    _say(kf, "rewrite it", AIMessage("rewritten"))

    kf.session("s").rewind(third.turn_id)

    assert report.read_text(encoding="utf-8") == "SHORT"


def test_a_copy_still_current_on_the_path_outlives_one_left_on_a_branch():
    """Ranked in the order the turns ran, the copy t1 kept looks superseded by t2's,
    though t2 was left behind and t1's is the version the path still has: dropped, a
    later rewind to t3 could not put it back."""
    def line(turn, size):
        kept = {"outputs/r.md": {"mark": turn, "bytes": size}}
        return {"turn": turn, "under": turn, "kept": kept}

    records = [line("t1", 10), line("t2", 10), {"turn": "t3", "under": "t3"}]
    parents = {"t1": None, "t2": "t1", "t3": "t1"}

    assert dropped_for_space(records, 10, parents) == [(1, "outputs/r.md")]
    # The control: in the order they ran, as before any rewind, t1's goes first.
    assert dropped_for_space(records, 10) == [(0, "outputs/r.md")]


def test_after_a_rewind_a_turn_keeps_the_copy_its_path_still_needs(rewinding):
    """Ranked in the order the turns ran, the copy of the version rewound to went first
    at the next turn over budget, and the session could not be put back there again."""
    cfg = replace(rewinding, rewind_max_bytes=25)
    kf = Kingfisher(cfg, sessions=LocalSessions())
    (_outputs(cfg) / "r.md").write_text("A" * 10, encoding="utf-8")
    first = _say(kf, "write it", AIMessage("ok"))
    (_outputs(cfg) / "r.md").write_text("B" * 10, encoding="utf-8")
    second = _say(kf, "rewrite it", AIMessage("ok"))
    kf.session("s").rewind(first.turn_id)
    (_outputs(cfg) / "n.md").write_text("C" * 10, encoding="utf-8")

    _say(kf, "add a note", AIMessage("ok"))

    history = cfg.workspace / "sessions" / "s" / HARNESS / FILE_HISTORY
    lines = {one["turn"]: one for one in map(json.loads, read_record(history).splitlines())}
    assert "dropped_in" not in lines[first.turn_id]["kept"]["outputs/r.md"]
    assert "dropped_in" in lines[second.turn_id]["kept"]["outputs/r.md"]


def test_a_turn_the_ledger_closed_and_the_log_never_opened_is_refused(rewinding):
    """A turn that died after the ledger and before the log is named by the ledger alone:
    told it has "no turn", the caller hears a turn it ran never happened, and a refusal
    written after anything would leave the session changed by a rewind that did not run."""
    kf = Kingfisher(rewinding, sessions=LocalSessions())
    _say(kf, "TASK-FIRST", AIMessage("ANSWER-FIRST"))
    died = _say(kf, "TASK-SECOND", AIMessage("ANSWER-SECOND"))
    log = rewinding.workspace / "sessions" / "s" / HARNESS / TURNS_FILE
    lines = read_record(log).splitlines(keepends=True)
    write_old_record(log, "".join(one for one in lines if json.loads(one)["turn"] != died.turn_id))
    kept = _harness_files(rewinding)

    with pytest.raises(RewindError, match="wrote no conversation to go back to"):
        kf.session("s").rewind(died.turn_id)

    assert _harness_files(rewinding) == kept

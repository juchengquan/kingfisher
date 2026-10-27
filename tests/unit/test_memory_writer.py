"""The memory writer: when it runs, what it is shown, what it may save, and how it fails."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from langchain_core.messages import AIMessage, SystemMessage

from kingfisher import Kingfisher, MemoryEntry, Request
from kingfisher.core.config import ConfigError
from kingfisher.core.interfaces import TurnInstructions
from kingfisher.core.layout import HARNESS, MEMORY_WRITER
from kingfisher.core.model_catalogue import load
from kingfisher.core.session.memory import listed
from kingfisher.core.session.memory_writer import MAX_CHARACTERS, MAX_ENTRIES, writer_model
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start
from tests.unit.scripted import Scripted

#: The opening words of the writer's instructions, which tell its call from the agent's.
WRITER = "You keep the memory of one conversation"


def _writing(scripted, *, wiring: dict[str, Any] | None = None, **settings: Any) -> Kingfisher:
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    cfg = replace(scripted, memory_enabled=True, **settings)
    return Kingfisher(cfg, sessions=LocalSessions(), **(wiring or {}))


def _memory_file(scripted):
    return scripted.workspace / "sessions" / "s" / "memory" / "AGENTS.md"


def _turn(kf: Kingfisher, *script: Any, task: str = "go", **request: Any):
    Scripted.script.extend(script)
    events = list(kf.stream(Request(task, agent="only", session_id="s", **request)))
    (finished,) = [one for one in events if one.kind == "finished"]
    return finished.result, events


def _writer_calls() -> list[list[Any]]:
    return [
        call
        for call in Scripted.heard
        if isinstance(call[0], SystemMessage) and call[0].text.startswith(WRITER)
    ]


def _shown(call: list[Any]) -> str:
    """Everything one call was sent, as one text."""
    return "\n".join(message.text for message in call)


def _kinds(events, kind: str) -> list[str]:
    return [one.text for one in events if one.kind == kind]


# -- when it runs --------------------------------------------------------------


def test_the_writer_runs_on_the_fourth_turn_by_default_and_not_before(scripted):
    """A counter that never advanced would leave memory uncurated; one read as `>= 0`
    would cost a call on every "thanks"; a default back at 2 would cost the next turn
    most of its cache twice as often."""
    kf = _writing(scripted)

    for said in ("one", "two", "three"):
        _turn(kf, AIMessage(content=said))
    assert _writer_calls() == [], "the writer ran before the fourth turn"

    _turn(kf, AIMessage(content="four"), AIMessage(content="- Likes EUR"))
    assert len(_writer_calls()) == 1, "the writer did not run on the fourth turn"

    _turn(kf, AIMessage(content="five"))
    assert len(_writer_calls()) == 1, "the count did not start again after the writer ran"


def test_memory_every_sets_how_many_turns_it_waits(scripted):
    kf = _writing(scripted, memory_every=3)

    _turn(kf, AIMessage(content="one"))
    _turn(kf, AIMessage(content="two"))
    assert _writer_calls() == []
    _turn(kf, AIMessage(content="three"), AIMessage(content="- Likes EUR"))

    assert len(_writer_calls()) == 1


#: A window the short turns stay inside, and a question half of it: one turn under
#: deepagents' trigger, two past it. As `test_ledger.py` drives the real summariser.
WINDOW = 80_000
HALF = "q" * 160_000


def _windowed(cfg):
    models = {
        name: replace(one, extra={"profile": {"max_input_tokens": WINDOW}})
        for name, one in cfg.models.models.items()
    }
    return replace(cfg, models=replace(cfg.models, models=models))


def test_it_runs_on_a_summarised_turn_and_reads_what_the_summary_dropped(scripted):
    """Driven through deepagents' real summariser, since a mark set by hand passes
    whatever the key is called; the first turn's reply, dropped by the summary, must
    still reach the writer, or a fact said once before a summary is lost for good."""
    kf = _writing(_windowed(scripted), memory_every=10)

    _turn(kf, AIMessage(content="first reply: totals in EUR"), task=HALF)
    assert Scripted.summaries == 0, "the control: the first turn was summarised"
    assert _writer_calls() == []

    _turn(kf, AIMessage(content="second reply"), AIMessage(content="- Likes EUR"), task=HALF)

    assert Scripted.summaries == 1, "the summariser never fired"
    (call,) = _writer_calls()
    assert "first reply: totals in EUR" in _shown(call)
    assert "second reply" in _shown(call)


def test_with_memory_off_it_never_runs(scripted):
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    kf = Kingfisher(replace(scripted, memory_every=1), sessions=LocalSessions())

    for said in ("one", "two", "three"):
        _turn(kf, AIMessage(content=said))

    assert _writer_calls() == []
    assert not (scripted.workspace / "sessions" / "s" / HARNESS / MEMORY_WRITER).exists()


# -- what it saves ---------------------------------------------------------------


def test_its_revised_list_is_written_numbered_and_reported(scripted):
    kf = _writing(scripted, memory_every=2)
    memory = _memory_file(scripted)
    memory.write_text("# Project memory\n\n- [m1] Short answers\n", encoding="utf-8")

    _turn(kf, AIMessage(content="one"))
    result, events = _turn(
        kf,
        AIMessage(content="two"),
        AIMessage(content="- [m1] Short answers, figures in EUR\n- Reviews on Mondays"),
    )

    assert memory.read_text(encoding="utf-8") == (
        "# Project memory\n\n- [m1] Short answers, figures in EUR\n- [m2] Reviews on Mondays\n"
    )
    assert result.memory.changed == (MemoryEntry("m1", "Short answers, figures in EUR"),)
    assert result.memory.added == (MemoryEntry("m2", "Reviews on Mondays"),)
    assert _kinds(events, "memory_updated") == ["added m2; changed m1"]


def test_a_new_entry_never_takes_the_id_of_one_the_writer_dropped(scripted):
    """Numbered from the answer alone, the new entry would be `m2` again and reported as
    `m2` changed, where `m2` was removed and something else added."""
    kf = _writing(scripted, memory_every=2)
    _memory_file(scripted).write_text("- [m1] Short answers\n- [m2] Likes GBP\n", encoding="utf-8")

    _turn(kf, AIMessage(content="one"))
    result, _ = _turn(
        kf, AIMessage(content="two"), AIMessage(content="- [m1] Short answers\n- Likes EUR")
    )

    assert result.memory.removed == (MemoryEntry("m2", "Likes GBP"),)
    assert result.memory.added == (MemoryEntry("m3", "Likes EUR"),)
    assert not result.memory.changed


def test_an_id_the_list_never_held_is_numbered_afresh():
    """Kept as written, an invented `m1` would read as an existing entry changed."""
    before = (MemoryEntry("m4", "Short answers"),)

    assert listed("- [m4] Short answers\n- [m1] Likes EUR", before) == (
        MemoryEntry("m4", "Short answers"),
        MemoryEntry("m5", "Likes EUR"),
    )


def test_a_fenced_list_is_read_and_none_keeps_nothing():
    assert listed("```\n- Likes EUR\n```", ()) == (MemoryEntry("m1", "Likes EUR"),)
    assert listed("none", (MemoryEntry("m1", "Likes EUR"),)) == ()


# -- how it fails ----------------------------------------------------------------


OVER_COUNT = "\n".join(f"- fact {n}" for n in range(MAX_ENTRIES + 1))
OVER_SIZE = "- " + "x" * MAX_CHARACTERS


@pytest.mark.parametrize(
    ("answer", "why"),
    [
        (OVER_COUNT, f"held {MAX_ENTRIES + 1} entries, over the {MAX_ENTRIES} kept"),
        (OVER_SIZE, f"over the {MAX_CHARACTERS:,} kept"),
        ("Here is the revised list:\n- Likes EUR", "not a list of entries"),
        ("", "it answered nothing"),
        (RuntimeError("provider unreachable"), "its call failed"),
    ],
    ids=["too-many", "too-long", "malformed", "empty", "failed"],
)
def test_an_answer_not_kept_leaves_memory_as_it_was_and_says_why(scripted, answer, why):
    """A half-written memory is worse than a stale one, and a turn whose answer is in
    hand must not fail because the writer did."""
    kf = _writing(scripted, memory_every=2)
    memory = _memory_file(scripted)
    memory.write_text("# Project memory\n\n- [m1] Short answers\n", encoding="utf-8")

    _turn(kf, AIMessage(content="one"))
    result, events = _turn(
        kf, AIMessage(content="two"), answer if isinstance(answer, Exception) else AIMessage(answer)
    )

    assert len(_writer_calls()) == 1, "the control: the writer never ran"
    assert result.answer == "two"
    assert result.completed
    assert memory.read_text(encoding="utf-8") == "# Project memory\n\n- [m1] Short answers\n"
    assert not result.memory
    (said,) = _kinds(events, "memory_not_updated")
    assert why in said


# -- the identifier check --------------------------------------------------------


def test_an_entry_holding_a_context_value_is_dropped_and_reported(scripted):
    """Compared ignoring case; a value under four characters is no identifier, or "en"
    would drop every entry with "when" in it."""
    kf = _writing(scripted, memory_every=2)
    context = {"team": "Payments Platform", "lang": "en"}

    _turn(kf, AIMessage(content="one"), context=context)
    result, events = _turn(
        kf,
        AIMessage(content="two"),
        AIMessage(content="- Works in the payments platform team\n- Answers when asked\n"),
        context=context,
    )

    assert result.memory.dropped == (MemoryEntry("m1", "Works in the payments platform team"),)
    assert result.memory.added == (MemoryEntry("m2", "Answers when asked"),)
    assert not result.memory.removed
    assert "payments" not in _memory_file(scripted).read_text(encoding="utf-8").lower()
    assert _kinds(events, "memory_updated") == ["added m2; dropped m1"]


def test_an_entry_the_agent_saved_holding_a_context_value_is_dropped_too(scripted):
    """The trial the check is for: told not to save the user's team, the agent saved it.
    On a turn the writer does not run, so the check is not the writer's alone."""
    kf = _writing(scripted, memory_every=2)
    edit = {
        "file_path": "/memory/AGENTS.md",
        "old_string": "# Project memory\n",
        "new_string": "# Project memory\n\n- On the Payments Platform team\n- Likes EUR\n",
    }

    result, _ = _turn(
        kf,
        AIMessage(content="", tool_calls=[{"name": "edit_file", "args": edit, "id": "c1"}]),
        AIMessage(content="noted"),
        context={"team": "payments platform"},
    )

    assert _writer_calls() == [], "the control: the writer ran on the first turn"
    assert result.memory.dropped == (MemoryEntry("m1", "On the Payments Platform team"),)
    assert result.memory.added == (MemoryEntry("m2", "Likes EUR"),)
    assert "Payments" not in _memory_file(scripted).read_text(encoding="utf-8")


# -- what it is shown --------------------------------------------------------------


class _Told(TurnInstructions):
    def text(self, cfg, session_id: str, caller) -> str:
        return "TURN-TEXT: the user is Ada Lovelace"


def test_the_writer_is_never_sent_the_turn_text_the_note_or_a_tool_result(scripted):
    """The turn text is where a deployment says who the user is; a writer never shown it
    cannot copy it. Read off what the scripted model was actually handed, with the
    agent's own call as the control that each of them was there to leak."""
    kf = _writing(scripted, memory_every=2, wiring={"turn_instructions": _Told()})
    (scripted.workspace / "sessions" / "s" / "outputs" / "a.txt").write_text(
        "TOOL-RESULT-7781", encoding="utf-8"
    )
    read = {"name": "read_file", "args": {"file_path": "/outputs/a.txt"}, "id": "c1"}

    _turn(kf, AIMessage(content="one"), task="first task")
    _turn(
        kf,
        AIMessage(content="", tool_calls=[read]),
        AIMessage(content="it says what it says"),
        AIMessage(content="- Likes EUR"),
        task="read a.txt",
        note="NOTE-FOR-THIS-TURN",
    )

    (call,) = _writer_calls()
    agents = [one for one in Scripted.heard if one is not call]
    for leak in ("TURN-TEXT", "TOOL-RESULT-7781", "NOTE-FOR-THIS-TURN", "You do the task."):
        assert any(leak in _shown(one) for one in agents), f"the control: {leak} never sent"
        assert leak not in _shown(call), f"the writer was sent {leak}"
    shown = _shown(call)
    assert "User: first task" in shown
    assert "User: read a.txt" in shown
    assert "Agent called: read_file" in shown
    assert "Agent: it says what it says" in shown


# -- its model -------------------------------------------------------------------


def test_memory_writer_names_the_model_and_its_absence_means_the_agents(scripted):
    running = object()

    assert writer_model(scripted, running, "*")() is running
    named = replace(scripted, models=replace(scripted.models, memory_writer="cheap-model"))
    assert writer_model(named, running, "*")().model == "cheap-model"


def test_a_memory_writer_naming_no_model_is_refused(tmp_path):
    body = """
endpoints:
  gateway:
    api: anthropic
    base_url: https://example.invalid/anthropic
    key_env: GATEWAY_API_KEY

default: main-model
memory_writer: mian-model

models:
  main-model:
    endpoint: gateway
"""
    path = tmp_path / "models.yaml"
    path.write_text(body, encoding="utf-8")

    with pytest.raises(ConfigError, match="memory_writer model 'mian-model' is not defined"):
        load(path, {"GATEWAY_API_KEY": "sk"})
    path.write_text(body.replace("mian-model", "main-model"), encoding="utf-8")
    assert load(path, {"GATEWAY_API_KEY": "sk"}).memory_writer == "main-model"


def test_with_memory_on_the_claim_outlasts_the_writers_call(cfg):
    """The writer calls its model after the turn's last superstep, under the claim; a
    claim sized without it lets a second turn in while the writer is still saving."""
    on = replace(cfg, memory_enabled=True)
    longest = max(profile.timeout_s for profile in cfg.models.models.values())

    assert on.claim_stale_after == cfg.claim_stale_after + longest

"""`scripts/session_survey.py` reads the files a session keeps, by their names."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.layout import HARNESS, LEDGER_FILE, MEMORY_WRITER
from kingfisher.core.session.steps import drive
from kingfisher.core.session.store import SECOND_COPY, HarnessFiles
from kingfisher.plugins import LocalSessions
from tests.conftest import EVERY_TOOL, an_agent, start, tools_dir
from tests.unit.scripted import Scripted

SURVEY = Path(__file__).resolve().parents[3] / "scripts" / "session_survey.py"

#: Long enough to be filed as it arrives, so the turn leaves a result file beside the
#: transcript's preview of it: a short result has none until it leaves the conversation.
TOOL = '''
def web_search(query: str) -> str:
    """Search the web."""
    return f"Ten results for {query}\\n" + "a result line\\n" * 7_000


TOOLS = [web_search]
'''


def _survey() -> ModuleType:
    """The script as a module: it is no package's, so it runs where kingfisher is not."""
    spec = importlib.util.spec_from_file_location("session_survey", SURVEY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclasses resolve their annotations through it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_the_survey_reads_what_a_turn_leaves_behind(scripted):
    """The survey copies kingfisher's file names and formats rather than importing them, so
    a file renamed here would leave it reporting a session full of turns as an empty one;
    and reading files kept twice as one, it counted each seal as a message, lost the memory
    writer's count, and filed each second copy as the rest of `.harness`.
    """
    tools_dir(scripted).mkdir(parents=True, exist_ok=True)
    (tools_dir(scripted) / "probe.py").write_text(TOOL, encoding="utf-8")
    an_agent(scripted, **EVERY_TOOL)
    start(scripted, "s")
    Scripted.script.extend([
        AIMessage(
            content="",
            tool_calls=[{"name": "web_search", "args": {"query": "EV prices"}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ])
    kf = Kingfisher(scripted, sessions=LocalSessions())
    kf.run(Request("go", agent="only", session_id="s"))
    # Saved as the memory writer saves it, which no turn here runs.
    drive(HarnessFiles(kf.session("s").files()).store(MEMORY_WRITER, b'{"turns": 3}'))

    found = _survey().read_session(scripted.workspace / "sessions" / "s")

    assert len(found.turns) == 1
    assert found.recorded_results == 1
    assert found.chars["user"] and found.chars["tool results"]
    assert found.repeated > 0, "the result's file and the transcript both hold it"
    for part in ("result files", "transcript", "ledger"):
        assert found.disk[part], part
    assert found.disk["other"] == 0, "a file the survey has no part for"
    assert "?" not in found.messages, "a seal read as a message"
    assert found.unread_turns == 3
    harness = scripted.workspace / "sessions" / "s" / HARNESS
    copies = (harness / LEDGER_FILE, harness / f"{LEDGER_FILE}{SECOND_COPY}")
    assert found.disk["ledger"] == sum(copy.stat().st_size for copy in copies)

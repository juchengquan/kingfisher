"""`scripts/turn_deadline_survey.py` reads the run log kingfisher writes, by its names."""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path
from types import ModuleType

from langchain_core.messages import AIMessage

from kingfisher import Kingfisher, Request
from kingfisher.plugins import JsonlRunEvents, LocalSessions
from tests.conftest import an_agent, start, subagents_dir, tools_dir
from tests.unit.scripted import Scripted

SURVEY = Path(__file__).resolve().parents[2] / "scripts" / "turn_deadline_survey.py"

#: Long enough that, of three in a row, the third begins past a one-second limit.
LOOKUP = '''import time


def lookup(n: int) -> str:
    """Look a customer up."""
    time.sleep(0.6)
    return f"row{n}"


TOOLS = [lookup]
'''

HELPER = """name: helper
description: Looks customers up.
tools: [lookup]
system_prompt: |
  You look customers up.
"""


def _survey() -> ModuleType:
    """The script as a module: it is no package's, so it runs where kingfisher is not."""
    spec = importlib.util.spec_from_file_location("turn_deadline_survey", SURVEY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Registered first: its dataclasses resolve their annotations through it.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _call(name: str, cid: str, **args: object) -> AIMessage:
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": cid}])


def test_the_survey_reads_what_a_turn_past_its_limit_logs(scripted, tmp_path):
    """The survey copies kingfisher's event and field names rather than importing them, so
    one renamed here would leave it reporting no program and no subagent: the deployment's
    log would read as nothing ever running past its limit."""
    bounded = replace(scripted, interpreter_enabled=True, turn_timeout_s=1)
    tools_dir(bounded).mkdir(parents=True, exist_ok=True)
    (tools_dir(bounded) / "lookup.py").write_text(LOOKUP, encoding="utf-8")
    subagents_dir(bounded).mkdir(parents=True, exist_ok=True)
    (subagents_dir(bounded) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(
        bounded, builtin_tools="[eval, task, write_file]", tools="[lookup]", subagents="[helper]"
    )
    start(bounded, "s")
    log = tmp_path / "run.jsonl"
    kf = Kingfisher(bounded, sessions=LocalSessions(), run_events=JsonlRunEvents(log))

    # A program whose third lookup and whose write both begin past the limit.
    Scripted.script.extend(
        [
            _call(
                "eval",
                "e1",
                code="(async () => { for (let i = 1; i <= 3; i++) await tools.lookup({n: i});"
                " return await tools.writeFile("
                "{file_path: '/outputs/late.md', content: 'x'}); })()",
            ),
            AIMessage(content="done"),
        ]
    )
    kf.run(Request("go", agent="only", session_id="s"))
    # Cut short, the turn left the model's last reply unread.
    Scripted.script.clear()
    # A program handing a subagent three lookups, the third begun past the limit and the two
    # model calls after the second and third lookups too.
    Scripted.script.extend(
        [
            _call(
                "eval",
                "e2",
                code="(async () => await task({description: 'look up', subagentType: 'helper'}))()",
            ),
            *(_call("lookup", f"h{n}", n=n) for n in range(1, 4)),
            AIMessage(content="helped"),
            AIMessage(content="done"),
        ]
    )
    kf.run(Request("again", agent="only", session_id="s"))

    survey = _survey()
    report = survey.survey(survey.read_turns([log]), limit=1, grace=0.5)

    turns, programs, subagents = report["turns"], report["programs"], report["subagents"]
    assert (turns["n"], turns["stopped_by_their_limit"]) == (2, 2)
    assert turns["ran_past_the_claim"] == 2
    assert turns["running_when_the_claim_lapsed"] == {"a program": 2}
    assert programs["n"] == 2
    assert programs["tool_calls_in_one"]["max"] == 4
    assert programs["offered_calls_begun_after_the_limit"] == 1
    assert programs["built_in_calls_begun_after_the_limit"] == 1
    assert (subagents["n"], subagents["started_from_a_program"]) == (1, 1)
    assert subagents["tool_calls_begun_after_the_limit"] == 1
    assert subagents["model_calls_begun_after_the_limit"] == 2
    # What it prints is counts: no id, and no offered tool's name.
    printed = survey.render(report)
    assert "lookup" not in printed and "helper" not in printed

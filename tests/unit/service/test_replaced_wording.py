"""A deployment's replacements reach every place a turn says a piece, and only its own
turns."""

from __future__ import annotations

import asyncio
import logging

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher
from kingfisher.core.logged import WARNINGS_LOGGER
from kingfisher.core.request import Request
from kingfisher.plugins import LocalSessions
from tests.conftest import an_agent, start
from tests.unit.scripted import Scripted

REFUSED = "refusals/tool_not_available"


def _replace(cfg, name: str, text: str) -> None:
    path = cfg.definitions / "prompts" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _refused_grep(kf: Kingfisher, cfg, *, run_async: bool = False) -> str:
    """What a turn's call to a tool it was not granted was told."""
    Scripted.script.extend(
        [
            AIMessage("", tool_calls=[{"name": "grep", "args": {"pattern": "x"}, "id": "c1"}]),
            AIMessage("done"),
        ]
    )
    asked = Request("go", agent="only", session_id=start(cfg, f"s{len(Scripted.heard)}"))
    if run_async:
        asyncio.run(kf.arun(asked))
    else:
        kf.run(asked)
    return next(one for one in Scripted.heard[-1] if isinstance(one, ToolMessage)).text


@pytest.mark.parametrize("run_async", [False, True], ids=["run", "arun"])
def test_a_replacement_reaches_what_a_turn_says_on_its_graph_threads(scripted, run_async):
    """The graph runs in a context copied from the turn's, by a different block on each
    loop: one that missed it sent kingfisher's English, with the replacement accepted."""
    an_agent(scripted, builtin_tools="[read_file]")
    _replace(scripted, REFUSED, "拒绝 {{tool}} ({{subject}}); 可用: {{tools}}.")
    kf = Kingfisher(scripted, sessions=LocalSessions())

    told = _refused_grep(kf, scripted, run_async=run_async)

    assert told.startswith("Error: 拒绝 grep (this request); 可用: read_file.")


def test_a_replacement_reaches_the_prompt_and_the_task(scripted):
    """The system prompt is cached across turns and the task's notes are written before the
    graph exists; either reading kingfisher's own would leave half a turn in English."""
    an_agent(scripted)
    _replace(scripted, "session", "会话: /{{inputs}} /{{outputs}} /{{scratchpad}}")
    _replace(scripted, "turn/scratchpad", "草稿 /{{scratchpad}}/ ({{scratchpad}}).")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    Scripted.script.append(AIMessage("done"))

    kf.run(Request("go", agent="only", session_id=start(scripted, "s")))

    system, *_, task = Scripted.heard[-1]
    assert "会话: /inputs /outputs /scratchpad" in system.text
    assert "草稿 /scratchpad/ (scratchpad)." in task.text


def test_an_edit_reaches_the_next_turn_and_a_broken_one_never_does(scripted, caplog):
    """Read once, an edit waited for a restart; swapped in unchecked, a broken one would
    have failed every turn after it."""
    an_agent(scripted, builtin_tools="[read_file]")
    _replace(scripted, REFUSED, "first {{tool}} {{subject}} {{tools}}")
    kf = Kingfisher(scripted, sessions=LocalSessions())
    assert _refused_grep(kf, scripted).startswith("Error: first grep")

    _replace(scripted, REFUSED, "second {{tool}} {{subject}} {{tools}}")
    assert _refused_grep(kf, scripted).startswith("Error: second grep")

    _replace(scripted, REFUSED, "third, which forgot its blanks")
    with caplog.at_level(logging.WARNING, logger=WARNINGS_LOGGER):
        assert _refused_grep(kf, scripted).startswith("Error: second grep")
    assert any(
        one.name == WARNINGS_LOGGER and one.kingfisher["event"] == "definitions_refused"
        for one in caplog.records
    )


def test_two_deployments_in_one_process_keep_their_own_wording(scripted, tmp_path_factory):
    """The rendered prompt is cached by what it is rendered from; keyed without the
    wording, the second deployment was sent the first one's."""
    other = scripted.__class__(
        **{
            **scripted.__dict__,
            "workspace": tmp_path_factory.mktemp("ws2"),
            "definitions": tmp_path_factory.mktemp("defs2"),
        }
    )
    told = {}
    for cfg, word in ((scripted, "甲"), (other, "乙")):
        an_agent(cfg)
        _replace(cfg, "session", f"{word} /{{{{inputs}}}} /{{{{outputs}}}} /{{{{scratchpad}}}}")
        kf = Kingfisher(cfg, sessions=LocalSessions())
        system = kf.sent(Request("go", agent="only", session_id="s")).system
        told[word] = "".join(one.text for one in system)

    assert "甲 /inputs" in told["甲"]
    assert "乙 /inputs" in told["乙"]

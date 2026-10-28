"""deepagents' file tools as kingfisher hands them to a model, driven through real turns."""

from __future__ import annotations

import os
from typing import Any

import pytest
from deepagents.middleware.filesystem import (
    EDIT_FILE_TOOL_DESCRIPTION,
    EXECUTE_TOOL_DESCRIPTION,
    GREP_TOOL_DESCRIPTION,
    LIST_FILES_TOOL_DESCRIPTION,
    WRITE_FILE_TOOL_DESCRIPTION,
)
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.agent.middlewares.builtin_tools import (
    ABSOLUTE,
    BACKENDS_ONLY,
    EDIT_FILE,
    GREP,
    JOINED,
    LS,
    MUST_READ,
    OFFLOADED_ELSEWHERE,
    READ_FIRST,
    READ_LINES,
    SANDBOXED,
    SEPARATELY,
    WHOLE,
    WRITE_FILE,
    BuiltinToolsMiddleware,
    alternatives,
    timeout_line,
)
from kingfisher.core.agent.prompting import EXECUTE, execute_line
from kingfisher.core.layout import DEFAULT_LAYOUT
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import EVERY_TOOL, an_agent, hosts_shell, start, subagents_dir
from tests.unit.scripted import Scripted

HELPER = (
    "name: helper\ndescription: Helps.\n"
    'builtin_tools: ["*"]\ntools: ["*"]\n'
    "system_prompt: |\n  You help.\n"
)


@pytest.fixture
def reading(scripted, monkeypatch):
    """An agent consulting a subagent, a 1,200-line file in the session, and the tools each
    model call was offered kept in order."""
    offered: list[list[Any]] = []

    def bind(self, tools, **kwargs):
        offered.append(list(tools))
        return self

    monkeypatch.setattr(Scripted, "bind_tools", bind)
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "helper.yaml").write_text(HELPER, encoding="utf-8")
    an_agent(scripted, subagents="[helper]", **EVERY_TOOL)
    start(scripted, "s")
    outputs = scripted.workspace / "sessions" / "s" / "outputs"
    (outputs / "long.txt").write_text("".join(f"row {n:04d}\n" for n in range(1_200)))
    (outputs / "notes.txt").write_text("alpha one\nbeta two\ngamma three\n| a |\n")
    return scripted, offered


def _turn(cfg: Any, *script: AIMessage) -> None:
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    service.run(request)


def _read(at: str, **args: Any) -> AIMessage:
    call = {"name": "read_file", "args": {"file_path": "/outputs/long.txt", **args}, "id": at}
    return AIMessage(content="", tool_calls=[call])


def _answer(at: str) -> str:
    heard = Scripted.heard[-1]
    (said,) = [one.text for one in heard if isinstance(one, ToolMessage) and one.tool_call_id == at]
    return said


def test_a_read_naming_no_limit_reads_a_thousand_lines(reading):
    """At deepagents' 100 lines, paging through files was a third of every call a turn made."""
    cfg, _ = reading

    _turn(cfg, _read("r1"), AIMessage("done"))

    said = _answer("r1")
    assert "row 0999" in said
    assert "row 1000" not in said


def test_a_limit_the_model_names_is_left_as_it_is(reading):
    """A model asking for a slice it wants gets that slice, not a thousand lines."""
    cfg, _ = reading

    _turn(cfg, _read("r1", limit=50), AIMessage("done"))

    said = _answer("r1")
    assert "row 0049" in said
    assert "row 0050" not in said


@pytest.mark.parametrize("subagent", ["helper", "general-purpose"])
def test_every_graph_is_told_to_read_a_file_whole_and_shown_the_default(reading, subagent):
    """Told 100 lines and to page, most reads named a limit of 100 themselves, so a default
    the description does not mention would change almost nothing."""
    cfg, offered = reading
    asked = {"description": "look", "subagent_type": subagent}
    consulting = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    _turn(cfg, consulting, AIMessage("looked"), AIMessage("done"))

    assert len(offered) == 3
    for tools in offered:
        (read,) = [tool for tool in tools if getattr(tool, "name", None) == "read_file"]
        assert WHOLE in read.description
        assert "instead of reading them whole" not in read.description, "deepagents reworded it"
        assert read.args["limit"]["default"] == READ_LINES
        (grep,) = [tool for tool in tools if getattr(tool, "name", None) == "grep"]
        assert JOINED in grep.description
        assert "run a separate grep for each" not in grep.description, "deepagents reworded it"


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("call.mp3", ("audio", "audio")),
        ("clip.mp4", ("video", "video")),
        ("clip.mkv", ("video", "video")),
        ("deck.pptx", ("a .pptx file", ".pptx files")),
    ],
)
def test_read_file_is_not_run_on_a_file_no_model_here_takes(reading, name, said):
    """Read, audio and video reach the model as one string of base64 millions of
    characters long, and a `.pptx` as a block the summarizer counts and the request drops.
    """
    cfg, _ = reading
    what, reads = said
    outputs = cfg.workspace / "sessions" / "s" / "outputs"
    (outputs / name).write_bytes(os.urandom(64 * 1024))

    _turn(cfg, _read("r1", file_path=f"/outputs/{name}"), AIMessage("done"))

    (answer,) = [
        one
        for one in Scripted.heard[-1]
        if isinstance(one, ToolMessage) and one.tool_call_id == "r1"
    ]
    assert answer.text == (
        f"Error: /outputs/{name} is {what}, which can't be handed to the model here. If you "
        f"hold a tool that can read {reads}, call it on /outputs/{name}; otherwise say that "
        "this part could not be done."
    )
    assert answer.status == "error"


def _grep(at: str, pattern: str) -> AIMessage:
    args = {"pattern": pattern, "path": "/outputs", "output_mode": "content"}
    return AIMessage(content="", tool_calls=[{"name": "grep", "args": args, "id": at}])


def test_a_pattern_joining_strings_with_a_bar_searches_for_each(reading):
    """`grep` is literal, and 63 of 97 searches that found nothing were `a|b` written anyway."""
    cfg, _ = reading

    _turn(cfg, _grep("g1", "alpha|beta"), AIMessage("done"))

    said = _answer("g1")
    assert said.startswith("Searched `alpha|beta` as separate literal parts.")
    alpha, beta = said.split("matches for `beta`:")
    assert "matches for `alpha`:" in alpha and "alpha one" in alpha
    assert "beta two" in beta


def test_a_pattern_with_an_empty_part_is_searched_as_written(reading):
    """A table cell, `| a |`, is a literal search for the cell, not three for nothing."""
    cfg, _ = reading

    _turn(cfg, _grep("g1", "| a |"), AIMessage("done"))

    said = _answer("g1")
    assert "| a |" in said
    assert "separate literal parts" not in said


def test_only_strings_all_of_them_there_are_split():
    """An empty part would search for everything, so a pattern with one is left whole."""
    assert alternatives("run_start|run_end") == ["run_start", "run_end"]
    assert alternatives("def run|def stream|x") == ["def run", "def stream", "x"]
    assert alternatives("a||b") is None
    assert alternatives("|a") is None
    assert alternatives("plain") is None


def test_it_adds_no_step_to_the_agents_loop(reading):
    """langgraph's recursion limit counts graph steps, and a hook beside the model call is one."""
    cfg, _ = reading
    backend = backend_at(cfg, cfg.workspace / "sessions" / "s")

    nodes = build_agent(cfg, backend=backend).graph.get_graph().nodes

    assert "TodoListMiddleware.after_model" in nodes, "hooks are not nodes named for their class"
    assert not [one for one in nodes if BuiltinToolsMiddleware.__name__ in one]


# -- which tools each description names -----------------------------------


@pytest.mark.parametrize("subagent", ["helper", "general-purpose"])
def test_an_unrestricted_graph_is_told_what_deepagents_says_but_the_false_claim(
    reading, subagent
):
    """Narrowing for every graph, the rewrite would change what an agent holding every tool
    was told, which only the untrue lines should: `edit_file`'s claim, and `execute`'s
    sandbox and its note on backends."""
    cfg, offered = reading
    rule = execute_line(hosts_shell(cfg), DEFAULT_LAYOUT)
    assert rule is not None
    asked = {"description": "look", "subagent_type": subagent}
    consulting = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    _turn(cfg, consulting, AIMessage("looked"), AIMessage("done"))

    assert len(offered) == 3
    for tools in offered:
        said = {getattr(tool, "name", None): tool.description for tool in tools}
        timed = timeout_line(cfg.execution_timeout_s)
        told = (
            EXECUTE_TOOL_DESCRIPTION.replace(SANDBOXED, "")
            .replace(BACKENDS_ONLY, "")
            .replace(ABSOLUTE, rule)
        )
        assert said[EXECUTE] == f"{told}\n{timed}"
        # And deepagents' sentence on the offload folder is dropped, not reworded: the
        # offloading it describes is off, so it would send a search to an empty folder.
        assert said[GREP] == (
            GREP_TOOL_DESCRIPTION.replace(SEPARATELY, JOINED)
            .replace(OFFLOADED_ELSEWHERE, "")
            .rstrip()
        )
        assert "large_tool_results" not in said[GREP]
        assert said[LS] == LIST_FILES_TOOL_DESCRIPTION
        assert said[WRITE_FILE] == WRITE_FILE_TOOL_DESCRIPTION.strip()
        assert READ_FIRST in said[EDIT_FILE]
        assert said[EDIT_FILE] == EDIT_FILE_TOOL_DESCRIPTION.replace(MUST_READ, READ_FIRST)

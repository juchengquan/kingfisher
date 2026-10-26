"""deepagents' file tools as kingfisher hands them to a model, driven through real turns."""

from __future__ import annotations

import asyncio
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
    EXACT_INDENT,
    FROM_READ,
    GREP,
    JOINED,
    LS,
    MUST_READ,
    OFFLOADED_ELSEWHERE,
    PREFER_EDIT,
    READ_FIRST,
    READ_LINES,
    REGEX,
    SANDBOXED,
    SEARCHING,
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
from kingfisher.plugins.sessions.shell import kingfishers_shell
from tests.conftest import EVERY_TOOL, an_agent, start, subagents_dir
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


def _turn(cfg: Any, *script: AIMessage, way: str = "sync") -> None:
    Scripted.script.extend(script)
    service = Kingfisher(cfg, sessions=LocalSessions())
    request = Request("go", agent="only", session_id="s")
    asyncio.run(service.arun(request)) if way == "async" else service.run(request)


def _read(at: str, **args: Any) -> AIMessage:
    call = {"name": "read_file", "args": {"file_path": "/outputs/long.txt", **args}, "id": at}
    return AIMessage(content="", tool_calls=[call])


def _answer(at: str) -> str:
    heard = Scripted.heard[-1]
    (said,) = [one.text for one in heard if isinstance(one, ToolMessage) and one.tool_call_id == at]
    return said


def test_a_read_naming_no_limit_reads_a_thousand_lines(reading, way):
    """At deepagents' 100 lines, paging through files was a third of every call a turn made."""
    cfg, _ = reading

    _turn(cfg, _read("r1"), AIMessage("done"), way=way)

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
def test_read_file_is_not_run_on_a_file_no_model_here_takes(reading, way, name, said):
    """Read, audio and video reach the model as one string of base64 millions of
    characters long, and a `.pptx` as a block the summarizer counts and the request drops.
    """
    cfg, _ = reading
    what, reads = said
    outputs = cfg.workspace / "sessions" / "s" / "outputs"
    (outputs / name).write_bytes(os.urandom(64 * 1024))

    _turn(cfg, _read("r1", file_path=f"/outputs/{name}"), AIMessage("done"), way=way)

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


def test_a_pattern_joining_strings_with_a_bar_searches_for_each(reading, way):
    """`grep` is literal, and 63 of 97 searches that found nothing were `a|b` written anyway."""
    cfg, _ = reading

    _turn(cfg, _grep("g1", "alpha|beta"), AIMessage("done"), way=way)

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


def _said_to(reading: tuple[Any, list[list[Any]]], grant: str) -> dict[str, str]:
    """What each tool says to an agent granted `grant`, over one real turn.

    Driven rather than built, because deepagents writes these descriptions outside
    kingfisher's middleware, from every built-in: a test handing the middleware text of
    its own would pass whatever deepagents sends.
    """
    cfg, offered = reading
    an_agent(cfg, builtin_tools=grant)
    _turn(cfg, AIMessage("done"))
    return {tool.name: tool.description for tool in offered[-1]}


@pytest.mark.parametrize(
    ("grant", "line"),
    [
        (
            "[execute, grep, glob]",
            "- You MUST avoid using search commands like find and grep. Instead use the grep, "
            "glob tools to search.\n"
            "    - execute(command=\"find . -name '*.py'\")  # Use glob tool instead\n"
            "    - execute(command=\"grep -r 'pattern' .\")  # Use grep tool instead\n",
        ),
        (
            "[execute, grep, read_file]",
            "- You MUST avoid using shell grep for searches. Instead use the grep tool to search "
            "text. Use read_file rather than cat/head/tail.\n"
            "    - execute(command=\"grep -r 'pattern' .\")  # Use grep tool instead\n",
        ),
        (
            "[execute, glob, read_file]",
            "- You MUST avoid using shell find for searches. Instead use the glob tool to find "
            "files. Use read_file rather than cat/head/tail.\n"
            "    - execute(command=\"find . -name '*.py'\")  # Use glob tool instead\n",
        ),
        ("[execute, read_file]", "- Use read_file rather than cat/head/tail.\n"),
    ],
)
def test_execute_names_only_the_search_and_read_tools_held(reading, grant, line):
    """deepagents tells every shell holder to search with `grep` and `glob` and read with
    `read_file`, whether or not it holds them."""
    said = _said_to(reading, grant)[EXECUTE]

    assert f"\n{line}" in said
    for name in ("grep", "glob", "read_file"):
        assert (name in said) is (name in grant), name


def test_execute_holding_no_search_or_read_tool_loses_the_line_whole(reading):
    """Emptied rather than dropped whole, the line would reach the model as a bare `- `
    or a blank line."""
    said = _said_to(reading, "[execute]")[EXECUTE]

    for name in ("grep", "glob", "read_file", "cat/head/tail"):
        assert name not in said, name
    assert "-" not in [line.strip() for line in said.splitlines()]
    assert "\n\n\n" not in said
    assert timeout_line(reading[0].execution_timeout_s) in said


@pytest.mark.parametrize("grant", ["[grep, execute]", "[grep]"])
def test_grep_sends_only_the_shells_holder_to_the_shell_for_regex(reading, grant):
    """deepagents sends every `grep` holder to `execute` for a regex, shell or not."""
    said = _said_to(reading, grant)[GREP]

    assert (REGEX in said) is ("execute" in grant)
    assert ("execute" in said) is ("execute" in grant), "deepagents reworded it"
    assert JOINED in said


ALWAYS = "You should almost ALWAYS use this tool before using the"


@pytest.mark.parametrize(
    ("grant", "last"),
    [
        ("[ls, read_file, edit_file]", f"{ALWAYS} read_file or edit_file tools."),
        ("[ls, read_file]", f"{ALWAYS} read_file tool."),
        ("[ls, edit_file]", f"{ALWAYS} edit_file tool."),
        (
            "[ls]",
            "This is useful for exploring the filesystem and finding the right file to read or "
            "edit.",
        ),
    ],
)
def test_ls_names_only_the_tools_it_lists_for_that_are_held(reading, grant, last):
    """`ls` sends every holder on to `read_file` and `edit_file`, held or not."""
    said = _said_to(reading, grant)[LS]

    assert said.endswith(f"\n{last}")


@pytest.mark.parametrize("grant", ["[write_file, edit_file]", "[write_file]"])
def test_write_file_points_at_edit_file_only_for_its_holder(reading, grant):
    """deepagents' `write_file` recommends `edit_file` whether or not it is held."""
    said = _said_to(reading, grant)[WRITE_FILE]

    assert (PREFER_EDIT in said) is ("edit_file" in grant)
    assert ("edit_file" in said) is ("edit_file" in grant), "deepagents reworded it"


@pytest.mark.parametrize("grant", ["[edit_file, read_file]", "[edit_file]"])
def test_edit_file_never_claims_to_check_that_the_file_was_read(reading, grant):
    """deepagents' `edit_file` says it errors on a file not read first, and checks no such
    thing; to an agent without `read_file` it was also advice to call a tool not held."""
    said = _said_to(reading, grant)[EDIT_FILE]

    assert "errors otherwise" not in said, "deepagents reworded it"
    assert (READ_FIRST in said) is ("read_file" in grant)
    assert ("before editing" in said) is ("read_file" in grant)


@pytest.mark.parametrize("grant", ["[edit_file, read_file]", "[edit_file]"])
def test_edit_file_speaks_of_the_read_output_only_to_a_reader(reading, grant):
    """`edit_file` told an agent without `read_file` to keep the indentation "from the read
    output" and leave out its status header, output it could never have seen."""
    said = _said_to(reading, grant)[EDIT_FILE]

    assert (FROM_READ in said) is ("read_file" in grant)
    assert (EXACT_INDENT in said) is ("read_file" not in grant)
    assert ("read output" in said) is ("read_file" in grant), "deepagents reworded it"


@pytest.mark.parametrize("subagent", ["helper", "general-purpose"])
def test_an_unrestricted_graph_is_told_what_deepagents_says_but_the_false_claim(
    reading, subagent
):
    """Narrowing for every graph, the rewrite would change what an agent holding every tool
    was told, which only the untrue lines should: `edit_file`'s claim, and `execute`'s
    sandbox and its note on backends."""
    cfg, offered = reading
    rule = execute_line(kingfishers_shell(), DEFAULT_LAYOUT)
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


RUNNER = (
    "name: runner\ndescription: Runs.\n"
    "builtin_tools: [execute]\n"
    "system_prompt: |\n  You run.\n"
)


def test_a_subagent_is_told_of_the_tools_it_holds_not_its_callers(reading):
    """Read off the agent's grant, a subagent granted only the shell would be sent to the
    agent's `grep`, `glob` and `read_file`."""
    cfg, offered = reading
    (subagents_dir(cfg) / "runner.yaml").write_text(RUNNER, encoding="utf-8")
    an_agent(cfg, subagents="[runner]", **EVERY_TOOL)
    asked = {"description": "run", "subagent_type": "runner"}
    consulting = AIMessage(content="", tool_calls=[{"name": "task", "args": asked, "id": "t1"}])

    _turn(cfg, consulting, AIMessage("ran"), AIMessage("done"))

    assert len(offered) == 3
    callers, runners = (
        {getattr(tool, "name", None): tool.description for tool in tools} for tools in offered[:2]
    )
    assert f"\n{SEARCHING}\n" in callers[EXECUTE]
    assert set(runners) == {EXECUTE}
    for name in ("grep", "glob", "read_file"):
        assert name not in runners[EXECUTE], name

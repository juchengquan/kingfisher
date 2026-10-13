"""What whoever holds the shell is told of its paths, and told again on a miss."""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from kingfisher import Kingfisher, Request, default_backends
from kingfisher.infrastructure.harness.middlewares.shell_paths import (
    ABSOLUTE,
    RULE,
    hint,
    missed,
)
from tests.conftest import an_agent, start, subagents_dir
from tests.unit.scripted import Scripted

CHECKER = """name: checker
description: Checks.
builtin_tools: [execute]
system_prompt: |
  You check.
"""


@pytest.fixture
def shelled(scripted, monkeypatch):
    """An agent holding the shell, consulting a subagent that holds it too, with the tools
    each model call was offered kept in order.
    """
    offered: list[list[Any]] = []

    def bind(self, tools, **kwargs):
        offered.append(list(tools))
        return self

    monkeypatch.setattr(Scripted, "bind_tools", bind)
    subagents_dir(scripted).mkdir(parents=True, exist_ok=True)
    (subagents_dir(scripted) / "checker.yaml").write_text(CHECKER, encoding="utf-8")
    an_agent(scripted, subagents="[checker]")
    start(scripted, "s")
    return scripted, offered


def _run(cfg, *script: AIMessage) -> None:
    Scripted.script.extend(script)
    Kingfisher(cfg, backends=default_backends).run(Request("go", agent="only", session_id="s"))


def _calls(*made: tuple[str, dict[str, Any], str]) -> AIMessage:
    return AIMessage(
        content="", tool_calls=[{"name": name, "args": args, "id": at} for name, args, at in made]
    )


def _execute(tools: list[Any]) -> str:
    (described,) = [tool.description for tool in tools if getattr(tool, "name", None) == "execute"]
    return described


@pytest.mark.parametrize("subagent", ["checker", "general-purpose"])
def test_every_graph_holding_the_shell_is_told_its_paths_rather_than_absolute_ones(
    shelled, subagent
):
    """deepagents tells `execute`'s holder to use absolute paths, and the only absolute
    paths a model knows are the file tools': the shipped `reviewer`, a subagent and so
    never shown `system.md`, ran `open('/inputs/cars.csv')` in the shell and failed.
    """
    cfg, offered = shelled
    _run(
        cfg,
        _calls(("task", {"description": "check", "subagent_type": subagent}, "t1")),
        AIMessage(content="checked"),
        AIMessage(content="done"),
    )
    # The agent, the subagent, and the agent again: all three model calls, so the
    # subagent's is one of them rather than assumed.
    assert len(offered) == 3
    for tools in offered:
        described = _execute(tools)
        assert ABSOLUTE not in described
        assert "absolute paths" not in described, "deepagents reworded its advice"
        assert RULE in described


def test_a_shell_call_missing_a_file_tool_path_is_told_the_shell_spelling(shelled):
    """Told the rule, the agent still wrote `open("/inputs/cars.csv")` into a script and
    ran it, and the shell's own error names the path but never the reason.
    """
    cfg, _ = shelled
    _run(
        cfg,
        _calls(
            ("execute", {"command": "cat /inputs/cars.csv"}, "c1"),
            ("execute", {"command": "cat inputs/cars.csv"}, "c2"),
        ),
        AIMessage(content="done"),
    )
    answers = {
        message.tool_call_id: message.text
        for message in Scripted.heard[-1]
        if isinstance(message, ToolMessage)
    }
    assert hint("/inputs/cars.csv") in answers["c1"]
    # The control: a miss spelt the shell's way is a missing file, and says only that.
    assert "No such file" in answers["c2"]
    assert "file-tool path" not in answers["c2"]


@pytest.mark.parametrize(
    ("output", "path"),
    [
        ("FileNotFoundError: [Errno 2] No such file or directory: '/inputs/cars.csv'",
         "/inputs/cars.csv"),
        ("ls: /inputs/: No such file or directory", "/inputs/"),
        ("python3: can't open file '/scratchpad/x.py': [Errno 2] No such file or directory",
         "/scratchpad/x.py"),
        ("sh: 1: /scratchpad/run.sh: not found", "/scratchpad/run.sh"),
        ("bash: /outputs/a.csv: Read-only file system", "/outputs/a.csv"),
        # The host's spelling of a session folder, which is right.
        ("cat: /Users/me/sessions/s/inputs/x.csv: No such file or directory", None),
        ("cat: inputs/x.csv: No such file or directory", None),
        ("cat: ./inputs/x.csv: No such file or directory", None),
        # A name that only starts like a session folder.
        ("cat: /inputs.csv: No such file or directory", None),
        # A path printed beside a miss of something else.
        ("wrote /outputs/a.csv\nsh: rg: command not found", None),
    ],
)
def test_a_miss_is_read_off_the_line_naming_the_path(output, path):
    """A host path ending in a session folder's name was read as a file-tool one, and a
    correct command was told to drop a slash it never had.
    """
    assert missed(output) == path

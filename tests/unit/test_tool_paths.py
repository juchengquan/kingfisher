"""What a workspace tool is handed, once the session is on both routes.

A workspace tool is an ordinary Python function: it runs in this process and
opens files with the operating system, so `/data/config.ini` meant
`/data/config.ini` on the machine and was not there. The built-in file tools do
not have that problem because deepagents defines them inside the middleware that
owns the backend -- and hands that backend to nobody else.

So there were two routes to the filesystem and the session was on one. These are
about the bridge, and they assert both halves of what it buys: a tool that works
with the names the agent was taught, and a tool that cannot be aimed at another
session.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kingfisher.infrastructure.harness.backend import WorkspaceToolPaths


@dataclass
class Call:
    """The shape `wrap_tool_call` receives, in the fields this middleware reads.

    A dataclass because the middleware rebuilds the request with
    `dataclasses.replace` -- which is how langgraph documents rewriting a call:
    `{**request.tool_call, "args": {...}}`.
    """

    tool_call: dict[str, Any] = field(default_factory=dict)


def a_call(name: str = "line_count", **args: Any) -> Call:
    return Call(tool_call={"name": name, "args": args, "id": "c1"})


@pytest.fixture
def session(tmp_path):
    directory = tmp_path / "sessions" / "mine"
    (directory / "data").mkdir(parents=True)
    return directory


@pytest.fixture
def bridge(session):
    return WorkspaceToolPaths(frozenset({"line_count", "csv_profile"}), session)


def handed(bridge, request) -> Any:
    """What the tool would actually receive."""
    seen: list[Any] = []
    bridge.wrap_tool_call(request, lambda r: seen.append(r.tool_call["args"]))
    return seen[0] if seen else None


# -- the usability half ------------------------------------------------------


def test_the_name_the_agent_was_taught_now_works(bridge, session):
    """Measured in a real run before this existed: the model passed
    `/data/config.ini`, the tool raised `FileNotFoundError`, and the delegate
    reported it could not read a file that `ls` could see."""
    assert handed(bridge, a_call(path="/data/config.ini")) == {
        "path": str(session / "data" / "config.ini")
    }


def test_a_relative_name_lands_in_the_session_too(bridge, session):
    """The shell's spelling of the same file -- `system.md` teaches both, as one
    path with and without its leading slash."""
    assert handed(bridge, a_call(path="data/config.ini")) == {
        "path": str(session / "data" / "config.ini")
    }


# -- the leak half -----------------------------------------------------------


def test_another_session_cannot_be_named_at_all(bridge, session):
    """The leak, closed by arithmetic rather than by refusal.

    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer.
    Now that argument is measured from this session, so it names a place inside
    it that does not exist -- for the same reason `/etc/passwd` does.
    """
    args = handed(bridge, a_call(path="/workspace/sessions/other/secret.txt"))

    assert args["path"].startswith(str(session))
    assert "sessions/other" not in str(Path(args["path"]).relative_to(session).parts[0])


def test_climbing_out_is_refused_with_the_rule(bridge):
    """`..` cannot be resolved into something safe, so it is refused rather than
    silently rebased -- and the error names what to use instead, because a model
    that is told the rule can correct itself mid-turn."""
    answer = bridge.wrap_tool_call(a_call(path="../other/secret.txt"), lambda r: None)

    assert answer.status == "error"
    assert "/data/<name>" in answer.content


# -- what it leaves alone ----------------------------------------------------


def test_a_tool_that_is_not_a_workspace_tool_is_untouched(bridge):
    """The built-in file tools already resolve against the session, inside the
    backend. Translating twice would root a path in a root."""
    assert handed(bridge, a_call("read_file", path="/data/config.ini")) == {
        "path": "/data/config.ini"
    }


def test_arguments_that_do_not_name_files_are_untouched(bridge, session):
    """Only the ones the convention names. A tool taking a pattern or a column
    should get exactly what the model wrote."""
    args = handed(bridge, a_call(path="/data/x.csv", pattern="^id$", limit=5))

    assert args["pattern"] == "^id$"
    assert args["limit"] == 5
    assert args["path"] == str(session / "data" / "x.csv")


def test_something_that_is_not_a_string_is_handed_back_as_it_is(bridge):
    """A tool may take a number called `path`, and this is not the place to have
    an opinion about that."""
    assert handed(bridge, a_call(path=7)) == {"path": 7}

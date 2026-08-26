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


# -- through a real graph ----------------------------------------------------
#
# The tests above hold the middleware directly, which is where the decisions
# are. These drive a compiled agent with a scripted model, because "the tool
# received the right string" and "the model got a useful answer" are different
# claims and only the second is the one anybody cares about.


A_TOOL = '''
"""A tool that reports the path it was actually handed."""


def whereami(path: str) -> str:
    """Report what this tool received.

    `path` is the same virtual path the file tools take -- `/data/<name>` --
    rooted at this session.
    """
    from pathlib import Path

    return f"handed={path} exists={Path(path).exists()}"


TOOLS = [whereami]
'''


def a_workspace_with_the_tool(cfg):
    from tests.conftest import tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "whereami.py").write_text(A_TOOL, encoding="utf-8")


def ran(cfg, session_dir, argument: str) -> str:
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel

    responses = [
        AIMessage(
            content="",
            tool_calls=[{"name": "whereami", "args": {"path": argument}, "id": "c1"}],
        ),
        AIMessage(content="done"),
    ]
    agent = build_agent(
        cfg, session_dir=session_dir, model=FakeToolCallingModel(responses=responses)
    )
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )
    return "\n".join(str(getattr(m, "content", "")) for m in out["messages"])


def test_the_agents_own_name_reaches_the_tool_as_a_real_file(cfg, session_dir):
    """End to end, and the claim that matters: the model writes the name it was
    taught, and the tool opens a file that is there.

    Before this, the same call produced `FileNotFoundError` from inside the
    process and a delegate reporting it could not read a file `ls` could see.
    """
    a_workspace_with_the_tool(cfg)
    (session_dir / "data").mkdir(parents=True, exist_ok=True)
    (session_dir / "data" / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    transcript = ran(cfg, session_dir, "/data/report.csv")

    assert str(session_dir / "data" / "report.csv") in transcript
    assert "exists=True" in transcript


def test_another_sessions_file_is_not_reachable_through_a_tool(cfg, session_dir):
    """The leak, end to end.

    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer.
    The same shape now resolves under this session, so the tool is handed a path
    that does not exist -- and the secret is not in the transcript at any point.
    """
    a_workspace_with_the_tool(cfg)
    other = session_dir.parent / "another-tenant" / "derived"
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")

    transcript = ran(cfg, session_dir, str(other / "secret.txt"))

    assert "exists=False" in transcript
    assert "TENANT-A-PRIVATE" not in transcript
    assert transcript.count(str(session_dir)) >= 1, "resolved under this session"


A_READER = '''
"""A tool that reads what it was handed."""


def peek(path: str) -> str:
    """Read it.

    `path` is the same virtual path the file tools take, rooted at this session.
    """
    from pathlib import Path

    p = Path(path)
    return f"exists={p.exists()} content={p.read_text().strip() if p.exists() else '-'}"


TOOLS = [peek]
'''


def test_a_link_inside_the_session_does_not_widen_it(cfg, session_dir):
    """The check `within` tells adapters to do, and the reason it is not optional.

    `within` is lexical on purpose -- the domain may not touch the filesystem --
    and a session directory is one the agent can write to. `execute` is rooted
    there, so it can make a symlink pointing at another session, hand a tool the
    virtual path to it, and be read the target.

    Measured before the second check existed: this returned
    `content=TENANT-A-PRIVATE` through a tool, while `read_file` refused the very
    same path. The bridge was weaker than the tools it was built to match.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "peek.py").write_text(A_READER, encoding="utf-8")
    other = session_dir.parent / "another-tenant" / "derived"
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.txt").write_text("TENANT-A-PRIVATE", encoding="utf-8")
    (session_dir / "derived").mkdir(parents=True, exist_ok=True)
    (session_dir / "derived" / "link.txt").symlink_to(other / "secret.txt")

    agent = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {"name": "peek", "args": {"path": "/derived/link.txt"}, "id": "c1"}
                    ],
                ),
                AIMessage(content="done"),
            ]
        ),
    )
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "TENANT-A-PRIVATE" not in transcript
    assert "resolves outside this session" in transcript


def test_a_link_that_stays_inside_still_works(session, bridge):
    """The other half, so the check refuses escapes rather than symlinks. A
    session that could hold no links at all would be a surprising place to run a
    shell in."""
    (session / "derived").mkdir(parents=True, exist_ok=True)
    (session / "data" / "real.csv").write_text("a\n", encoding="utf-8")
    (session / "derived" / "near.csv").symlink_to(session / "data" / "real.csv")

    args = handed(bridge, a_call(path="/derived/near.csv"))

    assert args["path"] == str((session / "data" / "real.csv").resolve())

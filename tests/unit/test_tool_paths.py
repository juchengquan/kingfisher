"""What a workspace tool is handed, once the session is on both routes."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kingfisher.infrastructure.harness.backend import WorkspaceToolPaths


@dataclass
class Call:
    """The shape `wrap_tool_call` receives, in the fields this middleware reads."""

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
    """Measured in a real run before this existed: the model passed `/data/config.ini`,
    the tool raised `FileNotFoundError`, and the delegate reported it could not read
    a file that `ls` could see.
    """
    assert handed(bridge, a_call(path="/data/config.ini")) == {
        "path": str(session / "data" / "config.ini")
    }


def test_a_relative_name_lands_in_the_session_too(bridge, session):
    """The shell's spelling of the same file -- `system.md` teaches both, as one path
    with and without its leading slash.
    """
    assert handed(bridge, a_call(path="data/config.ini")) == {
        "path": str(session / "data" / "config.ini")
    }


# -- the leak half -----------------------------------------------------------


def test_another_session_cannot_be_named_at_all(bridge, session):
    """The leak, closed by arithmetic rather than by refusal.

    `line_count('/workspace/sessions/<other>/secret.txt')` returned an answer. Now
    that argument is measured from this session, so it names a place inside it that
    does not exist -- for the same reason `/etc/passwd` does.
    """
    args = handed(bridge, a_call(path="/workspace/sessions/other/secret.txt"))

    assert args["path"].startswith(str(session))
    assert "sessions/other" not in str(Path(args["path"]).relative_to(session).parts[0])


def test_climbing_out_is_refused_with_the_rule(bridge):
    """`..` cannot be resolved into something safe, so it is refused rather than
    silently rebased -- and the error names what to use instead, because a model that
    is told the rule can correct itself mid-turn.
    """
    answer = bridge.wrap_tool_call(a_call(path="../other/secret.txt"), lambda r: None)

    assert answer.status == "error"
    assert "/data/<name>" in answer.content


# -- an argument that is not `path` -------------------------------------------
#
# Nothing says such an argument names a file, so it is not translated -- and before
# this, it reached the tool as written. A tool calling its file `input_file` was
# handed the host path of another session's file and returned what was in it.


def test_a_host_path_in_any_other_argument_is_refused(bridge, session):
    """The leak the translation did not cover, because it keys on the name."""
    other = session.parent / "other" / "data" / "secret.txt"
    seen: list[object] = []

    answer = bridge.wrap_tool_call(a_call(input_file=str(other)), seen.append)

    assert not seen, "the tool was called with another session's host path"
    assert answer.status == "error"
    assert "host path" in answer.content


def test_another_session_is_refused_where_no_host_root_would_catch_it():
    """A container puts the workspace at `/workspace`, which is not a host root. What
    catches it there is the directory this session's siblings are in.
    """
    mine = WorkspaceToolPaths(frozenset({"peek"}), Path("/workspace/sessions/mine"))
    seen: list[object] = []

    answer = mine.wrap_tool_call(
        a_call("peek", input_file="/workspace/sessions/other/data/secret.txt"), seen.append
    )

    assert not seen
    assert answer.status == "error"


def test_a_host_path_inside_a_list_is_refused_too(bridge):
    """An argument may carry several files, and the first one is not the only one."""
    seen: list[object] = []

    answer = bridge.wrap_tool_call(
        a_call(files=["notes.txt", {"source": "/etc/passwd"}]), seen.append
    )

    assert not seen
    assert answer.status == "error"


def test_what_a_process_reads_itself_through_is_refused(bridge):
    """`/proc/self/environ` is this process's environment, keys and all, and it is not
    under any root the file tools refuse.
    """
    for spelled in ("/proc/self/environ", "/proc"):
        answer = bridge.wrap_tool_call(a_call(input_file=spelled), lambda r: None)

        assert answer.status == "error", spelled


def test_an_argument_that_only_looks_like_a_path_is_handed_over(bridge):
    """The control: a URL, a route and a statement are not host paths, and a check that
    refused every leading slash would break the tools that take them.
    """
    written = {
        "url": "https://example.com/home/users/",
        "route": "/api/v1/users",
        "statement": "SELECT * FROM tmp",
    }

    assert handed(bridge, a_call(**written)) == written


# -- what it leaves alone ----------------------------------------------------


def test_a_tool_that_is_not_a_workspace_tool_is_untouched(bridge):
    """The built-in file tools already resolve against the session, inside the backend."""
    assert handed(bridge, a_call("read_file", path="/data/config.ini")) == {
        "path": "/data/config.ini"
    }


def test_arguments_that_do_not_name_files_are_untouched(bridge, session):
    """Only the ones the convention names."""
    args = handed(bridge, a_call(path="/data/x.csv", pattern="^id$", limit=5))

    assert args["pattern"] == "^id$"
    assert args["limit"] == 5
    assert args["path"] == str(session / "data" / "x.csv")


def test_something_that_is_not_a_string_is_handed_back_as_it_is(bridge):
    """A tool may take a number called `path`, and this is not the place to have an
    opinion about that.
    """
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
    """End to end, and the claim that matters: the model writes the name it was taught,
    and the tool opens a file that is there.
    """
    a_workspace_with_the_tool(cfg)
    (session_dir / "data").mkdir(parents=True, exist_ok=True)
    (session_dir / "data" / "report.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    transcript = ran(cfg, session_dir, "/data/report.csv")

    assert str(session_dir / "data" / "report.csv") in transcript
    assert "exists=True" in transcript


def test_another_sessions_file_is_not_reachable_through_a_tool(cfg, session_dir):
    """The leak, end to end."""
    a_workspace_with_the_tool(cfg)
    other = session_dir.parent / "another-tenant" / "derived"
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.txt").write_text("TENANT-A-PRIVATE\n", encoding="utf-8")

    transcript = ran(cfg, session_dir, str(other / "secret.txt"))

    assert "exists=False" in transcript
    assert "TENANT-A-PRIVATE" not in transcript
    assert transcript.count(str(session_dir)) >= 1, "resolved under this session"


A_TOOL_WITH_ANOTHER_NAME = '''
"""A tool that reads a file it calls something other than `path`."""


def peek_file(input_file: str) -> str:
    """Read it."""
    from pathlib import Path

    return Path(input_file).read_text()


TOOLS = [peek_file]
'''


def test_a_file_argument_with_another_name_cannot_read_another_session(cfg, session_dir):
    """The leak, end to end, exactly as it was measured: `TENANT-A-PRIVATE` came back
    through a tool whose argument was not called `path`.
    """
    from langchain_core.messages import AIMessage

    from kingfisher.infrastructure.harness.agent import build_agent
    from tests.conftest import FakeToolCallingModel, tools_dir

    tools_dir(cfg).mkdir(parents=True, exist_ok=True)
    (tools_dir(cfg) / "peek_file.py").write_text(A_TOOL_WITH_ANOTHER_NAME, encoding="utf-8")
    other = session_dir.parent / "another-tenant" / "data"
    other.mkdir(parents=True, exist_ok=True)
    (other / "secret.txt").write_text("TENANT-A-PRIVATE", encoding="utf-8")

    call = {"name": "peek_file", "args": {"input_file": str(other / "secret.txt")}, "id": "c1"}
    agent = build_agent(
        cfg,
        session_dir=session_dir,
        model=FakeToolCallingModel(
            responses=[AIMessage(content="", tool_calls=[call]), AIMessage(content="done")]
        ),
    )
    out = agent.invoke(
        {"messages": [{"role": "user", "content": "go"}]}, config={"recursion_limit": 12}
    )
    transcript = "\n".join(str(getattr(m, "content", "")) for m in out["messages"])

    assert "TENANT-A-PRIVATE" not in transcript
    assert "host path" in transcript


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
    """The other half, so the check refuses escapes rather than symlinks."""
    (session / "derived").mkdir(parents=True, exist_ok=True)
    (session / "data" / "real.csv").write_text("a\n", encoding="utf-8")
    (session / "derived" / "near.csv").symlink_to(session / "data" / "real.csv")

    args = handed(bridge, a_call(path="/derived/near.csv"))

    assert args["path"] == str((session / "data" / "real.csv").resolve())

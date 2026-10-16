"""What a session keeps about itself, where the agent cannot reach it.

`.harness` holds the agent a session opened with, its conversation, the lock a
turn holds and its run log. Each of those used to live under `state_dir`, one
directory per kind keyed by session id, where two things were true and neither
was intended: nothing deleted them when the session went, and -- because
`state_dir` defaults inside the workspace and `writable_roots` returns the whole
workspace -- the shell could reach them anyway.
"""

from __future__ import annotations

import platform
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher.core.access.capabilities import Capabilities
from kingfisher.core.agent.assembly import build_agent
from kingfisher.core.layout import CLAIM, HARNESS, PINNED_AGENT
from kingfisher.core.request import Request
from kingfisher.core.workspace import ensure_layout
from kingfisher.plugins import LocalSessions
from kingfisher.plugins.sessions.backend import backend_at
from tests.conftest import FakeToolCallingModel, pin, start

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

AGENT = """name: assistant
description: An agent.
system_prompt: |
  You help.
"""


def agent_snapshot(session_dir) -> Path:
    """Where the default backend keeps a session's pinned agent, on disk."""
    return Path(session_dir) / HARNESS / PINNED_AGENT


def _agent(cfg) -> None:
    root = cfg.definitions / "agents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "assistant.yaml").write_text(AGENT, encoding="utf-8")


def _drive(cfg, session_dir, tool, args) -> str:
    """Run one tool call through a real graph and return what the tool said."""
    call = AIMessage(content="", tool_calls=[{"name": tool, "id": "1", "args": args}])
    graph = build_agent(
        cfg,
        backend=backend_at(cfg, session_dir),
        capabilities=Capabilities(builtin_tools=("write_file", "read_file", "ls")),
        model=FakeToolCallingModel(responses=[call, AIMessage(content="done")]),
    ).graph
    result = graph.invoke(
        {"messages": [("user", "go")]},
        config={"configurable": {"thread_id": "t"}, "recursion_limit": 8},
    )
    return " ".join(
        str(m.content) for m in result["messages"] if type(m).__name__ == "ToolMessage"
    )


# -- the tool half --------------------------------------------------------


def test_a_file_tool_cannot_write_into_the_harness(cfg, session_dir):
    """The pin is the agent definition this session is running under. A turn that
    could rewrite it could change its own instructions mid-conversation."""
    pinned = agent_snapshot(session_dir)
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(AGENT, encoding="utf-8")

    said = _drive(
        cfg, session_dir, "write_file",
        {"file_path": "/.harness/agent.yaml", "content": "name: something-else"},
    )

    assert "permission denied" in said.lower()
    assert pinned.read_text(encoding="utf-8") == AGENT


def test_a_file_tool_cannot_read_the_harness_either(cfg, session_dir):
    """Unlike `/skills/`, which is read-only rather than unreachable: a skill is
    instructions the agent is meant to follow, and this is bookkeeping about the
    agent that it gains nothing by reading."""
    pinned = agent_snapshot(session_dir)
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(AGENT, encoding="utf-8")

    said = _drive(cfg, session_dir, "read_file", {"file_path": "/.harness/agent.yaml"})

    assert "You help" not in said


def test_a_listing_does_not_show_the_harness(cfg, session_dir):
    """Filtered out of the results rather than refused, which is what a deny rule does
    to `ls`, `glob` and `grep` -- so the model never sees a directory it would then
    try to open and be refused for.
    """
    said = _drive(cfg, session_dir, "ls", {"path": "/"})

    assert "outputs" in said, said
    assert ".harness" not in said, said


# -- the shell half, which tool permissions never see ---------------------


@macos
def test_the_shell_cannot_write_into_the_harness(cfg, session_dir):
    """The half a tool rule cannot reach: the shell backend roots at the session, so
    `/.harness` is an ordinary relative path to `execute`."""
    pinned = agent_snapshot(session_dir)
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(AGENT, encoding="utf-8")
    shell = backend_at(cfg, session_dir)

    shell.execute(f'printf "name: mine" > "{pinned}"')
    shell.execute(f'rm -f "{pinned}"')

    assert pinned.read_text(encoding="utf-8") == AGENT


@macos
def test_the_shell_can_still_write_the_rest_of_the_session(cfg, session_dir):
    """The bound on the rule: one directory is carved out, not the session."""
    shell = backend_at(cfg, session_dir)

    assert shell.execute(f'echo fine > "{session_dir}/outputs/ok.txt"').exit_code == 0
    assert shell.execute('echo fine > "$TMPDIR/ok.txt"').exit_code == 0


@macos
def test_one_session_s_harness_is_not_denied_by_naming_another(cfg, workspace):
    """The profile is one static text covering every session, so the rule has to be a
    pattern -- and a pattern that matched too much would deny a path with `.harness`
    somewhere in the middle of it."""
    from kingfisher.plugins.sessions.folders import ensure_session_layout

    session = ensure_session_layout(workspace / "sessions" / "second")
    decoy = session / "outputs" / ".harness"
    decoy.mkdir(parents=True, exist_ok=True)
    shell = backend_at(cfg, session)

    assert shell.execute(f'echo fine > "{decoy}/ok.txt"').exit_code == 0


def test_the_pin_and_the_claim_go_with_the_session(cfg):
    """The residue this whole change is about: one file per session that ever existed,
    under `state_dir`, deleted by nothing."""
    from kingfisher.core.service import Kingfisher
    from tests.conftest import StubCheckpointer
    from tests.unit.test_run import StubAgent

    _agent(cfg)
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), sessions=LocalSessions(), threads=StubCheckpointer()
    )
    session_id = start(cfg, "s")
    pin(service, session_id, "assistant")
    service.run(Request(task="go", session_id=session_id))
    directory = cfg.workspace / "sessions" / session_id
    assert agent_snapshot(directory).is_file()

    service.delete_session(session_id)

    assert not agent_snapshot(directory).exists()
    assert not (directory / HARNESS / CLAIM).exists()
    assert not list((cfg.workspace / "sessions").iterdir())


# -- the harness-owned directory, and laying one out ----------------------


def test_the_harness_owned_directory_is_protected_too(cfg):
    """The profile lives in it, and after `TMPDIR` moved out nothing in it is the
    agent's -- so it is denied as a directory rather than as one named file."""
    from kingfisher.core.layout import HARNESS_OWNED
    from kingfisher.plugins.sessions.sandbox import confinement

    protected = confinement.protected_roots(
        cfg.workspace, (cfg.skills_dir,), tuple(cfg.catalogue_roots.values())
    )

    assert (cfg.workspace / HARNESS_OWNED).resolve() in protected


def test_an_empty_directory_is_laid_out_fresh(tmp_path):
    """A path with no marker reads as never used, and `ensure_layout` lays it out."""
    assert ensure_layout(tmp_path / "fresh").is_dir()

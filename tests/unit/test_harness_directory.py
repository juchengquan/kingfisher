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
import shutil

import pytest
from langchain_core.messages import AIMessage

from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import build_backend
from kingfisher.infrastructure.harness.runlog import log_path
from kingfisher.infrastructure.workspace.layout import ensure_layout
from kingfisher.infrastructure.workspace.sessions import claim_path
from kingfisher.infrastructure.workspace.snapshots import agent_snapshot
from kingfisher.layout import LAYOUT_VERSION, MARKER
from tests.conftest import FakeToolCallingModel

macos = pytest.mark.skipif(
    platform.system() != "Darwin", reason="sandbox-exec is the macOS mechanism"
)

AGENT = """name: assistant
description: An agent.
system_prompt: |
  You help.
"""


def _agent(cfg) -> None:
    root = cfg.workspace / "agents"
    root.mkdir(parents=True, exist_ok=True)
    (root / "assistant.yaml").write_text(AGENT, encoding="utf-8")


def _drive(cfg, session_dir, tool, args) -> str:
    """Run one tool call through a real graph and return what the tool said."""
    call = AIMessage(content="", tool_calls=[{"name": tool, "id": "1", "args": args}])
    graph = build_agent(
        cfg,
        session_dir=session_dir,
        capabilities=Capabilities(builtin_tools=("write_file", "read_file", "ls")),
        model=FakeToolCallingModel(responses=[call, AIMessage(content="done")]),
    )
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

    assert "derived" in said, said
    assert ".harness" not in said, said


# -- the shell half, which tool permissions never see ---------------------


@macos
def test_the_shell_cannot_write_into_the_harness(cfg, session_dir):
    """The half a tool rule cannot reach: the shell backend roots at the session, so
    `/.harness` is an ordinary relative path to `execute`."""
    pinned = agent_snapshot(session_dir)
    pinned.parent.mkdir(parents=True, exist_ok=True)
    pinned.write_text(AGENT, encoding="utf-8")
    shell = build_backend(cfg, session_dir)

    shell.execute(f'printf "name: mine" > "{pinned}"')
    shell.execute(f'rm -f "{pinned}"')

    assert pinned.read_text(encoding="utf-8") == AGENT


@macos
def test_the_shell_can_still_write_the_rest_of_the_session(cfg, session_dir):
    """The bound on the rule: one directory is carved out, not the session."""
    shell = build_backend(cfg, session_dir)

    assert shell.execute(f'echo fine > "{session_dir}/derived/ok.txt"').exit_code == 0
    assert shell.execute('echo fine > "$TMPDIR/ok.txt"').exit_code == 0


@macos
def test_one_session_s_harness_is_not_denied_by_naming_another(cfg, workspace):
    """The profile is one static text covering every session, so the rule has to be a
    pattern -- and a pattern that matched too much would deny a path with `.harness`
    somewhere in the middle of it."""
    from kingfisher.infrastructure.workspace.sessions import ensure_session_layout

    session = ensure_session_layout(workspace / "sessions" / "second")
    decoy = session / "derived" / ".harness"
    decoy.mkdir(parents=True, exist_ok=True)
    shell = build_backend(cfg, session)

    assert shell.execute(f'echo fine > "{decoy}/ok.txt"').exit_code == 0


# -- what crosses a machine, and what does not ----------------------------


def _wired_to_a_store(cfg, tmp_path):
    from kingfisher import LocalSessionStore
    from kingfisher.application.service import Kingfisher
    from tests.conftest import StubCheckpointer
    from tests.unit.test_run import StubAgent

    kept = LocalSessionStore(tmp_path / "kept-elsewhere")
    return Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer(), sessions=kept), kept


def test_a_session_that_lost_its_directory_keeps_the_agent_it_opened_with(cfg, tmp_path):
    """The hole this closed. The pin lived under `state_dir`, which the store never
    saw, so a session resumed on another machine found none, re-pinned from *that*
    host's catalogue, and accepted whatever agent the request named -- "a session is
    fixed to the agent it opened with" held on one host and quietly failed across two.
    """
    _agent(cfg)
    service, _ = _wired_to_a_store(cfg, tmp_path)
    # Opened and pinned the way `POST /sessions` does it, which is also the only
    # way a deployment that supplies its own graph ever pins: `_graph_for` returns
    # that graph before it resolves an agent.
    session_id = service.start_session()
    service.remember_agent(session_id, "assistant")
    service.run(Request(task="go", session_id=session_id))

    # The machine goes; the store is all that is left.
    shutil.rmtree(cfg.workspace / "sessions" / session_id)
    service.run(Request(task="again", session_id=session_id))

    kept = agent_snapshot(cfg.workspace / "sessions" / session_id)
    assert kept.is_file(), "the pin did not come back, so the session re-pinned itself"
    assert "assistant" in kept.read_text(encoding="utf-8")


def test_the_claim_and_the_run_log_stay_on_the_machine(cfg, tmp_path):
    """A restored claim would make the session look busy for `claim_stale_after` --
    minutes -- before anyone could take the slot. The log grows and is re-read whole
    on every save, for a file nothing in production reads.
    """
    _agent(cfg)
    service, kept = _wired_to_a_store(cfg, tmp_path)
    session_id = service.start_session()
    service.remember_agent(session_id, "assistant")
    service.run(Request(task="go", session_id=session_id))

    held = kept.fetch(session_id)

    assert ".harness/agent.yaml" in held
    assert ".harness/transcript.jsonl" in held
    assert not [name for name in held if name.startswith(".harness/claim")]
    assert ".harness/runlog.jsonl" not in held


def test_the_run_log_and_the_claim_go_with_the_session(cfg):
    """The residue this whole change is about: one file per session that ever existed,
    under `state_dir`, deleted by nothing."""
    from kingfisher.application.service import Kingfisher
    from tests.conftest import StubCheckpointer
    from tests.unit.test_run import StubAgent

    _agent(cfg)
    service = Kingfisher(cfg, graph=StubAgent("ok"), threads=StubCheckpointer())
    session_id = service.start_session()
    service.remember_agent(session_id, "assistant")
    service.run(Request(task="go", session_id=session_id))
    directory = cfg.workspace / "sessions" / session_id
    assert log_path(directory).is_file()
    assert agent_snapshot(directory).is_file()

    service.delete_session(session_id)

    assert not log_path(directory).exists()
    assert not agent_snapshot(directory).exists()
    assert not claim_path(directory).exists()
    assert not list((cfg.workspace / "sessions").iterdir())


# -- a workspace laid out by a version that arranged it differently -------


def test_an_older_workspace_is_refused_rather_than_silently_relaid(tmp_path):
    """Silence is the danger, not breakage. Code looking in `.harness` for a pin that
    is still at the old path does not error -- it finds none and re-pins, possibly
    under a different agent, and finds no transcript and starts the conversation
    again from nothing.
    """
    workspace = tmp_path / "old"
    (workspace / ".kingfisher").mkdir(parents=True)
    (workspace / MARKER).write_text("kingfisher workspace\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="layout 1"):
        ensure_layout(workspace)


def test_a_workspace_this_version_laid_out_is_accepted(tmp_path):
    """So the refusal above is not passing because every workspace is refused."""
    workspace = ensure_layout(tmp_path / "new")

    assert f"layout {LAYOUT_VERSION}" in (workspace / MARKER).read_text(encoding="utf-8")
    assert ensure_layout(workspace) == workspace


def test_an_empty_directory_is_not_an_old_workspace(tmp_path):
    """A path with no marker has no version to disagree with, and `is_new_workspace`
    already treats it as never used."""
    assert ensure_layout(tmp_path / "fresh").is_dir()

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
import re
import shutil
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage

from kingfisher import default_backends
from kingfisher.config import ConfigError
from kingfisher.domain.capabilities import Capabilities
from kingfisher.domain.request import Request
from kingfisher.infrastructure.harness.agent import build_agent
from kingfisher.infrastructure.harness.backend import backend_at
from kingfisher.infrastructure.workspace import ensure_layout, is_new_workspace
from kingfisher.layout import CLAIM, HARNESS, LAYOUT_VERSION, MARKER, PINNED_AGENT
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
    root = cfg.workspace / "agents"
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
    from kingfisher.infrastructure.workspace import ensure_session_layout

    session = ensure_session_layout(workspace / "sessions" / "second")
    decoy = session / "outputs" / ".harness"
    decoy.mkdir(parents=True, exist_ok=True)
    shell = backend_at(cfg, session)

    assert shell.execute(f'echo fine > "{decoy}/ok.txt"').exit_code == 0


def test_the_pin_and_the_claim_go_with_the_session(cfg):
    """The residue this whole change is about: one file per session that ever existed,
    under `state_dir`, deleted by nothing."""
    from kingfisher.application.service import Kingfisher
    from tests.conftest import StubCheckpointer
    from tests.unit.test_run import StubAgent

    _agent(cfg)
    service = Kingfisher(
        cfg, graph=StubAgent("ok"), backends=default_backends, threads=StubCheckpointer()
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


# -- a workspace laid out by a version that arranged it differently -------


def test_the_harness_owned_directory_is_protected_too(cfg):
    """The profile lives in it, and after `TMPDIR` moved out nothing in it is the
    agent's -- so it is denied as a directory rather than as one named file."""
    from kingfisher.infrastructure.sandbox import confinement
    from kingfisher.layout import HARNESS_OWNED

    protected = confinement.protected_roots(
        cfg.workspace, (cfg.skills_dir,), tuple(cfg.catalogue_roots.values())
    )

    assert (cfg.workspace / HARNESS_OWNED).resolve() in protected


#: What layout 1 left under `.kingfisher`, written out rather than read from
#: `OLD_STATE`: cases built from the constant shrink with it, so a name dropped from
#: what is looked for would take its own case with it and pass.
LAYOUT_1_STATE = (".kingfisher/agents", ".kingfisher/runs", ".kingfisher/claims", ".kingfisher/tmp")


def _laid_out_by(layout, workspace, *left):
    """A workspace an older version made, with `left` still where it wrote it."""
    (workspace / ".kingfisher").mkdir(parents=True)
    # Layout 1's marker carried no number, which is how it is told apart.
    number = "" if layout == 1 else f"layout {layout}\n"
    (workspace / MARKER).write_text(f"kingfisher workspace\n{number}", encoding="utf-8")
    for one in left:
        (workspace / one).mkdir(parents=True)
    return workspace


@pytest.mark.parametrize(
    ("layout", "left"),
    [(1, "sessions/abc"), *((1, one) for one in LAYOUT_1_STATE), (2, "sessions/abc")],
)
def test_an_older_workspace_is_refused_rather_than_silently_relaid(tmp_path, layout, left):
    """Silence is the danger, not breakage. Code looking for a session's files where
    this layout keeps them does not error -- it finds no pin and re-pins, finds no
    transcript and starts again, finds no inputs and says nothing.
    """
    workspace = _laid_out_by(layout, tmp_path / "old", left)

    with pytest.raises(ConfigError, match=f"layout {layout}"):
        ensure_layout(workspace)


@pytest.mark.parametrize(
    ("layout", "left"),
    [
        (1, ("sessions/abc", *LAYOUT_1_STATE)),
        (1, (".kingfisher/runs",)),
        (2, ("sessions/abc",)),
    ],
)
def test_doing_what_the_refusal_says_clears_it(tmp_path, layout, left):
    """It refused on the marker's number alone, so deleting everything the message
    listed left it refusing still, and the way out it did not name -- deleting the
    marker -- makes the workspace read as new, which the integration driver seeds
    over. It also listed layout 1's leftovers to a workspace that had none of them.
    The paths deleted here are read out of the message, so one it leaves out keeps
    the refusal and one that is not there fails to delete.
    """
    workspace = _laid_out_by(layout, tmp_path / "old", *left)
    with pytest.raises(ConfigError) as refused:
        ensure_layout(workspace)
    listed = re.search(r"left from layout \d+ -- (.*?) -- ", str(refused.value))
    assert listed is not None, f"the message no longer lists what to delete: {refused.value}"

    for one in listed.group(1).split(", "):
        target = workspace / one.removeprefix("everything in ")
        # An empty name is the workspace itself, and deleting that passes: what is
        # left reads as new.
        assert workspace in target.parents, f"{one!r} is not a path in the workspace"
        if one.startswith("everything in "):
            for child in target.iterdir():
                shutil.rmtree(child)
        else:
            shutil.rmtree(target)

    assert ensure_layout(workspace) == workspace.resolve()
    assert f"layout {LAYOUT_VERSION}" in (workspace / MARKER).read_text(encoding="utf-8")
    assert not is_new_workspace(workspace)


def test_a_workspace_this_version_laid_out_is_accepted(tmp_path):
    """So the refusal above is not passing because every workspace is refused."""
    workspace = ensure_layout(tmp_path / "new")

    assert f"layout {LAYOUT_VERSION}" in (workspace / MARKER).read_text(encoding="utf-8")
    assert ensure_layout(workspace) == workspace


def test_an_empty_directory_is_not_an_old_workspace(tmp_path):
    """A path with no marker has no version to disagree with, and `is_new_workspace`
    already treats it as never used."""
    assert ensure_layout(tmp_path / "fresh").is_dir()

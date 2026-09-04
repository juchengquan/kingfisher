"""What a turn produced, reported so a reaped session does not take it away."""

from __future__ import annotations

from kingfisher.application.run import Request, run
from kingfisher.infrastructure.workspace.sessions import collect_artifacts
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent


def test_what_the_turn_left_in_derived_comes_back(cfg):
    """The session is reapable, so anything not reported is lost."""
    start(cfg, "s")
    result = run(Request("t", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                 checkpointer=StubCheckpointer())
    session = cfg.workspace / "sessions" / "s"
    (session / "derived" / "model.pkl").write_bytes(b"fitted")

    again = run(Request("t2", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                checkpointer=StubCheckpointer())

    assert "derived/model.pkl" in again.artifacts
    assert "derived/model.pkl" not in result.artifacts  # not there on the first turn


def test_memory_is_reported_too(cfg):
    """It is scaffolded at session creation, so it is there from turn one."""
    start(cfg, "s")
    result = run(Request("t", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                 checkpointer=StubCheckpointer())

    assert "memory/AGENTS.md" in result.artifacts


def test_run_scratch_is_not_reported(cfg):
    """`/runs` is disposable by design, and the prompt tells the agent so."""
    start(cfg, "s")
    result = run(Request("t", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                 checkpointer=StubCheckpointer())
    (result.run_dir / "scratch.txt").write_text("intermediate")

    again = run(Request("t2", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                checkpointer=StubCheckpointer())

    assert not any("runs/" in path for path in again.artifacts)


def test_inputs_are_not_reported(cfg):
    """`/data` came from the caller and is read-only; handing it back would be
    asking them to store what they already have."""
    start(cfg, "s")
    session = cfg.workspace / "sessions" / "s"
    result = run(Request("t", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                 checkpointer=StubCheckpointer())

    assert session.is_dir()
    assert not any(path.startswith("data/") for path in result.artifacts)


def test_paths_are_relative_to_the_session(cfg, session_dir):
    """A host path would be useless to a caller that does not share the disk."""
    (session_dir / "derived" / "nested").mkdir(parents=True)
    (session_dir / "derived" / "nested" / "out.csv").write_text("a,b\n")

    artifacts = collect_artifacts(session_dir)

    assert "derived/nested/out.csv" in artifacts
    assert not any(path.startswith("/") for path in artifacts)


def test_a_shell_write_is_reported_even_though_no_tool_saw_it(session_dir):
    """The reason this is a filesystem walk and not a record of tool calls:
    `execute` bypasses the file tools, and running a script is how most of
    `/derived` gets produced."""
    import subprocess

    subprocess.run(
        ["sh", "-c", "echo fitted > derived/model.txt"],
        cwd=session_dir,
        check=True,
    )

    assert "derived/model.txt" in collect_artifacts(session_dir)


def test_directories_are_omitted(session_dir):
    """An empty one carries nothing to persist and reappears with its files."""
    (session_dir / "derived" / "empty").mkdir(parents=True)

    assert "derived/empty" not in collect_artifacts(session_dir)

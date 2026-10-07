"""What a turn produced, reported so a reaped session does not take it away."""

from __future__ import annotations

from kingfisher import backend_at, default_backends
from kingfisher.application.run import Request, run
from kingfisher.infrastructure.session_files import collect_artifacts
from kingfisher.infrastructure.steps import drive
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent


def test_what_the_turn_left_in_derived_comes_back(cfg):
    """The session is reapable, so anything not reported is lost."""
    start(cfg, "s")
    result = run(
        Request("t", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )
    session = cfg.workspace / "sessions" / "s"
    (session / "outputs" / "model.pkl").write_bytes(b"fitted")

    again = run(
        Request("t2", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )

    assert "outputs/model.pkl" in again.artifacts
    assert "outputs/model.pkl" not in result.artifacts  # not there on the first turn


def test_memory_is_reported_too(cfg):
    """It is scaffolded at session creation, so it is there from turn one."""
    start(cfg, "s")
    result = run(
        Request("t", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )

    assert "memory/AGENTS.md" in result.artifacts


def test_run_scratch_is_not_reported(cfg):
    """`/scratchpad` is disposable by design, and the prompt tells the agent so."""
    start(cfg, "s")
    result = run(
        Request("t", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )
    session = cfg.workspace / "sessions" / result.session_id
    (session / "scratchpad" / "scratch.txt").write_text("intermediate")

    again = run(
        Request("t2", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )

    assert not any(path.startswith("scratchpad/") for path in again.artifacts)


def test_inputs_are_not_reported(cfg):
    """`/inputs` came from the caller and is read-only; handing it back would be asking
    them to store what they already have.
    """
    start(cfg, "s")
    session = cfg.workspace / "sessions" / "s"
    result = run(
        Request("t", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        backends=default_backends,
        checkpointer=StubCheckpointer(),
    )

    assert session.is_dir()
    assert not any(path.startswith("inputs/") for path in result.artifacts)


def test_paths_are_relative_to_the_session(cfg, session_dir):
    """A host path would be useless to a caller that does not share the disk."""
    (session_dir / "outputs" / "nested").mkdir(parents=True)
    (session_dir / "outputs" / "nested" / "out.csv").write_text("a,b\n")

    artifacts = drive(collect_artifacts(backend_at(cfg, session_dir)))

    assert "outputs/nested/out.csv" in artifacts
    assert not any(path.startswith("/") for path in artifacts)


def test_a_shell_write_is_reported_even_though_no_tool_saw_it(cfg, session_dir):
    """The reason this is a filesystem walk and not a record of tool calls: `execute`
    bypasses the file tools, and running a script is how most of `/outputs` gets
    produced.
    """
    import subprocess

    subprocess.run(
        ["sh", "-c", "echo fitted > outputs/model.txt"],
        cwd=session_dir,
        check=True,
    )

    assert "outputs/model.txt" in drive(collect_artifacts(backend_at(cfg, session_dir)))


def test_directories_are_omitted(cfg, session_dir):
    """An empty one carries nothing to persist and reappears with its files."""
    (session_dir / "outputs" / "empty").mkdir(parents=True)

    assert "outputs/empty" not in drive(collect_artifacts(backend_at(cfg, session_dir)))

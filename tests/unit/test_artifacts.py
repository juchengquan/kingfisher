"""What a turn produced, reported so a reaped session does not take it away."""

from __future__ import annotations

from dataclasses import replace

import pytest

from kingfisher import LocalSessions, backend_at
from kingfisher.core.result import ArtifactError
from kingfisher.core.run import Request, run
from kingfisher.core.session.files import collect_artifacts, read_artifact
from kingfisher.core.session.steps import drive
from kingfisher.layout import Layout
from tests.conftest import StubCheckpointer, start
from tests.unit.test_run import StubAgent

#: A deployment that renamed every leaf it may rename. The rules below pair it with
#: the default, because what went wrong was not the renamed path being wrong -- it was
#: the default path being the only one anything exercised.
MOVED = Layout(inputs="in", outputs="out", memory="mem", scratchpad="scratch")


@pytest.mark.parametrize("layout", [Layout(), MOVED], ids=["default", "renamed"])
def test_what_a_turn_produced_comes_back_under_the_layout_it_was_produced_in(
    layout, cfg, tmp_path
):
    """`collect_artifacts` walked the module constants, so a deployment that renamed
    its outputs folder was handed `()` -- measured, with the turn's own report sitting
    in the session and the session reapable, so what is not named here is lost. The
    caller is the one edge of this module a renamed layout reaches, and it was the one
    edge the rename was not threaded through.

    Paired with the default rather than driven on the rename alone: every rule that
    covered this ran the default, which is why none of them could see it.
    """
    cfg = replace(cfg, layout=layout)
    start(cfg, "s")
    run(Request("t", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
        sessions=LocalSessions(), checkpointer=StubCheckpointer())
    session = cfg.workspace / "sessions" / "s"
    (session / layout.outputs / "report.md").write_text("the answer\n", encoding="utf-8")

    again = run(Request("t2", session_id="s"), cfg=cfg, graph=StubAgent("ok"),
                sessions=LocalSessions(), checkpointer=StubCheckpointer())

    assert f"{layout.outputs}/report.md" in again.artifacts
    assert f"{layout.memory}/AGENTS.md" in again.artifacts
    # And fetchable by the name it was handed under, which is the other half: a name
    # the caller is given that the fetch then refuses is a result nobody can open.
    got = drive(read_artifact(backend_at(cfg, session), f"{layout.outputs}/report.md", layout))
    assert got == b"the answer\n"


def test_a_folder_this_deployment_did_not_name_is_not_an_artifact(cfg):
    """The bound on the rule above. Under a renamed layout the *default* spellings are
    just folders in the session, and handing one over would reach past what this
    deployment calls an artifact.
    """
    cfg = replace(cfg, layout=MOVED)
    start(cfg, "s")
    session = cfg.workspace / "sessions" / "s"
    backend = backend_at(cfg, session)

    with pytest.raises(ArtifactError, match="name a file under out, mem"):
        drive(read_artifact(backend, "outputs/report.md", MOVED))


def test_what_the_turn_left_in_derived_comes_back(cfg):
    """The session is reapable, so anything not reported is lost."""
    start(cfg, "s")
    result = run(
        Request("t", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        sessions=LocalSessions(),
        checkpointer=StubCheckpointer(),
    )
    session = cfg.workspace / "sessions" / "s"
    (session / "outputs" / "model.pkl").write_bytes(b"fitted")

    again = run(
        Request("t2", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        sessions=LocalSessions(),
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
        sessions=LocalSessions(),
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
        sessions=LocalSessions(),
        checkpointer=StubCheckpointer(),
    )
    session = cfg.workspace / "sessions" / result.session_id
    (session / "scratchpad" / "scratch.txt").write_text("intermediate")

    again = run(
        Request("t2", session_id="s"),
        cfg=cfg,
        graph=StubAgent("ok"),
        sessions=LocalSessions(),
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
        sessions=LocalSessions(),
        checkpointer=StubCheckpointer(),
    )

    assert session.is_dir()
    assert not any(path.startswith("inputs/") for path in result.artifacts)


def test_paths_are_relative_to_the_session(cfg, session_dir):
    """A host path would be useless to a caller that does not share the disk."""
    (session_dir / "outputs" / "nested").mkdir(parents=True)
    (session_dir / "outputs" / "nested" / "out.csv").write_text("a,b\n")

    artifacts = drive(collect_artifacts(backend_at(cfg, session_dir), cfg.layout))

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

    assert "outputs/model.txt" in drive(collect_artifacts(backend_at(cfg, session_dir), cfg.layout))


def test_directories_are_omitted(cfg, session_dir):
    """An empty one carries nothing to persist and reappears with its files."""
    (session_dir / "outputs" / "empty").mkdir(parents=True)

    assert "outputs/empty" not in drive(collect_artifacts(backend_at(cfg, session_dir), cfg.layout))

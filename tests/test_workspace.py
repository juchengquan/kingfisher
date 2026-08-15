from __future__ import annotations

import os

from tests.conftest import StubCheckpointer

from kingfisher.workspace import (
    LAYOUT_DIRS,
    ensure_layout,
    is_repo,
    pre_run_commit,
    sweep,
)


def test_layout_is_created_and_idempotent(tmp_path):
    ws = ensure_layout(tmp_path / "ws")
    ensure_layout(ws)
    for name in LAYOUT_DIRS:
        assert (ws / name).is_dir()
    assert (ws / ".gitignore").exists()
    assert (ws / ".kingfisher" / "WORKSPACE").exists()


def test_gitignore_encodes_the_durability_tiers(workspace):
    text = (workspace / ".gitignore").read_text()
    # Ignored wholesale: irreplaceable inputs, expensive derived data, harness state.
    assert "data/" in text
    assert "derived/" in text
    assert ".kingfisher/" in text
    # Run output: conclusions tracked, scratch ignored. Negation needs the
    # parent directories re-included or git will not reach the files.
    assert "runs/**" in text
    # Two levels — runs/<session>/<turn>/ — and every parent must be
    # re-included or git never descends far enough to see the negated files.
    assert "!runs/*/" in text
    assert "!runs/*/*/" in text
    assert "!runs/*/*/report.md" in text
    assert "!runs/*/*/result.json" in text


def test_sweep_keeps_the_newest_and_deletes_thread_with_directory(workspace):
    """A swept session loses its directory and its thread together, so the
    checkpointer can never point at files that no longer exist."""
    for i, name in enumerate(["oldest", "middle", "newest"]):
        d = workspace / "runs" / name
        d.mkdir(parents=True)
        os.utime(d, (1_000 + i * 100, 1_000 + i * 100))

    ckpt = StubCheckpointer()
    result = sweep(workspace, keep=2, checkpointer=ckpt)

    assert result.removed == ("oldest",)
    assert not (workspace / "runs" / "oldest").exists()
    assert (workspace / "runs" / "newest").exists()
    assert ckpt.deleted == ["oldest"]


def test_sweep_is_a_noop_when_under_the_limit(workspace):
    (workspace / "runs" / "only").mkdir(parents=True)
    result = sweep(workspace, keep=5, checkpointer=StubCheckpointer())
    assert result.removed == ()


def test_pre_run_commit_stages_only_the_tracked_tier(workspace):
    """`git add -A` would sweep up unrelated work if the workspace is shared."""
    (workspace / "reports" / "smoke.md").write_text("findings\n")
    stray = workspace / "unrelated.txt"
    stray.write_text("not kingfisher's business\n")

    os.environ.setdefault("GIT_AUTHOR_NAME", "kingfisher-test")
    os.environ.setdefault("GIT_AUTHOR_EMAIL", "test@example.invalid")
    os.environ.setdefault("GIT_COMMITTER_NAME", "kingfisher-test")
    os.environ.setdefault("GIT_COMMITTER_EMAIL", "test@example.invalid")

    sha = pre_run_commit(workspace, "kingfisher: pre-run test")

    assert is_repo(workspace)
    if sha is None:  # git unavailable in this environment
        return
    import subprocess

    listed = subprocess.run(  # noqa: S603
        ["git", "-C", str(workspace), "ls-files"],
        capture_output=True,
        text=True,
        check=False,
    ).stdout
    assert "reports/smoke.md" in listed
    assert "unrelated.txt" not in listed


def test_caller_supplied_turn_id_wins_and_is_idempotent(workspace):
    """A service passes its own request id, so a retry reuses the same turn
    rather than forking a second one."""
    from kingfisher.workspace import allocate_turn_dir

    first_id, first = allocate_turn_dir(workspace, "sess", "req-abc")
    again_id, again = allocate_turn_dir(workspace, "sess", "req-abc")

    assert first_id == again_id == "req-abc"
    assert first == again
    assert first.is_dir()


def test_concurrent_allocation_never_collides(workspace):
    """Scanning for the highest id and then creating it is a race. Allocation
    goes through mkdir, which fails if the name is taken, so two callers
    cannot both decide they are t001."""
    import concurrent.futures as futures

    from kingfisher.workspace import allocate_turn_dir

    with futures.ThreadPoolExecutor(max_workers=16) as pool:
        results = list(
            pool.map(lambda _: allocate_turn_dir(workspace, "busy")[0], range(40))
        )

    assert len(set(results)) == 40, f"collision: {len(results) - len(set(results))} duplicates"
    assert all(r.startswith("t") for r in results)


def test_allocated_ids_are_sequential_and_readable(workspace):
    from kingfisher.workspace import allocate_turn_dir

    ids = [allocate_turn_dir(workspace, "ordered")[0] for _ in range(3)]
    assert ids == ["t001", "t002", "t003"]

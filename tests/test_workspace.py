from __future__ import annotations

import os
import shutil

from kingfisher.adapters.workspace_fs import LocalSessionDirs, ensure_layout
from kingfisher.adapters.workspace_git import is_repo, pre_run_commit
from kingfisher.domain import retention
from kingfisher.domain.layout import (
    LAYOUT_DIRS,
    SESSION_DIRS,
    TRACKED_PATHS,
    WORKSPACE_GITIGNORE,
)
from tests.conftest import StubCheckpointer


def test_layout_is_created_and_idempotent(tmp_path):
    ws = ensure_layout(tmp_path / "ws")
    ensure_layout(ws)
    for name in LAYOUT_DIRS:
        assert (ws / name).is_dir()
    assert (ws / ".gitignore").exists()
    assert (ws / ".kingfisher" / "WORKSPACE").exists()


def test_gitignore_encodes_the_durability_tiers(workspace):
    text = (workspace / ".gitignore").read_text()
    # Harness state is local by design.
    assert ".kingfisher/" in text
    # Everything a session owns -- inputs, derived output, memory, run scratch
    # -- is ignored wholesale. Nothing under sessions/ is re-included: what a
    # run produces and wants kept leaves through the result, not through git.
    assert "sessions/" in text
    assert "!sessions" not in text


def sweep(workspace, keep, checkpointer):
    """What `stream()` does: list, plan, apply. A helper here because the tests
    below are about the outcome, not the three steps."""
    dirs = LocalSessionDirs()
    runs = workspace / "runs"
    return retention.apply(
        retention.plan(dirs.listing(runs), keep), runs, dirs, checkpointer
    )


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
    # Skills are authored by a person and shared by every session, which is
    # what the tracked tier is now for. Memory moved into the session.
    (workspace / "skills" / "tabular-qa").mkdir(parents=True, exist_ok=True)
    (workspace / "skills" / "tabular-qa" / "SKILL.md").write_text("---\nname: x\n---\nbody\n")
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
    assert "skills/tabular-qa/SKILL.md" in listed
    assert "unrelated.txt" not in listed


class BrokenCheckpointer:
    """A checkpointer whose thread deletion fails."""

    def delete_thread(self, thread_id: str) -> None:
        msg = f"cannot delete {thread_id}"
        raise RuntimeError(msg)


def test_sweep_deletes_the_thread_before_the_directory(workspace):
    """No transaction spans a filesystem and sqlite, so the order is chosen to
    make the surviving failure benign."""
    order: list[str] = []

    class Recording:
        def delete_thread(self, thread_id: str) -> None:
            order.append("thread")

    d = workspace / "runs" / "old"
    d.mkdir(parents=True)
    (d / "scratch.txt").write_text("x")

    original = shutil.rmtree

    def watched(*args, **kwargs):
        order.append("directory")
        return original(*args, **kwargs)

    shutil.rmtree = watched
    try:
        sweep(workspace, keep=0, checkpointer=Recording())
    finally:
        shutil.rmtree = original

    assert order == ["thread", "directory"]


def test_a_failed_thread_delete_leaves_the_session_whole(workspace):
    """Nothing is half-deleted: if the thread will not go, the directory stays,
    so the next sweep retries an intact session rather than finding a thread
    that points at files which are gone."""
    d = workspace / "runs" / "old"
    d.mkdir(parents=True)

    result = sweep(workspace, keep=0, checkpointer=BrokenCheckpointer())

    assert result.removed == ()
    assert d.is_dir(), "the directory was removed despite the thread surviving"
    assert len(result.failures) == 1
    assert "thread not deleted" in result.failures[0]


def test_sweep_failures_are_reported_not_swallowed(workspace):
    """A checkpointer that can never delete should be visible, not tolerated
    silently on every single run."""
    for name in ("a", "b"):
        (workspace / "runs" / name).mkdir(parents=True)

    result = sweep(workspace, keep=0, checkpointer=BrokenCheckpointer())

    assert len(result.failures) == 2
    # Each failure names the session it belongs to, so the report is actionable.
    assert {f.split(":")[0] for f in result.failures} == {"a", "b"}
    assert all("RuntimeError" in f for f in result.failures)


def test_the_layout_names_no_genre_of_output():
    """`/reports` privileged one kind of result in the workspace structure
    itself. Durability is the only thing the layout should encode: `/derived`
    survives, `runs/` does not, and what you call the file is your business."""
    assert "reports" not in LAYOUT_DIRS
    assert "reports" not in SESSION_DIRS
    assert "derived" in SESSION_DIRS

    # And nothing is tracked for being a "report" either.
    assert not any("report" in path for path in TRACKED_PATHS)
    assert "report" not in WORKSPACE_GITIGNORE


def test_git_tracks_what_a_person_authored_not_what_a_run_produced(workspace):
    """The tiers are now clean: authored content is restorable from git,
    produced content lives in derived/ and is never swept, run scratch is
    disposable. Nothing straddles two tiers."""
    assert set(TRACKED_PATHS) == {".gitignore", "PROMPT.md", "skills", "subagents"}
    # Everything a run produces now lives inside a session, and sessions are
    # ignored wholesale -- so there is one line to check rather than three.
    assert "sessions/" in WORKSPACE_GITIGNORE
    assert not any(name in TRACKED_PATHS for name in SESSION_DIRS)
